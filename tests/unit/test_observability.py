import pytest

from app import observability
from app.config import get_settings


@pytest.fixture
def mlflow_on(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path}/mlflow.db")
    get_settings.cache_clear()
    observability.init_mlflow()
    yield
    monkeypatch.setenv("MLFLOW_ENABLED", "false")
    get_settings.cache_clear()
    observability.init_mlflow()


def test_disabled_is_noop():
    observability.init_mlflow()
    with observability.trace_span("x", a=1) as span:
        span.set(b=2)


def test_records_span_with_scalar_attrs_only(mlflow_on):
    import mlflow

    with observability.trace_span("unit.test", tokens=5) as span:
        span.set(model="m", nested={"prompt": "secret"})
    mlflow.flush_trace_async_logging()
    trace = mlflow.get_trace(mlflow.get_last_active_trace_id())
    attrs = trace.data.spans[0].attributes
    assert attrs["tokens"] == 5 and attrs["model"] == "m"
    assert "nested" not in attrs


def test_mlflow_failure_is_swallowed(mlflow_on, monkeypatch):
    import mlflow

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(mlflow, "start_span", boom)
    with observability.trace_span("x") as span:
        span.set(a=1)


def test_body_exception_propagates(mlflow_on):
    with pytest.raises(ValueError):
        with observability.trace_span("x"):
            raise ValueError("boom")


def test_io_only_recorded_when_content_logging_on(mlflow_on, monkeypatch):
    import mlflow

    def last_inputs():
        mlflow.flush_trace_async_logging()
        return mlflow.get_trace(mlflow.get_last_active_trace_id()).data.spans[0].inputs

    monkeypatch.setenv("MLFLOW_LOG_CONTENT", "false")
    get_settings.cache_clear()
    with observability.trace_span("io.off") as span:
        span.io(inputs={"query": "hi"})
    assert not last_inputs()

    monkeypatch.setenv("MLFLOW_LOG_CONTENT", "true")
    get_settings.cache_clear()
    with observability.trace_span("io.on") as span:
        span.io(inputs={"query": "hi"}, outputs={"answer": "yo"})
    assert last_inputs() == {"query": "hi"}
