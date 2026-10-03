"""Integration tests for POST /bills/upload with the LLM stubbed out."""

import pytest

from app import llm
from app.extraction import parse_extraction

FAKE_JSON = """Here is the extraction:
```json
{
  "vendor_name": "Bhatbhateni Supermarket",
  "vendor_pan": "601234567",
  "bill_number": "BB-88991",
  "bill_date_bs": "2083.05.02",
  "bill_date_ad": null,
  "subtotal": "6895.00",
  "discount": null,
  "taxable_amount": "6895.00",
  "vat_amount": "896.35",
  "total_amount": "7791.35",
  "items": [{"description": "Rice 25kg", "quantity": "1", "unit_price": null,
             "amount": "6895.00"}, {"bad": "row"}],
  "ocr_text": "Bhatbhateni Supermarket\\nBB-88991\\nTotal 7791.35"
}
```
"""


def _fake_llm_factory(payload: str):
    async def fake_post(session, purpose, user_id, path, body):
        assert purpose == "extraction"
        await llm._log(session, purpose, body.get("model"), "gen-1", 500, 100, 10,
                       None, None, "ok", 200, None, 1)
        return {"choices": [{"message": {"content": payload}}]}

    return fake_post


@pytest.fixture
def fake_llm(monkeypatch):
    calls: list[dict] = []

    async def fake_post(session, purpose, user_id, path, body):
        calls.append(body)
        return await _fake_llm_factory(FAKE_JSON)(session, purpose, user_id, path, body)

    monkeypatch.setattr(llm, "_post", fake_post)
    return calls


async def test_parse_extraction_strips_fences():
    out = parse_extraction(FAKE_JSON)
    assert out["vendor_name"] == "Bhatbhateni Supermarket"
    assert out["vendor_pan"] == "601234567"
    assert out["bill_date_bs"] == "2083.05.02"
    assert len(out["items"]) == 1  # the {"bad": "row"} entry is dropped
    assert out["ocr_text"].startswith("Bhatbhateni")


async def test_parse_extraction_rejects_no_json():
    with pytest.raises(llm.LlmError):
        parse_extraction("sorry, I cannot extract that bill")


async def test_upload_creates_bill_and_extraction(client, auth, fake_llm, db):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
        "53de0000000c4944415408d763a8c3f00f000301010018dd8db00000000049454e44ae426082"
    )
    r = await client.post("/bills/upload", headers=auth,
                          files={"file": ("bill.png", png, "image/png")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["vendor_name"] == "Bhatbhateni Supermarket"
    assert body["vendor_pan"] == "601234567"
    # BS date 2083.05.02 normalised to AD + raw kept
    assert body["bill_date_ad"] == "2026-08-18"
    assert body["bill_date_bs_raw"] == "2083.05.02"
    assert body["status"] == "extracted"
    assert [i["description"] for i in body["items"]] == ["Rice 25kg"]

    # idempotent resubmit: same bytes -> same bill, no second LLM call
    r2 = await client.post("/bills/upload", headers=auth,
                           files={"file": ("again.png", png, "image/png")})
    assert r2.status_code == 201
    assert r2.json()["id"] == body["id"]
    assert len(fake_llm) == 1


async def test_upload_rejects_bad_mime_and_oversize(client, auth, fake_llm):
    r = await client.post("/bills/upload", headers=auth,
                          files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415
    r = await client.post("/bills/upload", headers=auth,
                          files={"file": ("big.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")})
    assert r.status_code == 413


async def test_upload_llm_failure_maps_to_502(client, auth, monkeypatch):
    async def fail_post(session, purpose, user_id, path, body):
        raise llm.LlmError("OpenRouter http 500", http_status=500)

    monkeypatch.setattr(llm, "_post", fail_post)
    png = bytes(32)
    r = await client.post("/bills/upload", headers=auth,
                          files={"file": ("bill.png", png, "image/png")})
    assert r.status_code == 502
    assert "Extraction failed" in r.json()["detail"]


async def test_extraction_persisted_for_bench(client, auth, fake_llm, db):
    from sqlalchemy import select

    from app.models import Extraction
    png = bytes(32)
    body = (await client.post("/bills/upload", headers=auth,
                              files={"file": ("bill.png", png, "image/png")})).json()
    row = (await db.execute(
        select(Extraction).where(Extraction.bill_id == body["id"]))).scalar_one()
    assert row.extractor == "llm_vision"
    assert row.structured["total_amount"] == "7791.35"
    assert "Bhatbhateni" in row.ocr_text
