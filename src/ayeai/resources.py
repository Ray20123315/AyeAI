from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import psutil

from .config import RuntimeConfig
from .utils import disk_free_gb, run_command, sleep_interruptible, which


PROTECTED_FOREGROUND = re.compile(
    r"(steam|discord|chrome|msedge|firefox|opera|brave|obs|valorant|league|cs2|counter|eldenring|minecraft|dota|fortnite|apex|overwatch|wow|warcraft|genshin|pubg|game|xbox|epicgames|uplay)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ResourceSnapshot:
    timestamp: float
    cpu_percent: float
    memory_percent: float
    free_ram_gb: float
    disk_free_gb: float
    foreground_process: str | None
    foreground_pid: int | None
    protected_foreground: bool
    gpu_name: str | None
    gpu_util_percent: float | None
    gpu_memory_used_mb: float | None
    gpu_memory_total_mb: float | None
    gpu_temperature_c: float | None
    npu_available: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _foreground_process() -> tuple[str | None, int | None]:
    if os.name != "nt":
        return None, None
    try:
        user32 = ctypes.windll.user32
        window = user32.GetForegroundWindow()
        if not window:
            return None, None
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        process_id = int(pid.value)
        return psutil.Process(process_id).name(), process_id
    except (OSError, AttributeError, psutil.Error):
        return None, None


def _gpu_snapshot() -> dict[str, Any]:
    executable = which("nvidia-smi")
    if not executable:
        return {}
    try:
        result = run_command(
            [
                executable,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout=5,
        )
        if result.returncode != 0:
            return {}
        fields = [item.strip() for item in (result.stdout or b"").decode("utf-8", errors="replace").splitlines()[0].split(",")]
        if len(fields) < 5:
            return {}
        return {
            "gpu_name": fields[0],
            "gpu_util_percent": float(fields[1]),
            "gpu_memory_used_mb": float(fields[2]),
            "gpu_memory_total_mb": float(fields[3]),
            "gpu_temperature_c": float(fields[4]),
        }
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return {}


class ResourceMonitor:
    def __init__(self, config: RuntimeConfig, npu_available: bool = False, manual_busy_flag: Path | None = None):
        self.config = config
        self.npu_available = npu_available
        self.manual_busy_flag = manual_busy_flag
        self.last: ResourceSnapshot | None = None

    def sample(self) -> ResourceSnapshot:
        cpu = float(psutil.cpu_percent(interval=0.15))
        memory = psutil.virtual_memory()
        foreground, pid = _foreground_process()
        gpu = _gpu_snapshot()
        free_disk = disk_free_gb(Path.cwd())
        manual_busy = bool(self.manual_busy_flag and self.manual_busy_flag.exists())
        protected = manual_busy or bool(foreground and PROTECTED_FOREGROUND.search(foreground))
        if manual_busy and not foreground:
            foreground = "ayeai-manual-resource-busy"
        self.last = ResourceSnapshot(
            timestamp=time.time(),
            cpu_percent=cpu,
            memory_percent=float(memory.percent),
            free_ram_gb=memory.available / (1024**3),
            disk_free_gb=free_disk,
            foreground_process=foreground,
            foreground_pid=pid,
            protected_foreground=protected,
            gpu_name=gpu.get("gpu_name"),
            gpu_util_percent=gpu.get("gpu_util_percent"),
            gpu_memory_used_mb=gpu.get("gpu_memory_used_mb"),
            gpu_memory_total_mb=gpu.get("gpu_memory_total_mb"),
            gpu_temperature_c=gpu.get("gpu_temperature_c"),
            npu_available=self.npu_available,
        )
        return self.last

    def is_critical(self, snapshot: ResourceSnapshot | None = None) -> tuple[bool, list[str]]:
        snapshot = snapshot or self.last or self.sample()
        reasons: list[str] = []
        if snapshot.disk_free_gb < self.config.min_free_disk_gb:
            reasons.append(f"free disk {snapshot.disk_free_gb:.1f} GB")
        if snapshot.free_ram_gb < self.config.min_free_ram_gb:
            reasons.append(f"free RAM {snapshot.free_ram_gb:.1f} GB")
        if snapshot.gpu_temperature_c is not None and snapshot.gpu_temperature_c >= self.config.max_temperature_c + 5:
            reasons.append(f"GPU temperature {snapshot.gpu_temperature_c:.0f} C")
        if snapshot.memory_percent >= 96:
            reasons.append(f"memory {snapshot.memory_percent:.0f}%")
        return bool(reasons), reasons

    def gpu_safe(self, snapshot: ResourceSnapshot | None = None) -> bool:
        snapshot = snapshot or self.last or self.sample()
        if snapshot.protected_foreground:
            return False
        if snapshot.cpu_percent >= 78:
            return False
        if snapshot.gpu_temperature_c is not None and snapshot.gpu_temperature_c >= self.config.max_temperature_c:
            return False
        if snapshot.gpu_memory_total_mb and snapshot.gpu_memory_used_mb:
            if snapshot.gpu_memory_used_mb / snapshot.gpu_memory_total_mb * 100 >= self.config.max_gpu_memory_percent:
                return False
        return True

    def cpu_safe(self, snapshot: ResourceSnapshot | None = None) -> bool:
        snapshot = snapshot or self.last or self.sample()
        return snapshot.cpu_percent < self.config.cpu_max_percent and snapshot.free_ram_gb >= self.config.min_free_ram_gb

    def protected_or_busy(self, snapshot: ResourceSnapshot | None = None) -> bool:
        snapshot = snapshot or self.last or self.sample()
        return snapshot.protected_foreground or snapshot.cpu_percent >= 70

    def wait_if_critical(self, stop_event: Any, pause_event: Any, logger: Any) -> ResourceSnapshot:
        while not stop_event.is_set():
            snapshot = self.sample()
            critical, reasons = self.is_critical(snapshot)
            if not critical:
                return snapshot
            logger.warning("資源保護暫停：%s", ", ".join(reasons))
            pause_event.wait(timeout=min(15.0, self.config.resource_poll_seconds * 4))
            sleep_interruptible(self.config.resource_poll_seconds, stop_event)
        return self.last or self.sample()


class BackendSelector:
    """Selects a backend only at chunk boundaries and applies hysteresis."""

    def __init__(self, config: RuntimeConfig, monitor: ResourceMonitor, available: dict[str, bool]):
        self.config = config
        self.monitor = monitor
        self.available = available
        self.current: str | None = None
        self.last_switch = 0.0
        self.gpu_block_until = 0.0

    def choose(self, preferred: str = "auto") -> tuple[str, ResourceSnapshot, list[str]]:
        snapshot = self.monitor.sample()
        now = time.monotonic()
        reasons: list[str] = []
        critical, critical_reasons = self.monitor.is_critical(snapshot)
        if critical:
            return "pause", snapshot, critical_reasons
        if snapshot.protected_foreground or snapshot.cpu_percent >= 70:
            self.gpu_block_until = max(self.gpu_block_until, now + self.config.game_cooldown_seconds)
            reasons.append("foreground protected/high-load")
        if preferred != "auto" and self.available.get(preferred, False):
            if preferred != "cuda" or (now >= self.gpu_block_until and self.monitor.gpu_safe(snapshot)):
                selected = preferred
            else:
                selected = "npu" if self.available.get("npu") else "cpu"
                reasons.append("requested GPU is protected")
        elif now < self.gpu_block_until or not self.monitor.gpu_safe(snapshot):
            selected = "npu" if self.available.get("npu") else "cpu"
            reasons.append("GPU deferred for foreground/load/temperature")
        else:
            selected = "cuda" if self.available.get("cuda") else ("npu" if self.available.get("npu") else "cpu")
        if selected == "cpu" and not self.monitor.cpu_safe(snapshot):
            return "pause", snapshot, reasons + ["CPU/RAM pressure"]
        if self.current and selected != self.current and now - self.last_switch < self.config.backend_cooldown_seconds:
            if self.available.get(self.current, False) and not (self.current == "cuda" and now < self.gpu_block_until):
                selected = self.current
                reasons.append("cooldown/hysteresis")
        if selected != self.current:
            reasons.append(f"backend transition {self.current or 'none'} -> {selected}")
            self.current = selected
            self.last_switch = now
        return selected, snapshot, reasons


def set_background_priority(logger: Any) -> None:
    if os.name != "nt":
        return
    try:
        process = psutil.Process(os.getpid())
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        logger.debug("已將背景程序設為 BELOW_NORMAL_PRIORITY_CLASS")
    except (psutil.Error, OSError, AttributeError) as exc:
        logger.warning("無法設定背景優先權：%s", exc)
