"""MLflow tracing, kept behind one thin wrapper.

Only metadata goes in here (model, tokens, latency, counts, scores). Prompts, answers and
queries contain users' financial data and must never be passed to `trace_span` or `set`.
The one exception is `io`, which records the chat query/answer and only acts when
MLFLOW_LOG_CONTENT is on.
Every MLflow failure is swallowed: monitoring must not break a request.
"""

from contextlib import contextmanager

import structlog

from app.config import get_settings

log = structlog.get_logger()

_ready = False


class _Handle:
    """What `trace_span` yields. `set` is a no-op when tracing is off or MLflow misbehaves."""

    def __init__(self, span=None):
        self._span = span

    def set(self, **attrs) -> None:
        if self._span is None:
            return
        clean = {k: v for k, v in attrs.items()
                 if isinstance(v, (str, int, float, bool)) or v is None}
        try:
            self._span.set_attributes(clean)
        except Exception as e:
            log.warning("mlflow.set_failed", error=str(e))


    def io(self, inputs=None, outputs=None) -> None:
        """Record span inputs/outputs (user text). No-op unless MLFLOW_LOG_CONTENT is on."""
        if self._span is None or not get_settings().mlflow_log_content:
            return
        try:
            if inputs is not None:
                self._span.set_inputs(inputs)
            if outputs is not None:
                self._span.set_outputs(outputs)
        except Exception as e:
            log.warning("mlflow.set_failed", error=str(e))


def init_mlflow() -> None:
    """Point MLflow at the configured tracking server. Safe to call when disabled."""
    global _ready
    s = get_settings()
    _ready = False
    if not s.mlflow_enabled:
        return
    try:
        import mlflow

        if s.mlflow_tracking_uri:
            mlflow.set_tracking_uri(s.mlflow_tracking_uri)
        mlflow.set_experiment(s.mlflow_experiment)
        _ready = True
        log.info("mlflow.enabled", experiment=s.mlflow_experiment)
    except Exception as e:
        log.warning("mlflow.init_failed", error=str(e))


@contextmanager
def trace_span(name: str, span_type: str | None = None, **attrs):
    """Open an MLflow span (a child of the active one, or a new trace root)."""
    if not _ready:
        yield _Handle()
        return
    cm = None
    span = None
    try:
        import mlflow

        cm = mlflow.start_span(name=name, span_type=span_type or "UNKNOWN")
        span = cm.__enter__()
    except Exception as e:
        log.warning("mlflow.span_failed", error=str(e))
        cm = None
    handle = _Handle(span)
    handle.set(**attrs)
    try:
        yield handle
    except BaseException as exc:
        if cm is not None:
            try:
                cm.__exit__(type(exc), exc, exc.__traceback__)
            except Exception as e:
                log.warning("mlflow.span_failed", error=str(e))
        raise
    else:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception as e:
                log.warning("mlflow.span_failed", error=str(e))
