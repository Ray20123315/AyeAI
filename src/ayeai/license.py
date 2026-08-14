"""AyeAI proprietary notice shown by the packaged executable."""

from __future__ import annotations

import sys
from pathlib import Path

from .i18n import normalize_language


GITHUB_URL = "https://github.com/Ray20123315/AyeAI"
LICENSE_NAME = "AyeAI Proprietary License — All Rights Reserved"


def _bundled_license_text() -> str:
    """Read the exact LICENSE bundled beside the frozen application."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "LICENSE")
    candidates.append(Path(__file__).resolve().parents[2] / "LICENSE")
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
    return ""


def _with_bundled_license(notice: str) -> str:
    full_license = _bundled_license_text()
    if not full_license:
        return notice
    return f"{notice}\n\n{'-' * 78}\nBundled LICENSE / 完整授權條款\n{'-' * 78}\n{full_license}"


def license_text(language: str | None = None) -> str:
    lang = normalize_language(language)
    if lang == "zh-CN":
        return _with_bundled_license(
            "AyeAI 专有软件许可声明（保留所有权利）\n"
            f"项目与完整条款：{GITHUB_URL}\n"
            "本软件、源代码、模型配置与打包文件均受专有许可保护。除许可明确允许的个人内部使用外，"
            "未经版权持有人书面许可，不得复制、修改、再发布、出售、出租、再许可、反向工程或提供网络服务。\n"
            "使用本 EXE 即表示你已阅读并同意该许可；第三方组件仍受其各自许可证约束。"
        )
    if lang == "en":
        return _with_bundled_license(
            "AyeAI Proprietary License (All Rights Reserved)\n"
            f"Project and full terms: {GITHUB_URL}\n"
            "The software, source code, model configuration, and packaged files are proprietary. Except for"
            " personal internal use expressly allowed by the license, copying, modifying, redistributing,"
            " selling, renting, sublicensing, reverse engineering, or providing it as a hosted service requires"
            " written permission from the copyright holder.\n"
            "Using this EXE means you have read and accepted the license; third-party components remain under"
            " their own licenses."
        )
    return _with_bundled_license(
        "AyeAI 專有軟體授權聲明（保留所有權利）\n"
        f"專案與完整條款：{GITHUB_URL}\n"
        "本軟體、原始碼、模型設定與打包檔案均受專有授權保護。除授權明確允許的個人內部使用外，"
        "未經版權持有人書面許可，不得複製、修改、再發布、販售、出租、再授權、逆向工程或提供網路服務。\n"
        "使用本 EXE 即表示你已閱讀並同意本授權；第三方元件仍受其各自授權條款約束。"
    )


def emit_startup_notice(language: str | None = None, *, force: bool = False) -> None:
    if force or bool(getattr(sys, "frozen", False)):
        print("=" * 78)
        print(license_text(language))
        print("=" * 78)
