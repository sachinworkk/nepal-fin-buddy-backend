from app.rag import chunk_text


def test_empty_and_whitespace():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_single_chunk():
    assert chunk_text("hello world") == ["hello world"]


def test_long_text_chunks_overlap():
    text = " ".join(f"word{i}" for i in range(500))
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) > 1
    # step = size - overlap; consecutive chunks share the `overlap`-char window
    step = 200 - 50
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert b[:50] == a[step:step + 50]


def test_no_tiny_trailing_chunk():
    text = "a" * 210
    chunks = chunk_text(text, size=200, overlap=50)
    assert all(len(c) >= 40 for c in chunks)


def test_whitespace_normalised():
    assert chunk_text("hello \n\n  world \t twice") == ["hello world twice"]