"""Small dependency-free translation layer for the terminal UI and bootstrap."""

from __future__ import annotations

import ctypes
import locale
import os
from typing import Any


LANGUAGES = ("zh-TW", "zh-CN", "en")

MESSAGES: dict[str, dict[str, str]] = {
    "app_title": {
        "zh-TW": "AyeAI 互動 CLI",
        "zh-CN": "AyeAI 交互 CLI",
        "en": "AyeAI Interactive CLI",
    },
    "instructions": {
        "zh-TW": "左右鍵選擇    Enter 執行    Esc 返回/離開",
        "zh-CN": "左右键选择    Enter 执行    Esc 返回/离开",
        "en": "Left/Right select    Enter run    Esc back/exit",
    },
    "notice_prefix": {"zh-TW": "訊息", "zh-CN": "消息", "en": "Notice"},
    "main_menu": {"zh-TW": "主選單", "zh-CN": "主菜单", "en": "Main menu"},
    "main_menu_active": {"zh-TW": "主選單（目前已有 Job）", "zh-CN": "主菜单（目前已有 Job）", "en": "Main menu (job loaded)"},
    "control_job": {"zh-TW": "控制目前 Job", "zh-CN": "控制当前 Job", "en": "Control current job"},
    "add_video": {"zh-TW": "新增影片", "zh-CN": "新增视频", "en": "Add video"},
    "existing_job": {"zh-TW": "控制既有 Job", "zh-CN": "控制已有 Job", "en": "Open existing job"},
    "doctor": {"zh-TW": "Doctor", "zh-CN": "Doctor", "en": "Doctor"},
    "exit": {"zh-TW": "離開", "zh-CN": "离开", "en": "Exit"},
    "pause": {"zh-TW": "暫停", "zh-CN": "暂停", "en": "Pause"},
    "resume": {"zh-TW": "續跑", "zh-CN": "继续", "en": "Resume"},
    "stop": {"zh-TW": "停止", "zh-CN": "停止", "en": "Stop"},
    "status": {"zh-TW": "狀態", "zh-CN": "状态", "en": "Status"},
    "recent_log": {"zh-TW": "最近 Log", "zh-CN": "最近 Log", "en": "Recent log"},
    "back": {"zh-TW": "返回", "zh-CN": "返回", "en": "Back"},
    "job_status": {"zh-TW": "AyeAI Job 狀態", "zh-CN": "AyeAI Job 状态", "en": "AyeAI job status"},
    "doctor_title": {"zh-TW": "AyeAI Doctor（完整 backend 實測）", "zh-CN": "AyeAI Doctor（完整 backend 实测）", "en": "AyeAI Doctor (full backend probe)"},
    "input_video": {"zh-TW": "輸入 MP4/MKV 完整路徑（直接 Enter 返回）：", "zh-CN": "输入 MP4/MKV 完整路径（直接 Enter 返回）：", "en": "Video path (press Enter to go back): "},
    "input_job": {"zh-TW": "輸入 job 資料夾完整路徑（直接 Enter 返回）：", "zh-CN": "输入 job 文件夹完整路径（直接 Enter 返回）：", "en": "Job folder path (press Enter to go back): "},
    "press_key": {"zh-TW": "按任意鍵返回…", "zh-CN": "按任意键返回…", "en": "Press any key to return…"},
    "no_job": {"zh-TW": "目前沒有執行中的 Job。", "zh-CN": "目前没有运行中的 Job。", "en": "No job is currently running."},
    "status_read_failed": {"zh-TW": "狀態讀取失敗：{error}", "zh-CN": "状态读取失败：{error}", "en": "Could not read status: {error}"},
    "status_line": {"zh-TW": "狀態：{status}    進度：{progress:.0f}% ({done}/{total} chunks)", "zh-CN": "状态：{status}    进度：{progress:.0f}% ({done}/{total} chunks)", "en": "Status: {status}    Progress: {progress:.0f}% ({done}/{total} chunks)"},
    "backend_line": {"zh-TW": "目前 backend：{backend}    chunks：done={done} retry={retry} corrupt={corrupt}", "zh-CN": "当前 backend：{backend}    chunks：done={done} retry={retry} corrupt={corrupt}", "en": "Backend: {backend}    chunks: done={done} retry={retry} corrupt={corrupt}"},
    "details_line": {"zh-TW": "高光：{highlights}    損壞待檢：{corrupt_review}    更新：{updated}", "zh-CN": "高光：{highlights}    损坏待检：{corrupt_review}    更新：{updated}", "en": "Highlights: {highlights}    Corrupt review: {corrupt_review}    Updated: {updated}"},
    "input_line": {"zh-TW": "輸入：{path}", "zh-CN": "输入：{path}", "en": "Input: {path}"},
    "output_line": {"zh-TW": "輸出：{path}", "zh-CN": "输出：{path}", "en": "Output: {path}"},
    "runner_error": {"zh-TW": "Runner 錯誤：{error}", "zh-CN": "Runner 错误：{error}", "en": "Runner error: {error}"},
    "start_failed": {"zh-TW": "啟動影片失敗：{error}", "zh-CN": "启动视频失败：{error}", "en": "Could not start video: {error}"},
    "open_failed": {"zh-TW": "讀取 Job 失敗：{error}", "zh-CN": "读取 Job 失败：{error}", "en": "Could not open job: {error}"},
    "job_running": {"zh-TW": "目前 Job 尚在執行；請先等它完成，或到控制選單選擇停止。", "zh-CN": "当前 Job 仍在运行；请先等待完成，或在控制菜单选择停止。", "en": "The current job is still running; wait for it or stop it first."},
    "pause_notice": {"zh-TW": "已要求暫停（目前 chunk 結束後生效）", "zh-CN": "已请求暂停（当前 chunk 结束后生效）", "en": "Pause requested (applies after the current chunk)"},
    "resume_notice": {"zh-TW": "已要求續跑", "zh-CN": "已请求继续", "en": "Resume requested"},
    "stop_notice": {"zh-TW": "已要求安全停止", "zh-CN": "已请求安全停止", "en": "Safe stop requested"},
    "stop_wait": {"zh-TW": "正在安全停止；目前 chunk 結束或回到檢查點後會停止。", "zh-CN": "正在安全停止；当前 chunk 结束或回到检查点后会停止。", "en": "Stopping safely; it will stop after the current chunk or checkpoint."},
    "please_wait": {"zh-TW": "請稍候…", "zh-CN": "请稍候…", "en": "Please wait…"},
    "doctor_ok": {"zh-TW": "OK", "zh-CN": "OK", "en": "OK"},
    "doctor_attention": {"zh-TW": "需要處理", "zh-CN": "需要处理", "en": "Needs attention"},
    "doctor_failed": {"zh-TW": "Doctor 失敗：{error}", "zh-CN": "Doctor 失败：{error}", "en": "Doctor failed: {error}"},
    "full_log_missing": {"zh-TW": "尚無 log：{path}", "zh-CN": "暂无 log：{path}", "en": "No log yet: {path}"},
    "no_log": {"zh-TW": "（尚無 log）", "zh-CN": "（暂无 log）", "en": "(no log yet)"},
    "safe_stopped": {"zh-TW": "已安全停止互動 UI。", "zh-CN": "已安全停止交互 UI。", "en": "Interactive UI stopped safely."},
    "not_windows": {"zh-TW": "互動 CLI UI 需要 Windows；請使用原本的 python main.py CLI。", "zh-CN": "交互 CLI UI 需要 Windows；请使用原本的 python main.py CLI。", "en": "The interactive CLI UI requires Windows; use the regular python main.py CLI."},
    "backend": {"zh-TW": "backend", "zh-CN": "backend", "en": "backend"},
    "transcription": {"zh-TW": "transcription", "zh-CN": "transcription", "en": "transcription"},
    "hardware": {"zh-TW": "hardware", "zh-CN": "hardware", "en": "hardware"},
    "gpu": {"zh-TW": "GPU", "zh-CN": "GPU", "en": "GPU"},
    "python": {"zh-TW": "Python", "zh-CN": "Python", "en": "Python"},
    "ffmpeg": {"zh-TW": "FFmpeg", "zh-CN": "FFmpeg", "en": "FFmpeg"},
    "ffprobe": {"zh-TW": "ffprobe", "zh-CN": "ffprobe", "en": "ffprobe"},
    "total_result": {"zh-TW": "總結果：{value}", "zh-CN": "总结果：{value}", "en": "Overall: {value}"},
}


def normalize_language(value: str | None) -> str:
    if not value or value.lower() == "auto":
        return detect_language()
    normalized = value.replace("_", "-").lower()
    if normalized in {"zh-tw", "zh-hant", "zh-hk", "zh-mo"}:
        return "zh-TW"
    if normalized in {"zh-cn", "zh-hans", "zh-sg", "zh-my"}:
        return "zh-CN"
    if normalized.startswith("zh"):
        return "zh-TW"
    return "en" if normalized.startswith("en") else "en"


def detect_language() -> str:
    override = os.environ.get("AYEAI_LANG")
    if override and override.lower() != "auto":
        return normalize_language(override)
    if os.name == "nt":
        try:
            buffer = ctypes.create_unicode_buffer(85)
            ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer))
            if buffer.value:
                return normalize_language(buffer.value)
        except Exception:
            pass
    try:
        language = locale.getlocale()[0] or locale.getdefaultlocale()[0] or ""
    except Exception:
        language = ""
    return normalize_language(language)


def tr(language: str | None, key: str, **kwargs: Any) -> str:
    lang = normalize_language(language)
    template = MESSAGES.get(key, {}).get(lang) or MESSAGES.get(key, {}).get("en") or key
    return template.format(**kwargs) if kwargs else template
