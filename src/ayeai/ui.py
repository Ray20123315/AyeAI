"""Windows-only keyboard UI for the resumable AyeAI CLI pipeline."""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .config import RuntimeConfig
from .doctor import run_doctor
from .i18n import normalize_language, tr
from .pipeline import JobRunner, control_job
from .state import StateError, StateStore


_STARTUP_NOTICE_PENDING = bool(getattr(sys, "frozen", False))


def menu_move(index: int, key: str, size: int) -> int:
    """Return the next horizontal menu index; kept pure for deterministic tests."""

    if size <= 0:
        return 0
    if key in {"left", "up"}:
        return (index - 1) % size
    if key in {"right", "down"}:
        return (index + 1) % size
    return index % size


def _enable_ansi() -> None:
    if os.name == "nt":
        # Ask cmd.exe to enable VT sequences without changing PATH or any
        # persistent system setting.
        os.system("")


def _clear_screen() -> None:
    if os.name == "nt":
        # Some Windows terminals do not interpret VT/ANSI sequences.  Use
        # the native console command so arrow-key redraws never accumulate.
        os.system("cls")
        return
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def _read_key(timeout: float | None = None) -> str:
    """Read one key without Enter; Windows arrow scan codes become actions."""

    if os.name != "nt":
        if timeout is not None:
            time.sleep(timeout)
            return "timeout"
        value = input()
        return "enter" if not value else value[:1].lower()

    import msvcrt

    if timeout is not None:
        deadline = time.monotonic() + max(0.0, timeout)
        while not msvcrt.kbhit():
            if time.monotonic() >= deadline:
                return "timeout"
            time.sleep(0.05)
    first = msvcrt.getwch()
    if first in ("\x00", "\xe0"):
        second = msvcrt.getwch()
        return {"K": "left", "M": "right", "H": "up", "P": "down"}.get(second, "other")
    if first in ("\r", "\n"):
        return "enter"
    if first == "\x1b":
        return "esc"
    if first == "\x03":
        return "ctrl-c"
    return "other"


def select_horizontal(
    title: str,
    options: Sequence[str],
    *,
    selected: int = 0,
    language: str = "zh-TW",
    status_supplier: Callable[[], str] | None = None,
    notice_supplier: Callable[[], str] | None = None,
) -> int | None:
    """Render a left/right/Enter menu and return the selected option."""

    if not options:
        raise ValueError("menu options cannot be empty")
    index = selected % len(options)
    while True:
        global _STARTUP_NOTICE_PENDING
        if _STARTUP_NOTICE_PENDING:
            _STARTUP_NOTICE_PENDING = False
        else:
            _clear_screen()
        print(tr(language, "app_title"))
        print("=" * 64)
        print(title)
        if status_supplier:
            try:
                status = status_supplier()
            except Exception as exc:  # status must never take down the UI
                status = tr(language, "status_read_failed", error=exc)
            if status:
                print(status)
        if notice_supplier:
            notice = notice_supplier()
            if notice:
                print(f"{tr(language, 'notice_prefix', default='Notice')}: {notice}")
        print()
        labels = []
        for position, option in enumerate(options):
            marker = ">" if position == index else " "
            labels.append(f"[{marker}] {option}")
        print("    ".join(labels))
        print()
        print(tr(language, "instructions"))
        key = _read_key(timeout=0.8 if status_supplier else None)
        if key == "timeout":
            continue
        if key == "ctrl-c":
            raise KeyboardInterrupt
        if key == "esc":
            return None
        if key in {"left", "right", "up", "down"}:
            index = menu_move(index, key, len(options))
        elif key == "enter":
            return index


@dataclass
class JobSession:
    input_path: Path
    job_dir: Path
    runner: JobRunner | None
    thread: threading.Thread | None = None
    result: dict[str, Any] | None = None
    error: BaseException | None = None

    def start(self) -> None:
        if self.runner is None:
            raise StateError("既有 Job 不能直接啟動 Runner")

        def worker() -> None:
            try:
                self.result = self.runner.run()
            except BaseException as exc:  # keep the UI alive and show the error
                self.error = exc
                try:
                    self.runner.logger.exception("interactive runner failed")
                except Exception:
                    pass

        self.thread = threading.Thread(target=worker, name="ayeai-interactive-runner", daemon=False)
        self.thread.start()

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())


def _clean_path_input(value: str) -> Path:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1]
    return Path(cleaned).expanduser().resolve()


