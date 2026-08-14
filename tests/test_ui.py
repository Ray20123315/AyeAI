import sys
from pathlib import Path

from ayeai import ui
from ayeai.ui import menu_move


def test_horizontal_menu_wraps_left_and_right() -> None:
    assert menu_move(0, "left", 4) == 3
    assert menu_move(3, "right", 4) == 0
    assert menu_move(1, "other", 4) == 1


def test_bat_starts_ui_for_video_and_keeps_venv_isolated() -> None:
    content = Path("AyeAI.bat").read_text(encoding="utf-8")
    assert ".venv\\Scripts\\python.exe" in content
    assert "--ui" in content
    assert "%~x1" in content


def test_horizontal_menu_accepts_right_then_enter(monkeypatch) -> None:
    keys = iter(["right", "enter"])
    monkeypatch.setattr(ui, "_clear_screen", lambda: None)
    monkeypatch.setattr(ui, "_read_key", lambda timeout=None: next(keys))
    assert ui.select_horizontal("test", ("one", "two")) == 1


def test_windows_arrow_scan_code_maps_to_right(monkeypatch) -> None:
    class FakeMsvcrt:
        calls = 0

        @staticmethod
        def getwch():
            value = "\xe0" if FakeMsvcrt.calls == 0 else "M"
            FakeMsvcrt.calls += 1
            return value

        @staticmethod
        def kbhit():
            return True

    monkeypatch.setattr(ui.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt)
    assert ui._read_key(timeout=0.1) == "right"


def test_windows_clear_screen_uses_native_console_command(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(ui.os, "name", "nt")
    monkeypatch.setattr(ui.os, "system", calls.append)
    ui._clear_screen()
    assert calls == ["cls"]


def test_run_ui_can_leave_with_right_and_enter(monkeypatch, tmp_path: Path) -> None:
    keys = iter(["right", "right", "right", "enter"])
    monkeypatch.setattr(ui, "_clear_screen", lambda: None)
    monkeypatch.setattr(ui, "_read_key", lambda timeout=None: next(keys))
    config = ui.RuntimeConfig(npu_model_dir=tmp_path / "npu")
    assert ui.run_ui(config=config) == 0
