from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .media import extract_pcm, pcm_rms_profile


class LLMProvider(Protocol):
    name: str

    def score_context(self, transcript: str, metrics: dict[str, float]) -> tuple[float, list[str]]:
        ...


class HeuristicLLMProvider:
    """Deterministic local provider; can be replaced without changing pipeline state."""

    name = "heuristic-local"

    def score_context(self, transcript: str, metrics: dict[str, float]) -> tuple[float, list[str]]:
        reasons: list[str] = []
        if metrics["density"] >= 4.5:
            reasons.append("語音密度高")
        if metrics["loudness_change"] >= 0.30:
            reasons.append("音量/情緒變化")
        if metrics["exclaim"] > 0:
            reasons.append("驚嘆或強烈語氣")
        if metrics["repetition"] > 0:
            reasons.append("重複或口號式語句")
        if metrics["context"] >= 0.65:
            reasons.append("上下文資訊量高")
        if not transcript.strip() and metrics["loudness_change"] >= 0.60:
            reasons.append("無逐字稿但有明顯音訊事件")
        score = (
            0.25 * min(1.0, metrics["density"] / 8.0)
            + 0.25 * min(1.0, metrics["loudness_change"])
            + 0.18 * min(1.0, metrics["exclaim"] / 3.0)
            + 0.14 * min(1.0, metrics["repetition"])
            + 0.18 * min(1.0, metrics["context"])
        )
        return float(max(0.0, min(1.0, score))), reasons


@dataclass(slots=True)
class Highlight:
    start: float
    end: float
    score: float
    confirmed: bool
    reasons: list[str]
    transcript: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "score": round(self.score, 4),
            "confirmed": self.confirmed,
            "reasons": self.reasons,
            "transcript": self.transcript,
        }


_PUNCT = re.compile(r"[!！?？。～~…]+")
_EXCITEMENT = re.compile(r"(哇|欸|啊+|天啊|太扯|真的假的|不可能|笑死|救命|漂亮|贏了|輸了|nice|wow|what|omg)", re.IGNORECASE)
_SPACE_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def _norm(text: str) -> str:
    return _SPACE_PUNCT.sub("", text.lower())


def _repetition(text: str) -> float:
    normalized = _norm(text)
    if len(normalized) < 4:
        return 0.0
    for size in (2, 3, 4, 5):
        grams = [normalized[i : i + size] for i in range(len(normalized) - size + 1)]
        if len(grams) != len(set(grams)):
            return 1.0
    return 0.0


def _merge_duplicate_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for segment in sorted(segments, key=lambda item: (item["start"], item["end"])):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        normalized = _norm(text)
        duplicate_index: int | None = None
        for index in range(max(0, len(output) - 4), len(output)):
            previous = output[index]
            overlap = min(segment["end"], previous["end"]) - max(segment["start"], previous["start"])
            if overlap >= -0.05 and normalized and normalized == _norm(previous["text"]):
                duplicate_index = index
                break
        if duplicate_index is None:
            output.append(dict(segment))
        else:
            previous = output[duplicate_index]
            # Only remove an exact overlap duplicate; uncertain overlaps remain visible.
            previous["start"] = min(previous["start"], segment["start"])
            previous["end"] = max(previous["end"], segment["end"])
    return output


def _audio_event(input_path: Any, start: float, end: float) -> tuple[float, float]:
    try:
        center_start = max(0.0, start)
        samples = extract_pcm(input_path, center_start, max(0.5, end - center_start))
        profile = pcm_rms_profile(samples)
        if len(profile) < 3:
            return float(profile.mean() if len(profile) else 0.0), 0.0
        midpoint = len(profile) // 2
        before = float(np.mean(profile[:midpoint])) if midpoint else float(profile.mean())
        after = float(np.mean(profile[midpoint:])) if midpoint < len(profile) else before
        peak = float(np.max(profile))
        change = min(1.0, abs(after - before) * 4.0 + max(0.0, peak - min(before, after)) * 2.0)
        return peak, change
    except Exception:
        return 0.0, 0.0


def analyze_highlights(
    input_path: Any,
    segments: list[dict[str, Any]],
    duration: float,
    threshold: float,
    max_per_hour: int,
    provider: LLMProvider | None = None,
) -> list[Highlight]:
    provider = provider or HeuristicLLMProvider()
    clean = _merge_duplicate_segments(segments)
    if not clean:
        # No speech should normally yield no highlight; an audio-only event is retained only when very strong.
        return []
    candidates: list[Highlight] = []
    for anchor in clean:
        start = max(0.0, float(anchor["start"]) - 8.0)
        end = min(duration, float(anchor["end"]) + 10.0)
        context_parts = [item["text"] for item in clean if item["end"] >= start and item["start"] <= end]
        transcript = " ".join(context_parts).strip()
        span = max(1.0, end - start)
        chars = len(_norm(transcript))
        density = chars / span
        exclaim = float(len(_PUNCT.findall(transcript)) + len(_EXCITEMENT.findall(transcript)))
        repetition = _repetition(transcript)
        _peak, loudness_change = _audio_event(input_path, start, end)
        context = min(1.0, len(context_parts) / 6.0 + min(1.0, chars / 180.0) * 0.5)
        metrics = {
            "density": density,
            "exclaim": exclaim,
            "repetition": repetition,
            "loudness_change": loudness_change,
            "context": context,
        }
        provider_score, reasons = provider.score_context(transcript, metrics)
        if not reasons:
            continue
        candidates.append(Highlight(start, end, provider_score, provider_score >= threshold, reasons, transcript))
    candidates.sort(key=lambda item: item.score, reverse=True)
    selected: list[Highlight] = []
    max_total = max(1, int(max_per_hour * max(1.0, duration / 3600.0)))
    for candidate in candidates:
        if not candidate.confirmed:
            continue
        overlaps = [min(candidate.end, item.end) - max(candidate.start, item.start) for item in selected]
        if any(overlap > min(candidate.end - candidate.start, item.end - item.start) * 0.55 for overlap, item in zip(overlaps, selected)):
            continue
        selected.append(candidate)
        if len(selected) >= max_total:
            break
    return sorted(selected, key=lambda item: item.start)
