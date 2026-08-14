from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .media import find_quiet_boundary


def build_logical_chunks(input_path: Path, duration: float, config: RuntimeConfig) -> list[dict[str, float]]:
    """Build logical windows only; no media files are written here."""
    duration = max(0.1, float(duration))
    if duration <= config.chunk_seconds:
        return [{"start": 0.0, "end": duration, "core_start": 0.0, "core_end": duration}]
    boundaries = [0.0]
    target = config.chunk_seconds
    minimum_step = max(20.0, config.chunk_seconds * 0.45)
    while target < duration - 0.25:
        adjusted = find_quiet_boundary(input_path, target, config.vad_window_seconds)
        if adjusted - boundaries[-1] < minimum_step:
            adjusted = target
        adjusted = max(boundaries[-1] + minimum_step, min(duration, adjusted))
        boundaries.append(adjusted)
        target = adjusted + config.chunk_seconds
    if boundaries[-1] < duration:
        boundaries.append(duration)
    half_overlap = config.overlap_seconds / 2.0
    chunks: list[dict[str, float]] = []
    for index in range(len(boundaries) - 1):
        core_start = boundaries[index]
        core_end = boundaries[index + 1]
        start = max(0.0, core_start - (half_overlap if index else 0.0))
        end = min(duration, core_end + (half_overlap if index < len(boundaries) - 2 else 0.0))
        chunks.append({"start": round(start, 3), "end": round(end, 3), "core_start": round(core_start, 3), "core_end": round(core_end, 3)})
    return chunks
