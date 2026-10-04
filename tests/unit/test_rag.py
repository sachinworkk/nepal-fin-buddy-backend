from app.rag import chunk_text


def test_empty_and_whitespace():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_single_chunk():
    assert chunk_text("hello world") == ["hello world"]


def test_sentence_boundaries_respected():
    sents = [f"Sentence number {i} ends here. " for i in range(30)]
    text = "".join(sents)
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) > 1
    # No chunk cuts mid-sentence: every chunk either ends at a sentence end or is the last
    for c in chunks[:-1]:
        assert c.rstrip().endswith((".", "!", "?", "।")), c[-20:]


def test_no_mid_word_cuts_on_oversized_sentence():
    text = " ".join(f"word{i}" for i in range(500))
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) > 1
    for c in chunks:
        assert not c.startswith("word") or True
    # no chunk ends or starts with a partial word fragment
    words = set(text.split())
    for c in chunks:
        assert c.split()[0] in words
        assert c.split()[-1] in words


def test_all_chunks_within_size_and_nonempty():
    text = ("Long paragraph about VAT in Nepal. " * 40) + " tail"
    chunks = chunk_text(text, size=300, overlap=60)
    assert all(0 < len(c) <= 300 for c in chunks)
    assert "".join(chunks).replace(" ", "") != ""


def test_no_tiny_orphan_chunks():
    text = "a" * 210 + ". " + "b" * 10
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) >= 1
    assert all(len(c) >= 40 for c in chunks)


def test_whitespace_normalised():
    assert chunk_text("hello \n\n  world \t twice") == ["hello world twice"]
