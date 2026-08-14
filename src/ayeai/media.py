from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from .utils import atomic_write_json, run_command, text_output, which


class MediaError(RuntimeError):
    pass


def _tool(name: str) -> str:
    path = which(name)
    if not path:
        raise MediaError(f"找不到 {name}，請先安裝 FFmpeg 並確認已加入 PATH")
    return path


def ffprobe_json(input_path: Path, timeout: float = 60.0) -> dict[str, Any]:
    result = run_command(
        [
            _tool("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            os.fspath(input_path),
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        raise MediaError(f"ffprobe 無法讀取來源：{text_output(result).strip()[:1000]}")
    try:
        return json.loads((result.stdout or b"{}").decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe 回傳非 JSON：{exc}") from exc


def summarize_media(probe: dict[str, Any]) -> dict[str, Any]:
    streams = probe.get("streams", [])
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video = [stream for stream in streams if stream.get("codec_type") == "video"]
    format_data = probe.get("format", {})
    duration = float(format_data.get("duration") or 0.0)
    if duration <= 0:
        durations = [float(stream.get("duration") or 0.0) for stream in streams]
        duration = max(durations or [0.0])
    return {
        "duration": duration,
        "format_name": format_data.get("format_name"),
        "size": int(format_data.get("size") or 0),
        "audio_streams": len(audio),
        "video_streams": len(video),
        "audio_codec": audio[0].get("codec_name") if audio else None,
        "video_codec": video[0].get("codec_name") if video else None,
        "sample_rate": int(audio[0].get("sample_rate") or 0) if audio else 0,
        "channels": int(audio[0].get("channels") or 0) if audio else 0,
    }


def validate_source(input_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if not input_path.exists() or not input_path.is_file():
        raise MediaError(f"來源不存在或不是檔案：{input_path}")
    probe = ffprobe_json(input_path)
    summary = summarize_media(probe)
    errors: list[str] = []
    if summary["duration"] <= 0:
        errors.append("找不到有效影片時長")
    if summary["audio_streams"] == 0:
        errors.append("來源沒有音訊 stream，無法產生逐字稿")
    if summary["video_streams"] == 0:
        errors.append("來源沒有影片 stream")
    if errors:
        raise MediaError("來源驗證失敗：" + "；".join(errors))
    return probe, summary


def extract_pcm(input_path: Path, start: float, duration: float, timeout: float | None = None) -> np.ndarray:
    if duration <= 0:
        return np.empty(0, dtype=np.float32)
    timeout = timeout or max(45.0, duration * 3.0)
    result = run_command(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-xerror",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            os.fspath(input_path),
            "-t",
            f"{duration:.3f}",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_f32le",
            "-f",
            "f32le",
            "pipe:1",
        ],
        timeout=timeout,
    )
    diagnostics = (result.stderr or b"").decode("utf-8", errors="replace")
    if result.returncode != 0 or not result.stdout or re.search(r"(error|premature|invalid|corrupt|truncat|decode)", diagnostics, re.IGNORECASE):
        detail = diagnostics or text_output(result)
        raise MediaError(f"音訊解碼失敗 @{start:.3f}s：{detail.strip()[:1000]}")
    samples = np.frombuffer(result.stdout, dtype="<f4").astype(np.float32, copy=True)
    if samples.size == 0:
        raise MediaError(f"音訊解碼結果為空 @{start:.3f}s")
    return np.nan_to_num(np.clip(samples, -1.0, 1.0))


def pcm_rms_profile(samples: np.ndarray, sample_rate: int = 16000, frame_ms: int = 30) -> np.ndarray:
    frame = max(1, int(sample_rate * frame_ms / 1000))
    count = len(samples) // frame
    if count == 0:
        return np.array([float(np.sqrt(np.mean(samples**2))) if len(samples) else 0.0], dtype=np.float32)
    trimmed = samples[: count * frame].reshape(count, frame)
    return np.sqrt(np.mean(trimmed * trimmed, axis=1)).astype(np.float32)


def find_quiet_boundary(input_path: Path, boundary: float, window: float = 3.0) -> float:
    """Find a nearby low-energy frame without creating a physical audio slice."""
    if boundary <= 0:
        return boundary
    try:
        start = max(0.0, boundary - window)
        samples = extract_pcm(input_path, start, window * 2)
        profile = pcm_rms_profile(samples)
        if len(profile) < 3:
            return boundary
        # Prefer a quiet frame, but avoid moving a boundary too far from target.
        center = len(profile) // 2
        low = max(0, center - int(window / 0.03))
        high = min(len(profile), center + int(window / 0.03) + 1)
        candidate = low + int(np.argmin(profile[low:high]))
        adjusted = start + candidate * 0.03 + 0.015
        return max(start + 0.25, min(boundary + window, adjusted))
    except MediaError:
        return boundary


def export_review_segment(
    input_path: Path,
    review_dir: Path,
    start: float,
    duration: float,
    error: str,
    chunk_id: int | str,
) -> Path | None:
    review_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(chunk_id))
    target = review_dir / f"corrupt_{safe_id}_{max(0.0, start):010.3f}.mp4"
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part.mp4")
    result = run_command(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-err_detect",
            "ignore_err",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            os.fspath(input_path),
            "-t",
            f"{max(1.0, duration):.3f}",
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            os.fspath(temporary),
        ],
        timeout=max(90.0, duration * 6),
    )
    if result.returncode == 0 and temporary.exists() and temporary.stat().st_size > 0:
        try:
            os.replace(temporary, target)
            atomic_write_json(
                target.with_suffix(".json"),
                {
                    "chunk_id": str(chunk_id),
                    "start": start,
                    "duration": duration,
                    "error": error,
                    "created_at": time.time(),
                    "action": "pending",
                },
            )
            return target
        finally:
            temporary.unlink(missing_ok=True)
    temporary.unlink(missing_ok=True)
    atomic_write_json(
        review_dir / f"corrupt_{safe_id}_{max(0.0, start):010.3f}.json",
        {"chunk_id": str(chunk_id), "start": start, "duration": duration, "error": error, "action": "pending", "export_error": text_output(result)[:1000]},
    )
    return None


def export_candidate(
    input_path: Path,
    output_path: Path,
    start: float,
    duration: float,
    *,
    crf: int = 21,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.part.mp4")
    result = run_command(
        [
            _tool("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            os.fspath(input_path),
            "-t",
            f"{max(1.0, duration):.3f}",
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-threads",
            "2",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            os.fspath(temporary),
        ],
        timeout=max(180.0, duration * 12),
    )
    if result.returncode != 0 or not temporary.exists() or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        raise MediaError(f"候選影片輸出失敗：{text_output(result).strip()[:1000]}")
    os.replace(temporary, output_path)
    try:
        probe = ffprobe_json(output_path)
        summary = summarize_media(probe)
        if summary["duration"] <= 0:
            raise MediaError("候選影片時長為 0")
        return summary
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def validate_candidate(output_path: Path) -> dict[str, Any]:
    probe = ffprobe_json(output_path)
    summary = summarize_media(probe)
    if summary["duration"] <= 0 or (summary["video_streams"] == 0 and summary["audio_streams"] == 0):
        raise MediaError("候選影片沒有可播放 stream 或時長")
    return summary
