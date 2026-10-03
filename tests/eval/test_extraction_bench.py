"""Extraction benchmark: run the real extractor over labelled bill images and
score field-by-field with tests/eval/extraction_scorer.py (no judge LLM).

STATUS: the extractor (step 5, POST /bills/upload) does not exist in the app
yet, so this suite skips with a clear message until it lands. Once
app/extraction.py (or equivalent) is built, implement run_extractor() below by
calling it; nothing else needs to change.

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


def run_extractor(image_path: Path, mime_type: str) -> dict:
    """Call the app's real bill extraction and return the extracted dict.

    TODO(step 5): replace with the real call, e.g.:
        from app.extraction import extract_bill  # when it exists
        return extract_bill(image_path.read_bytes(), mime_type)
    """
    raise NotImplementedError("extractor not built yet (step 5)")


@pytest.fixture(scope="module")
def labelled_bills():
    bills = load_labels()
    real = []
    for b in bills:
        img = FIXTURES / "bills" / b["image"]
        if img.exists():
            real.append((b, img))
        else:
            pytest.skip(f"fixture image missing: {img.name}", allow_module_level=False)
    return real


@pytest.fixture(scope="module")
def runs(labelled_bills):
    s = get_settings()
    if not s.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY not set", allow_module_level=True)
    out = []
    for labels, img in labelled_bills:
        mime = "application/pdf" if img.suffix == ".pdf" else "image/jpeg"
        try:
            actual = run_extractor(img, mime)
        except NotImplementedError:
            pytest.skip("extractor not built yet (step 5)", allow_module_level=True)
        out.append({"bill_id": labels["id"],
                    **score_extraction(labels["expected"], actual)})
    return out


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