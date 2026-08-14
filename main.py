from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ayeai.benchmark import run_benchmark
from ayeai.bootstrap import ensure_runtime
from ayeai.config import RuntimeConfig, default_npu_model_dir
from ayeai.doctor import run_doctor
from ayeai.i18n import normalize_language
from ayeai.license import emit_startup_notice
from ayeai.pipeline import control_job, review_corrupt, run_many
from ayeai.state import StateError, StateStore


def configure_console_encoding() -> None:
    """Keep multilingual CLI output usable on legacy Windows code pages."""
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="AyeAI：本機中文逐字稿、高光分析與候選影片輸出工具（無 GUI、可續跑）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("inputs", nargs="*", type=Path, help="一個或多個 MP4/MKV 來源影片；可含中文、空白與長路徑")
    parser.add_argument("--doctor", action="store_true", help="檢查 FFmpeg、Python、CUDA、NPU/OpenVINO、CPU、模型與資源，並實測可用 backend")
    parser.add_argument("--benchmark", action="store_true", help="用第一個輸入影片實測各 backend，寫入 runtime/benchmark.json")
    parser.add_argument("--status", type=Path, help="讀取 job 目錄狀態")
    parser.add_argument("--pause", type=Path, help="在 job 目錄建立 pause flag")
    parser.add_argument("--resume", type=Path, help="解除 pause/stop 並續跑 job 目錄")
    parser.add_argument("--stop", type=Path, help="在 job 目錄建立安全停止 flag")
    parser.add_argument("--review", type=Path, help="處理 job 目錄的 review_corrupt 記錄")
    parser.add_argument("--action", choices=["keep", "skip", "retry", "delete"], help="搭配 --review 使用")
    parser.add_argument("--id", type=int, help="只處理指定 corrupt review id")
    parser.add_argument("--all", action="store_true", help="搭配 --review 處理所有 corrupt review")
    parser.add_argument("--output-dir", type=Path, help="輸出 job 目錄；多影片時各自建立子目錄")
    parser.add_argument("--model", default="small", help="faster-whisper 模型大小或本機模型路徑")
    parser.add_argument("--npu-model-dir", type=Path, help="OpenVINO Whisper IR 目錄；預設 models/whisper-small-int8-ov")
    parser.add_argument("--backend", choices=["auto", "cuda", "npu", "cpu"], default="auto", help="指定偏好的 backend；auto 仍受資源保護覆寫")
    parser.add_argument("--chunk-seconds", type=float, default=90.0, help="邏輯 chunk 核心長度")
    parser.add_argument("--overlap-seconds", type=float, default=6.0, help="相鄰 chunk 總 overlap")
    parser.add_argument("--queue-size", type=int, default=2, help="bounded queue 深度")
    parser.add_argument("--max-retries", type=int, default=1, help="chunk 解碼/推論失敗後重試次數")
    parser.add_argument("--cpu-threads", type=int, help="CPU backend 最大 thread 數；預設為 CPU 核心數一半且最多 4")
    parser.add_argument("--no-auto-tune", action="store_true", help="不套用 runtime/benchmark.json 建議")
    parser.add_argument("--no-startup-probe", action="store_true", help="跳過啟動模型推論自測；不建議長期使用")
    parser.add_argument("--no-full-probe", action="store_true", help="--doctor 只做依賴/裝置檢查，不載入模型實測")
    parser.add_argument("--json", action="store_true", help="以 JSON 輸出結果")
    parser.add_argument("--verbose", action="store_true", help="顯示 debug log")
    parser.add_argument("--ui", action="store_true", help="啟動鍵盤互動 CLI UI；左右鍵選單、Enter 執行")
    parser.add_argument("--lang", choices=["auto", "zh-TW", "zh-CN", "en"], default="auto", help="UI/Doctor 語言：auto、zh-TW、zh-CN、en")
    parser.add_argument("--data-dir", type=Path, help="模型、下載與 runtime manifest 的使用者資料夾")
    parser.add_argument("--no-download", action="store_true", help="不要自動下載缺少的 FFmpeg、模型或 tokenizer")
    parser.add_argument("--download-only", action="store_true", help="只補全 runtime 檔案，不處理影片")
    parser.add_argument("--license", action="store_true", help="顯示 AyeAI 專有授權與 GitHub 連結")
    return parser


