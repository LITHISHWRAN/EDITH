
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.config import CONFIG, LOG_DIR


@dataclass
class Span:
    name: str
    duration_ms: float
    detail: dict = field(default_factory=dict)


@dataclass
class Trace:
    utterance: str
    spans: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time.perf_counter)

    @contextmanager
    def span(self, name: str, /, **detail):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.spans.append(
                Span(
                    name=name,
                    duration_ms=(time.perf_counter() - start) * 1000.0,
                    detail=detail,
                )
            )

    def note(self, **metadata):
        self.metadata.update(metadata)

    @property
    def total_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000.0

    def render(self) -> str:
        width = max((len(s.name) for s in self.spans), default=0)
        lines = [
            f"  {span.name.ljust(width)}  {span.duration_ms:7.1f} ms"
            for span in self.spans
        ]
        lines.append(f"  {'TOTAL'.ljust(width)}  {self.total_ms:7.1f} ms")
        return "\n".join(lines)

    def flush(self):
        if not CONFIG.trace_enabled:
            return

        LOG_DIR.mkdir(parents=True, exist_ok=True)

        record = {
            "timestamp": time.time(),
            "utterance": self.utterance,
            "total_ms": round(self.total_ms, 2),
            "metadata": self.metadata,
            "spans": [
                {
                    "name": s.name,
                    "ms": round(s.duration_ms, 2),
                    **s.detail,
                }
                for s in self.spans
            ],
        }

        with open(LOG_DIR / "traces.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
