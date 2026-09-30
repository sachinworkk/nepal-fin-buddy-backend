import io

from pypdf import PdfWriter


def blank_pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


async def test_note_crud_and_list_omits_text(client, auth):
    r = await client.post("/documents", headers=auth, json={"title": "Rent", "text": "Paid 20000"})
    assert r.status_code == 201 and r.json()["kind"] == "note"
    did = r.json()["id"]
    listed = (await client.get("/documents", headers=auth)).json()
    assert len(listed) == 1 and "text_content" not in listed[0]
    assert (await client.get(f"/documents/{did}", headers=auth)).json()["text_content"] == "Paid 20000"
    assert (await client.delete(f"/documents/{did}", headers=auth)).status_code == 204
    assert (await client.get(f"/documents/{did}", headers=auth)).status_code == 404


async def test_upload_text_and_pdf(client, auth):
    r = await client.post("/documents/upload", headers=auth,
                          files={"file": ("n.txt", b"hello \xe0\xa4\xa8\xe0\xa5\x87\xe0\xa4\xaa\xe0\xa4\xbe\xe0\xa4\xb2", "text/plain")})
    assert r.status_code == 201 and r.json()["text_content"].startswith("hello")
    r = await client.post("/documents/upload", headers=auth,
                          files={"file": ("s.pdf", blank_pdf(), "application/pdf")})
    assert r.status_code == 201 and r.json()["kind"] == "pdf"


async def test_upload_rejections(client, auth):
    r = await client.post("/documents/upload", headers=auth,
                          files={"file": ("a.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415
    r = await client.post("/documents/upload", headers=auth,
                          files={"file": ("bad.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 422


async def test_document_isolation(client, auth, other_auth):
    did = (await client.post("/documents", headers=auth, json={"title": "t", "text": "x"})).json()["id"]
    assert (await client.get(f"/documents/{did}", headers=other_auth)).status_code == 404
    assert (await client.delete(f"/documents/{did}", headers=other_auth)).status_code == 404
    assert (await client.get("/documents", headers=other_auth)).json() == []
