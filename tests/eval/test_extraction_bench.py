"""Extraction benchmark: run the real extractor over labelled bill images and
score field-by-field with tests/eval/extraction_scorer.py (no judge LLM).

Scores header fields, amounts, payment_method and line items against the
hand-verified labels. All bills are extracted in a single event loop.

Run with:  uv run pytest tests/eval/test_extraction_bench.py -m live_llm -q
Fixtures:  tests/eval/fixtures/labels.json + images in fixtures/bills/.
"""

import json
from pathlib import Path

import pytest

from app.config import get_settings
from tests.eval.extraction_scorer import accuracy_by_field, format_report, score_extraction

pytestmark = pytest.mark.live_llm

FIXTURES = Path(__file__).parent / "fixtures"


def load_labels() -> list[dict]:
    return json.loads((FIXTURES / "labels.json").read_text())["bills"]


def run_extractors(images: list[tuple[Path, str]]) -> list[dict]:
    """Call the app's real bill extraction for each (path, mime) and return the dicts."""
    import asyncio

    from app.db import SessionLocal, engine
    from app.extraction import extract_bill

    async def run() -> list[dict]:
        out = []
        try:
            for path, mime in images:
                async with SessionLocal() as session:
                    out.append(await extract_bill(session, None, path.read_bytes(), mime))
        finally:
            await engine.dispose()
        return out

    return asyncio.run(run())


@pytest.fixture(scope="module")
def labelled_bills():
    bills = load_labels()
    paths = [(b, FIXTURES / "bills" / b["image"]) for b in bills]
    missing = [p.name for _, p in paths if not p.exists()]
    assert not missing, f"labels.json references missing images: {missing}"
    return paths


@pytest.fixture(scope="module")
def runs(labelled_bills):
    s = get_settings()
    if not s.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY not set", allow_module_level=True)
    mimes = {".pdf": "application/pdf", ".png": "image/png"}
    extracted = run_extractors([(img, mimes.get(img.suffix, "image/jpeg"))
                                for _, img in labelled_bills])
    return [{"bill_id": labels["id"], **score_extraction(labels["expected"], got)}
            for (labels, _), got in zip(labelled_bills, extracted, strict=True)]


@pytest.mark.live_llm
def test_per_bill_accuracy(runs):
    """Each bill must score at least 70% of fields correct."""
    for run in runs:
        assert run["accuracy"] >= 0.7, (
            f"{run['bill_id']}: {run['correct']}/{run['total']} fields correct\n"
            f"{json.dumps({f: r for f, r in run['fields'].items() if r['status'] != 'correct'}, indent=2)}"
        )


@pytest.mark.live_llm
def test_overall_accuracy(runs):
    """Whole-suite field accuracy must be at least 80%."""
    total = sum(r["total"] for r in runs)
    correct = sum(r["correct"] for r in runs)
    assert correct / total >= 0.8, f"{correct}/{total} = {correct / total:.1%}"


@pytest.mark.live_llm
def test_report(runs):
    """Always prints the per-field table — the number that drives decisions."""
    report = format_report(accuracy_by_field(runs))
    print("\n" + report)
    assert report