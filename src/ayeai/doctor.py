from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

import psutil

from .backends import BackendManager
from .config import RuntimeConfig, default_npu_model_dir, project_root
from .media import ffprobe_json
from .resources import _gpu_snapshot
from .utils import disk_free_gb, run_command, text_output, which


def _check_tool(name: str) -> dict[str, Any]:
    executable = which(name)
    result: dict[str, Any] = {"name": name, "ok": bool(executable), "path": executable}
    if executable:
        try:
            version_args = [executable, "--version"] if name == "nvidia-smi" else [executable, "-version"]
            result["version"] = text_output(run_command(version_args, timeout=10)).splitlines()[0]
        except Exception as exc:
            result["error"] = str(exc)
    return result


def _model_cache_status(config: RuntimeConfig) -> dict[str, Any]:
    npu_dir = Path(config.npu_model_dir or default_npu_model_dir())
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    candidates = [
        hf_home / "hub" / f"models--Systran--faster-whisper-{config.model_size}",
        hf_home / "hub" / f"models--Systran--faster-whisper-{config.model_size.replace('/', '--')}",
    ]
    return {
        "faster_whisper_model": config.model_size,
        "faster_whisper_cache_seen": any(path.exists() for path in candidates),
        "npu_model_dir": str(npu_dir),
        "npu_model_exists": npu_dir.exists() and any(npu_dir.iterdir()) if npu_dir.exists() else False,
    }


def run_doctor(
    config: RuntimeConfig,
    probe: bool = True,
    runtime_report: dict[str, Any] | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "app": "ayeai",
        "version": "0.1.0",
        "language": language,
        "platform": platform.platform(),
        "python": {"version": platform.python_version(), "executable": sys.executable, "supported": sys.version_info[:2] in {(3, 12), (3, 13)}},
        "tools": {"ffmpeg": _check_tool("ffmpeg"), "ffprobe": _check_tool("ffprobe"), "nvidia_smi": _check_tool("nvidia-smi")},
        "resources": {
            "cpu_count": os.cpu_count(),
            "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "ram_available_gb": round(psutil.virtual_memory().available / (1024**3), 2),
            "disk_free_gb": round(disk_free_gb(project_root()), 2),
            "gpu": _gpu_snapshot(),
        },
        "models": _model_cache_status(config),
    }
    if runtime_report is not None:
        report["runtime"] = runtime_report
    try:
        from openvino import Core

        report["openvino"] = {"ok": True, "version": getattr(__import__("openvino"), "__version__", None), "devices": list(Core().available_devices)}
    except Exception as exc:
        report["openvino"] = {"ok": False, "error": str(exc)}
    manager = BackendManager(config, _NullLogger())
    if probe:
        manager.probe_all()
    report["backends"] = manager.report()
    report["usable_backends"] = [name for name, item in report["backends"].items() if item.get("transcription_ok")]
    report["ok"] = bool(report["tools"]["ffmpeg"]["ok"] and report["tools"]["ffprobe"]["ok"] and report["usable_backends"])
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
