"""Cross-machine runtime completion and capability detection.

The packaged EXE contains Python and the media tools. Model weights and
machine-specific drivers stay outside the read-only PyInstaller extraction
directory and are completed in the per-user AyeAI data directory.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .runtime import (
    bundled_resource,
    default_npu_model_dir,
    is_frozen,
    runtime_hf_home,
    runtime_models_dir,
    runtime_tools_dir,
    user_data_dir,
    resolve_tool,
)
from .utils import atomic_write_json, now_iso


DEFAULT_FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
NPU_REPOSITORY = "OpenVINO/whisper-small-int8-ov"


def _status(name: str, **values: Any) -> dict[str, Any]:
    return {"name": name, **values}


def _download(url: str, destination: Path, timeout: float = 120.0) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    request = urllib.request.Request(url, headers={"User-Agent": "AyeAI-runtime/0.2"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as handle:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _safe_extract_zip(archive: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    root = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            relative = Path(member.filename)
            target = (destination / relative).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError(f"拒絕不安全的 archive path: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            extracted.append(target)
    return extracted


def ensure_ffmpeg(auto_download: bool = True) -> dict[str, Any]:
    ffmpeg = resolve_tool("ffmpeg")
    ffprobe = resolve_tool("ffprobe")
    if ffmpeg and ffprobe:
        source = "bundled-or-user" if ("AyeAI" in ffmpeg or "vendor" in ffmpeg) else "PATH"
        return _status("ffmpeg", ready=True, source=source, ffmpeg=ffmpeg, ffprobe=ffprobe, downloaded=False)
    if not auto_download:
        return _status("ffmpeg", ready=False, downloaded=False, error="ffmpeg/ffprobe not found")
    url = os.environ.get("AYEAI_FFMPEG_URL", DEFAULT_FFMPEG_URL)
    tools_dir = runtime_tools_dir() / "ffmpeg"
    archive = user_data_dir() / "downloads" / "ffmpeg.zip"
    try:
        _download(url, archive)
        with tempfile.TemporaryDirectory(prefix="ayeai-ffmpeg-") as temporary:
            extracted = _safe_extract_zip(archive, Path(temporary))
            binaries = {path.name.lower(): path for path in extracted}
            selected: dict[str, Path] = {}
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                match = next((path for key, path in binaries.items() if key == name), None)
                if match:
                    selected[name] = match
            if len(selected) != 2:
                raise RuntimeError("下載的 FFmpeg archive 缺少 ffmpeg.exe 或 ffprobe.exe")
            tools_dir.mkdir(parents=True, exist_ok=True)
            for name, source in selected.items():
                temporary_target = tools_dir / f".{name}.part"
                shutil.copy2(source, temporary_target)
                os.replace(temporary_target, tools_dir / name)
        ffmpeg = resolve_tool("ffmpeg")
        ffprobe = resolve_tool("ffprobe")
        return _status("ffmpeg", ready=bool(ffmpeg and ffprobe), source="auto-download", ffmpeg=ffmpeg, ffprobe=ffprobe, downloaded=True, url=url)
    except Exception as exc:
        return _status("ffmpeg", ready=False, downloaded=False, url=url, error=str(exc))


def _ensure_hf_home() -> Path:
    path = runtime_hf_home()
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    return path


def ensure_faster_whisper_model(model_size: str, auto_download: bool = True) -> dict[str, Any]:
    model_path = Path(model_size).expanduser()
    if model_path.exists():
        return _status("faster-whisper", ready=True, repo_id=None, path=str(model_path.resolve()), downloaded=False)
    repo_id = f"Systran/faster-whisper-{model_size}"
    cache_dir = _ensure_hf_home()
    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(repo_id=repo_id, cache_dir=str(cache_dir / "hub"), local_files_only=not auto_download)
        return _status("faster-whisper", ready=True, repo_id=repo_id, path=str(path), downloaded=auto_download)
    except Exception as exc:
        return _status("faster-whisper", ready=False, repo_id=repo_id, downloaded=False, error=str(exc))


def _openvino_devices() -> list[str]:
    try:
        from openvino import Core

        return [str(device) for device in Core().available_devices]
    except Exception:
        return []


def _copy_bundled_npu_model(target: Path) -> bool:
    bundled = bundled_resource("models/whisper-small-int8-ov")
    if not bundled or not bundled.is_dir() or target.exists():
        return False
    temporary = target.with_name(f".{target.name}.part")
    try:
        shutil.copytree(bundled, temporary, dirs_exist_ok=True)
        os.replace(temporary, target)
        return True
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def ensure_npu_model(target: Path | None = None, auto_download: bool = True) -> dict[str, Any]:
    destination = (target or default_npu_model_dir()).resolve()
    if destination.exists() and any(destination.glob("*.xml")) and any(destination.glob("*.bin")):
        return _status("openvino-npu", ready=True, repo_id=NPU_REPOSITORY, path=str(destination), downloaded=False)
    try:
        if _copy_bundled_npu_model(destination):
            return _status("openvino-npu", ready=True, repo_id=NPU_REPOSITORY, path=str(destination), downloaded=False, source="bundled")
        if not auto_download:
            return _status("openvino-npu", ready=False, repo_id=NPU_REPOSITORY, path=str(destination), downloaded=False, error="NPU model is missing")
        from huggingface_hub import snapshot_download

        destination.parent.mkdir(parents=True, exist_ok=True)
        snapshot_download(repo_id=NPU_REPOSITORY, local_dir=str(destination), local_dir_use_symlinks=False)
        ready = destination.exists() and any(destination.glob("*.xml")) and any(destination.glob("*.bin"))
        return _status("openvino-npu", ready=ready, repo_id=NPU_REPOSITORY, path=str(destination), downloaded=True)
    except Exception as exc:
        return _status("openvino-npu", ready=False, repo_id=NPU_REPOSITORY, path=str(destination), downloaded=False, error=str(exc))


def detect_capabilities() -> dict[str, Any]:
    devices = _openvino_devices()
    return {
        "frozen_executable": is_frozen(),
        "runtime_data_dir": str(user_data_dir()),
        "python_executable": os.fspath(Path(__import__("sys").executable).resolve()),
        "cpu": {"available": True, "logical_processors": __import__("os").cpu_count()},
        "openvino_devices": devices,
        "npu_detected": any(device.upper().startswith("NPU") for device in devices),
        "cuda_tool": bool(resolve_tool("nvidia-smi")),
    }


def ensure_runtime(config: RuntimeConfig, auto_download: bool = True) -> dict[str, Any]:
    """Complete missing tools/models and return a machine-readable capability report."""

    report: dict[str, Any] = {
        "created_at": now_iso(),
        "capabilities": detect_capabilities(),
        "downloads": [],
    }
    report["downloads"].append(ensure_ffmpeg(auto_download=auto_download))
    report["downloads"].append(ensure_faster_whisper_model(config.model_size, auto_download=auto_download))
    if report["capabilities"]["npu_detected"]:
        report["downloads"].append(ensure_npu_model(config.npu_model_dir, auto_download=auto_download))
    else:
        report["downloads"].append(_status("openvino-npu", ready=False, skipped=True, reason="NPU device not detected"))
    report["ready"] = {item["name"]: bool(item.get("ready")) for item in report["downloads"]}
    try:
        atomic_write_json(user_data_dir() / "runtime_manifest.json", report)
    except OSError:
        pass
    return report
