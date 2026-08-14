from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from .backends import BackendManager
from .config import RuntimeConfig, default_runtime_dir
from .media import extract_pcm, validate_source
from .utils import atomic_write_json, now_iso


def run_benchmark(input_path: Path, config: RuntimeConfig, output_path: Path | None = None) -> dict[str, Any]:
    _probe, media = validate_source(input_path)
    duration = min(20.0, float(media["duration"]))
    audio = extract_pcm(input_path, 0.0, duration)
    manager = BackendManager(config, _NullLogger())
    statuses = manager.probe_all(audio)
    measurements: dict[str, Any] = {}
    for name, status in statuses.items():
        if not status.transcription_ok:
            measurements[name] = {"ready": False, "error": status.error or status.detail}
            continue
        started = time.perf_counter()
        try:
            segments = manager.transcribe(name, audio, 0.0)
            elapsed = time.perf_counter() - started
            measurements[name] = {"ready": True, "elapsed_seconds": round(elapsed, 4), "audio_seconds": round(duration, 3), "real_time_factor": round(elapsed / max(0.1, duration), 4), "segments": len(segments)}
        except Exception as exc:
            measurements[name] = {"ready": False, "error": str(exc)}
    ready = [(name, item["real_time_factor"]) for name, item in measurements.items() if item.get("ready")]
    best = min(ready, key=lambda item: item[1])[0] if ready else "cpu"
    best_rtf = min((item[1] for item in ready), default=2.0)
    if best_rtf <= 0.35:
        chunk_seconds = 120.0
    elif best_rtf <= 0.8:
        chunk_seconds = 90.0
    else:
        chunk_seconds = 60.0
    recommendation = {
        "backend": best,
        "chunk_seconds": chunk_seconds,
        "overlap_seconds": 6.0,
        "cpu_threads": max(1, min(config.cpu_threads, 4)),
        "parallel_workers": 1,
        "reason": "以實測 real-time factor 選擇；工作期間仍由前景/溫度/記憶體策略覆寫 GPU",
    }
    report = {"created_at": now_iso(), "input": str(input_path.resolve()), "media": media, "model_size": config.model_size, "measurements": measurements, "recommendation": recommendation}
    target = output_path or default_runtime_dir() / "benchmark.json"
    atomic_write_json(target, report)
    manager.close()
    return report


class _NullLogger:
    def debug(self, *args: Any, **kwargs: Any) -> None:
        return

    def info(self, *args: Any, **kwargs: Any) -> None:
        return

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return

    def error(self, *args: Any, **kwargs: Any) -> None:
        return
