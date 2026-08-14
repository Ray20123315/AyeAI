import sys
from pathlib import Path

from ayeai.bootstrap import ensure_runtime
from ayeai.config import RuntimeConfig
from ayeai.i18n import normalize_language, tr
from ayeai.license import GITHUB_URL, license_text
from main import _should_default_to_ui, build_parser


def test_language_normalization_and_translation() -> None:
    assert normalize_language("zh-Hant") == "zh-TW"
    assert normalize_language("zh-Hans") == "zh-CN"
    assert normalize_language("en-US") == "en"
    assert tr("en", "pause") == "Pause"
    assert tr("zh-CN", "resume") == "继续"


def test_license_notice_contains_repository_link() -> None:
    assert GITHUB_URL in license_text("zh-TW")
    assert GITHUB_URL in license_text("zh-CN")
    assert GITHUB_URL in license_text("en")


def test_license_notice_includes_bundled_license_file() -> None:
    assert "AyeAI PROPRIETARY SOFTWARE LICENSE" in license_text("en")


def test_runtime_detection_uses_existing_files_without_download(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AYEAI_DATA_DIR", str(tmp_path / "AyeAI-data"))
    report = ensure_runtime(RuntimeConfig(), auto_download=False)
    assert "capabilities" in report
    assert set(report["ready"]) >= {"ffmpeg", "faster-whisper", "openvino-npu"}


def test_frozen_exe_defaults_to_keyboard_ui(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert _should_default_to_ui(build_parser().parse_args([])) is True
    assert _should_default_to_ui(build_parser().parse_args(["--doctor"])) is False
    assert _should_default_to_ui(build_parser().parse_args(["--download-only"])) is False