def _start_session(input_path: Path, config: RuntimeConfig, output_dir: Path | None) -> JobSession:
    if not input_path.exists() or not input_path.is_file():
        raise FileNotFoundError(f"找不到影片檔：{input_path}")
    runner = JobRunner(input_path, config, output_dir=output_dir, multiple=False, verbose=False, console=False)
    session = JobSession(input_path=input_path, job_dir=runner.job_dir, runner=runner)
    session.start()
    return session


def _read_summary(job_dir: Path) -> dict[str, Any]:
    if not (job_dir / "state.db").exists():
        raise StateError(f"找不到有效 job：{job_dir}")
    store = StateStore(job_dir)
    try:
        return store.summary()
    finally:
        store.close()


def _format_summary(summary: dict[str, Any], session: JobSession | None = None, language: str = "zh-TW") -> str:
    chunks = summary.get("chunks") or {}
    done = summary.get("completed_or_isolated", 0)
    total = summary.get("total_chunks", 0)
    progress = float(summary.get("progress", 0.0)) * 100
    lines = [
        tr(language, "status_line", status=summary.get("status", "unknown"), progress=progress, done=done, total=total),
        tr(
            language,
            "backend_line",
            backend=summary.get("last_backend") or "-",
            done=chunks.get("done", 0),
            retry=chunks.get("retry", 0),
            corrupt=chunks.get("corrupt", 0),
        ),
        tr(
            language,
            "details_line",
            highlights=summary.get("confirmed_highlights", 0),
            corrupt_review=summary.get("corrupt_review", 0),
            updated=summary.get("updated_at", "-"),
        ),
    ]
    if session and session.error:
        lines.append(tr(language, "runner_error", error=session.error))
    if session:
        lines.append(tr(language, "input_line", path=session.input_path))
        lines.append(tr(language, "output_line", path=session.job_dir))
    return "\n".join(lines)


def _session_status(session: JobSession | None, language: str = "zh-TW") -> str:
    if not session:
        return tr(language, "no_job")
    try:
        return _format_summary(_read_summary(session.job_dir), session, language)
    except Exception as exc:
        return f"Job: {session.job_dir}\n{tr(language, 'status_read_failed', error=exc)}"


def _wait_for_key(message: str | None = None, language: str = "zh-TW") -> None:
    print()
    print(message or tr(language, "press_key"))
    while True:
        key = _read_key()
        if key == "ctrl-c":
            raise KeyboardInterrupt
        if key != "other":
            return


def _show_status(session: JobSession, language: str) -> None:
    _clear_screen()
    print(tr(language, "job_status"))
    print("=" * 64)
    print(_session_status(session, language))
    _wait_for_key(language=language)


def _show_log(session: JobSession, language: str, lines: int = 24) -> None:
    _clear_screen()
    print("最近 Log")
    print("=" * 64)
    log_path = session.job_dir / "logs" / "ayeai.log"
    if log_path.exists():
        content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        print("\n".join(content[-lines:]) or tr(language, "no_log"))
    else:
        print(tr(language, "full_log_missing", path=log_path))
    _wait_for_key(language=language)


def _stop_and_wait(session: JobSession, language: str, *, request: bool = True) -> None:
    if not session.running:
        return
    if request:
        try:
            control_job(session.job_dir, "stop")
        except Exception as exc:
            print(f"{tr(language, 'stop_notice')}: {exc}")
            return
    while session.running:
        _clear_screen()
        print(tr(language, "stop_wait"))
        print(_session_status(session, language))
        print(tr(language, "please_wait"))
        session.thread.join(timeout=0.8)  # type: ignore[union-attr]


def _control_session(session: JobSession, language: str) -> None:
    notice = ""

    def get_notice() -> str:
        return notice

    while True:
        choice = select_horizontal(
            tr(language, "control_job"),
            tuple(tr(language, key) for key in ("pause", "resume", "stop", "status", "recent_log", "back")),
            language=language,
            status_supplier=lambda: _session_status(session, language),
            notice_supplier=get_notice,
        )
        if choice is None or choice == 5:
            return
        if choice == 3:
            _show_status(session, language)
            continue
        if choice == 4:
            _show_log(session, language)
            continue
        action = ("pause", "resume", "stop")[choice]
        try:
            summary = control_job(session.job_dir, action)
            notice = {
                "pause": tr(language, "pause_notice"),
                "resume": tr(language, "resume_notice"),
                "stop": tr(language, "stop_notice"),
            }[action]
            if action == "stop" and session.running:
                _stop_and_wait(session, language, request=False)
            else:
                notice = f"{notice}；{summary.get('status', '')}"
        except (StateError, ValueError, OSError) as exc:
            notice = f"控制失敗：{exc}"