def make_config(args: argparse.Namespace) -> RuntimeConfig:
    config = RuntimeConfig(
        model_size=args.model,
        backend=args.backend,
        npu_model_dir=(args.npu_model_dir or default_npu_model_dir()).resolve(),
        chunk_seconds=args.chunk_seconds,
        overlap_seconds=args.overlap_seconds,
        queue_size=args.queue_size,
        max_retries=args.max_retries,
        auto_tune=not args.no_auto_tune,
        startup_backend_probe=not args.no_startup_probe,
    )
    if args.cpu_threads is not None:
        config.cpu_threads = args.cpu_threads
    config.validate()
    return config


def print_result(result: Any, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif isinstance(result, dict):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif isinstance(result, list):
        for item in result:
            print(json.dumps(item, ensure_ascii=False, indent=2))
    else:
        print(result)


def _should_default_to_ui(args: argparse.Namespace) -> bool:
    """Make a double-clicked frozen EXE enter the keyboard UI."""
    if not getattr(sys, "frozen", False) or args.inputs or args.ui:
        return False
    explicit_cli_mode = any(
        (
            args.doctor,
            args.benchmark,
            args.status,
            args.pause,
            args.resume,
            args.stop,
            args.review,
            args.download_only,
        )
    )
    return not explicit_cli_mode


def main(argv: list[str] | None = None) -> int:
    configure_console_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.data_dir:
            os.environ["AYEAI_DATA_DIR"] = str(args.data_dir.resolve())
        language = normalize_language(args.lang)
        emit_startup_notice(language, force=args.license)
        if args.license:
            return 0
        if _should_default_to_ui(args):
            args.ui = True
        config = make_config(args)
        runtime_report = None
        needs_runtime = bool(args.download_only or args.doctor or args.benchmark or args.ui or args.inputs)
        if needs_runtime:
            runtime_report = ensure_runtime(config, auto_download=not args.no_download)
        if args.download_only:
            print_result(runtime_report or {}, args.json)
            ready = (runtime_report or {}).get("ready", {})
            required_ready = bool(ready.get("ffmpeg") and ready.get("faster-whisper"))
            if (runtime_report or {}).get("capabilities", {}).get("npu_detected"):
                required_ready = required_ready and bool(ready.get("openvino-npu"))
            return 0 if required_ready else 2
        if args.ui:
            from ayeai.ui import run_ui

            initial_input = args.inputs[0].resolve() if args.inputs else None
            return run_ui(
                initial_input=initial_input,
                config=config,
                output_dir=args.output_dir.resolve() if args.output_dir else None,
                language=language,
            )
        if args.doctor:
            doctor_report = run_doctor(config, probe=not args.no_full_probe, runtime_report=runtime_report, language=language)
            print_result(doctor_report, args.json)
            return 0 if doctor_report.get("ok") else 2
        if args.benchmark:
            if not args.inputs:
                parser.error("--benchmark 需要一個輸入影片")
            print_result(run_benchmark(args.inputs[0].resolve(), config), args.json)
            return 0
        if args.status:
            if not (args.status.resolve() / "state.db").exists():
                raise StateError(f"找不到有效 job：{args.status}")
            store = StateStore(args.status.resolve())
            try:
                print_result(store.summary(), args.json)
            finally:
                store.close()
            return 0
        for action_name in ("pause", "resume", "stop"):
            path = getattr(args, action_name)
            if path:
                print_result(control_job(path.resolve(), action_name), args.json)
                return 0
        if args.review:
            if not args.action:
                parser.error("--review 需要 --action keep|skip|retry|delete")
            print_result(review_corrupt(args.review.resolve(), args.action, args.id, args.all), args.json)
            return 0
        if not args.inputs:
            parser.print_help()
            return 2
        results = run_many([path.resolve() for path in args.inputs], config, args.output_dir.resolve() if args.output_dir else None, args.verbose)
        print_result(results, args.json)
        return 0 if all(item.get("status") in {"completed", "completed_with_warnings"} for item in results) else 2
    except (StateError, ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中斷；已完成 chunk 的 checkpoint 會保留，重新執行即可續跑。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
