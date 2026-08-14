"""Paths and executable discovery that work both from source and PyInstaller."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def executable_dir() -> Path:
    return Path(sys.executable).resolve().parent if is_frozen() else bundle_root()


def user_data_dir() -> Path:
    override = os.environ.get("AYEAI_DATA_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        path = base / "AyeAI"
    else:
        path = Path.home() / ".local" / "share" / "AyeAI"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_tools_dir() -> Path:
    path = user_data_dir() / "tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_models_dir() -> Path:
    path = user_data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_hf_home() -> Path:
    override = os.environ.get("HF_HOME")
    if override:
        path = Path(override).expanduser().resolve()
    elif is_frozen():
        path = user_data_dir() / "huggingface"
    else:
        path = Path.home() / ".cache" / "huggingface"
    path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(path))
    return path


def bundled_resource(relative: str) -> Path | None:
    candidates = [bundle_root() / relative]
    if is_frozen():
        candidates.append(executable_dir() / relative)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def default_npu_model_dir() -> Path:
    if not is_frozen():
        source_model = bundle_root() / "models" / "whisper-small-int8-ov"
        if source_model.exists():
            return source_model
    return runtime_models_dir() / "whisper-small-int8-ov"


def resolve_tool(name: str) -> str | None:
    """Find bundled, per-user downloaded, or PATH-provided media tools."""

    base_name = Path(name).stem
    suffixes = [".exe", ""] if os.name == "nt" else [""]
    candidates: list[Path] = []
    for relative_root in ("vendor/ffmpeg", "ffmpeg"):
        for suffix in suffixes:
            resource = bundled_resource(f"{relative_root}/{base_name}{suffix}")
            if resource:
                candidates.append(resource)
    for suffix in suffixes:
        candidates.append(runtime_tools_dir() / "ffmpeg" / f"{base_name}{suffix}")
        candidates.append(runtime_tools_dir() / f"{base_name}{suffix}")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)
