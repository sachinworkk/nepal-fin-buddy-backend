"""Offline sanity checks for the RAG golden set (no LLM, no DB).

Guards the answer key: every number in an expected answer must literally appear in
the corpus, and every must_retrieve title must exist.
"""

import re

from tests.eval.golden import golden_cases, seed_documents

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _corpus_text() -> str:
    return " ".join(text for _, _, text in seed_documents()).translate(_DEVANAGARI_DIGITS)


def test_case_ids_are_unique():
    ids = [c["id"] for c in golden_cases()]
    assert len(ids) == len(set(ids))


def test_must_retrieve_titles_exist():
    titles = {title for _, title, _ in seed_documents()}
    for case in golden_cases():
        if case["must_retrieve"]:
            assert case["must_retrieve"] in titles, case["id"]


def test_expected_numbers_appear_in_corpus():
    corpus = _corpus_text()
    for case in golden_cases():
        if not case["must_retrieve"]:
            continue
        for number in _NUMBER.findall(case["expected_answer_hint"]):
            assert number in corpus, f"{case['id']}: '{number}' not found in corpus"


def test_expected_document_contains_the_numbers():
    docs = {title: text.translate(_DEVANAGARI_DIGITS) for _, title, text in seed_documents()}
    for case in golden_cases():
        if not case["must_retrieve"]:
            continue
        text = docs[case["must_retrieve"]]
        for number in _NUMBER.findall(case["expected_answer_hint"]):
            assert number in text, (
                f"{case['id']}: '{number}' is not in '{case['must_retrieve']}'"
            )