def _doctor_screen(config: RuntimeConfig, language: str) -> None:
    _clear_screen()
    print(tr(language, "doctor_title"))
    print("=" * 64)
    try:
        report = run_doctor(config, probe=True)
        print(tr(language, "total_result", value=tr(language, "doctor_ok" if report.get("ok") else "doctor_attention")))
        print(f"{tr(language, 'python')}：{report.get('python', {}).get('version')}")
        tools = report.get("tools", {})
        print(f"{tr(language, 'ffmpeg')}：{'OK' if tools.get('ffmpeg', {}).get('ok') else 'FAIL'}    "
              f"{tr(language, 'ffprobe')}：{'OK' if tools.get('ffprobe', {}).get('ok') else 'FAIL'}")
        print(f"{tr(language, 'gpu')}：{report.get('resources', {}).get('gpu')}")
        for name, status in (report.get("backends") or {}).items():
            print(f"{name.upper():4s}：{tr(language, 'transcription')}={'OK' if status.get('transcription_ok') else 'FAIL'}  "
                  f"{tr(language, 'hardware')}={'OK' if status.get('hardware_ok') else 'FAIL'}  {status.get('detail') or status.get('error') or ''}")
    except Exception as exc:
        print(tr(language, "doctor_failed", error=exc))
    _wait_for_key(language=language)


def run_ui(
    *,
    initial_input: Path | None = None,
    config: RuntimeConfig | None = None,
    output_dir: Path | None = None,
    language: str | None = None,
) -> int:
    """Run the interactive CLI UI and return a process-style exit code."""

    if os.name != "nt":
        print(tr(language, "not_windows"), file=sys.stderr)
        return 2
    active_language = normalize_language(language)
    active_config = config or RuntimeConfig()
    active_config.validate()
    _enable_ansi()
    session: JobSession | None = None
    if initial_input:
        try:
            session = _start_session(initial_input.resolve(), active_config, output_dir)
        except (StateError, ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
            _clear_screen()
            print(tr(active_language, "start_failed", error=exc))
            _wait_for_key(language=active_language)

    try:
        while True:
            if session:
                choice = select_horizontal(
                    tr(active_language, "main_menu_active"),
                    tuple(tr(active_language, key) for key in ("control_job", "add_video", "doctor", "exit")),
                    language=active_language,
                    status_supplier=lambda: _session_status(session, active_language),
                )
                if choice == 0:
                    _control_session(session, active_language)
                elif choice == 1:
                    if session.running:
                        _clear_screen()
                        print(tr(active_language, "job_running"))
                        _wait_for_key(language=active_language)
                        continue
                    raw = input(tr(active_language, "input_video"))
                    if not raw.strip():
                        continue
                    try:
                        session = _start_session(_clean_path_input(raw), active_config, output_dir)
                    except (StateError, ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
                        print(tr(active_language, "start_failed", error=exc))
                        _wait_for_key(language=active_language)
                elif choice == 2:
                    _doctor_screen(active_config, active_language)
                else:
                    _stop_and_wait(session, active_language)
                    return 0
            else:
                choice = select_horizontal(
                    tr(active_language, "main_menu"),
                    tuple(tr(active_language, key) for key in ("add_video", "existing_job", "doctor", "exit")),
                    language=active_language,
                )
                if choice == 0:
                    raw = input(tr(active_language, "input_video"))
                    if not raw.strip():
                        continue
                    try:
                        session = _start_session(_clean_path_input(raw), active_config, output_dir)
                    except (StateError, ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
                        print(tr(active_language, "start_failed", error=exc))
                        _wait_for_key(language=active_language)
                elif choice == 1:
                    raw = input(tr(active_language, "input_job"))
                    if not raw.strip():
                        continue
                    job_dir = _clean_path_input(raw)
                    try:
                        summary = _read_summary(job_dir)
                        session = JobSession(
                            input_path=Path(summary["input_path"]),
                            job_dir=job_dir,
                            runner=None,
                        )
                    except (StateError, ValueError, OSError, KeyError) as exc:
                        print(tr(active_language, "open_failed", error=exc))
                        _wait_for_key(language=active_language)
                elif choice == 2:
                    _doctor_screen(active_config, active_language)
                else:
                    return 0
    except KeyboardInterrupt:
        if session:
            _stop_and_wait(session, active_language)
        print(f"\n{tr(active_language, 'safe_stopped')}")
        return 130
