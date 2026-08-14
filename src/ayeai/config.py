from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from .runtime import bundle_root, user_data_dir


def _default_cpu_threads() -> int:
    count = os.cpu_count() or 4
    return max(1, min(4, count // 2))


@dataclasses.dataclass(slots=True)
class RuntimeConfig:
    model_size: str = "small"
    language: str = "zh"
    backend: str = "auto"
    npu_model_dir: Path | None = None
    chunk_seconds: float = 90.0
    overlap_seconds: float = 6.0
    vad_window_seconds: float = 3.0
    queue_size: int = 2
    max_retries: int = 1
    cpu_threads: int = dataclasses.field(default_factory=_default_cpu_threads)
    cpu_max_percent: float = 55.0
    max_temperature_c: float = 84.0
    max_gpu_memory_percent: float = 88.0
    min_free_disk_gb: float = 10.0
    min_free_ram_gb: float = 2.0
    resource_poll_seconds: float = 2.0
    backend_cooldown_seconds: float = 45.0
    game_cooldown_seconds: float = 30.0
    corrupt_review_seconds: float = 15.0
    highlight_threshold: float = 0.58
    highlight_max_per_hour: int = 12
    auto_tune: bool = True
    startup_backend_probe: bool = True
    keep_intermediate_audio: bool = False

    def normalized(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        if data["npu_model_dir"] is not None:
            data["npu_model_dir"] = str(Path(data["npu_model_dir"]).resolve())
        return data

    def config_hash(self) -> str:
        payload = json.dumps(self.normalized(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def validate(self) -> None:
        if self.chunk_seconds < 30 or self.chunk_seconds > 300:
            raise ValueError("chunk_seconds 必須介於 30 與 300 秒")
        if self.overlap_seconds < 0 or self.overlap_seconds >= self.chunk_seconds / 2:
            raise ValueError("overlap_seconds 必須 >= 0 且小於 chunk_seconds 一半")
        if self.queue_size < 1 or self.queue_size > 16:
            raise ValueError("queue_size 必須介於 1 與 16")
        if self.cpu_threads < 1:
            raise ValueError("cpu_threads 必須 >= 1")
        if self.max_retries < 0 or self.max_retries > 5:
            raise ValueError("max_retries 必須介於 0 與 5")


def project_root() -> Path:
    return bundle_root()


def default_runtime_dir() -> Path:
    path = (user_data_dir() / "runtime") if getattr(sys, "frozen", False) else project_root() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_npu_model_dir() -> Path:
    from .runtime import default_npu_model_dir as _default_npu_model_dir

    return _default_npu_model_dir()


def load_benchmark_recommendation() -> dict[str, Any] | None:
    path = default_runtime_dir() / "benchmark.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def apply_auto_tune(config: RuntimeConfig) -> RuntimeConfig:
    if not config.auto_tune:
        return config
    rec = load_benchmark_recommendation()
    if not rec:
        return config
    tuned = dataclasses.replace(config)
    recommendation = rec.get("recommendation", {})
    if isinstance(recommendation.get("chunk_seconds"), (int, float)):
        tuned.chunk_seconds = float(recommendation["chunk_seconds"])
    if isinstance(recommendation.get("overlap_seconds"), (int, float)):
        tuned.overlap_seconds = float(recommendation["overlap_seconds"])
    if isinstance(recommendation.get("backend"), str) and config.backend == "auto":
        tuned.backend = recommendation["backend"]
    if isinstance(recommendation.get("cpu_threads"), int):
        tuned.cpu_threads = max(1, min(config.cpu_threads, recommendation["cpu_threads"]))
    tuned.validate()
    return tuned
