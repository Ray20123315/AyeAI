from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def which(name: str) -> str | None:
    if Path(name).stem.lower() in {"ffmpeg", "ffprobe"}:
        try:
            from .runtime import resolve_tool

            resolved = resolve_tool(Path(name).stem)
            if resolved:
                return resolved
        except Exception:
            pass
    return shutil.which(name)


def run_command(
    args: Sequence[str | os.PathLike[str]],
    *,
    timeout: float | None = None,
    check: bool = False,
    capture: bool = True,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    command = [os.fspath(arg) for arg in args]
    return subprocess.run(
        command,
        input=input_bytes,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
        check=check,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def text_output(result: subprocess.CompletedProcess[bytes]) -> str:
    data = result.stdout or result.stderr or b""
    return data.decode("utf-8", errors="replace")


def human_seconds(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(int(seconds), 3600)
    minutes, seconds_i = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds_i:02d}s"
    if minutes:
        return f"{minutes}m {seconds_i:02d}s"
    return f"{seconds_i}s"


def safe_slug(value: str, max_length: int = 100) -> str:
    cleaned = "".join(char if char.isalnum() or char in " ._-" else "_" for char in value).strip(" .")
    return (cleaned or "job")[:max_length]


def setup_logging(log_path: Path | None = None, verbose: bool = False, console: bool = True) -> logging.Logger:
    logger = logging.getLogger("ayeai")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def sleep_interruptible(seconds: float, stop_event: Any = None) -> None:
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        if stop_event is not None and stop_event.is_set():
            return
        time.sleep(min(0.25, max(0.01, end - time.monotonic())))


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def disk_free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024**3)
