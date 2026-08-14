from __future__ import annotations

import json
import os
import queue
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .backends import BackendManager, BackendUnavailable
from .chunks import build_logical_chunks
from .config import RuntimeConfig, apply_auto_tune, default_npu_model_dir, project_root
from .highlighter import Highlight, analyze_highlights
from .media import MediaError, extract_pcm, export_candidate, export_review_segment, ffprobe_json, summarize_media, validate_candidate, validate_source
from .resources import BackendSelector, ResourceMonitor, set_background_priority
from .state import JobLock, StateError, StateStore
from .utils import atomic_write_json, atomic_write_text, file_identity, human_seconds, now_iso, safe_slug, setup_logging, sha256_file, sleep_interruptible


def _normalized_text(text: str) -> str:
    return "".join(char.lower() for char in text if not char.isspace() and char not in "，。！？!?、,.．…~～")


def merge_transcript(rows: Iterable[Any], duration: float, logger: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (float(item["start"]), float(item["end"]), int(item["id"]))):
        start = float(row["start"])
        end = float(row["end"])
        text = str(row["text"] or "").strip()
        if not text:
            continue
        if start < 0 or end > duration or end <= start:
            logger.warning("修正可疑 timestamp：%.3f–%.3f / duration %.3f", start, end, duration)
            start = max(0.0, min(duration, start))
            end = max(start + 0.05, min(duration, end if end > start else start + 0.5))
        item = {
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "confidence": row["confidence"],
            "no_speech_prob": row["no_speech_prob"],
        }
        duplicate = False
        for previous in reversed(merged[-6:]):
            overlap = min(item["end"], previous["end"]) - max(item["start"], previous["start"])
            if overlap >= -0.15 and _normalized_text(item["text"]) == _normalized_text(previous["text"]):
                previous["start"] = min(previous["start"], item["start"])
                previous["end"] = max(previous["end"], item["end"])
                duplicate = True
                break
        if not duplicate:
            # Different words in overlap are retained intentionally; preserving questionable speech is safer.
            merged.append(item)
    return merged


def _srt_timestamp(seconds: float) -> str:
    milliseconds = int(max(0.0, seconds) * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_transcript_files(job_dir: Path, segments: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    payload = {"metadata": metadata, "segments": segments}
    atomic_write_json(job_dir / "transcript" / "transcript.json", payload)
    atomic_write_text(job_dir / "transcript" / "transcript.txt", "\n".join(item["text"] for item in segments) + "\n")
    srt_parts: list[str] = []
    for index, item in enumerate(segments, 1):
        srt_parts.extend([str(index), f"{_srt_timestamp(item['start'])} --> {_srt_timestamp(item['end'])}", item["text"], ""])
    atomic_write_text(job_dir / "transcript" / "transcript.srt", "\n".join(srt_parts))


def resolve_job_dir(input_path: Path, output_dir: Path | None, input_hash: str, config_hash: str, multiple: bool = False) -> Path:
    if output_dir:
        base = output_dir / f"{safe_slug(input_path.stem)}_ayeai" if multiple else output_dir
    else:
        base = input_path.parent / f"{safe_slug(input_path.stem)}_ayeai"
    db_path = base / "state.db"
    if not db_path.exists():
        return base
    try:
        import sqlite3

        connection = sqlite3.connect(db_path)
        row = connection.execute("SELECT input_hash, config_hash FROM job WHERE id=1").fetchone()
        connection.close()
        if row and row[0] == input_hash and row[1] == config_hash:
            return base
        if row and row[0] == input_hash:
            return base.with_name(f"{base.name}_cfg_{config_hash[:8]}")
    except Exception:
        pass
    return base.with_name(f"{base.name}_input_{input_hash[:8]}")


class JobRunner:
    def __init__(
        self,
        input_path: Path,
        config: RuntimeConfig,
        output_dir: Path | None = None,
        multiple: bool = False,
        verbose: bool = False,
        console: bool = True,
    ):
        self.input_path = input_path.resolve()
        self.config = apply_auto_tune(config)
        self.config.validate()
        self.verbose = verbose
        self._stop = threading.Event()
        self._signal_installed = False
        self._logger = setup_logging(verbose=verbose, console=console)
        # ffprobe is intentionally first; corrupt or unsupported media never reaches model loading.
        self.probe, self.media = validate_source(self.input_path)
        self.identity = file_identity(self.input_path)
        self.job_dir = resolve_job_dir(self.input_path, output_dir, self.identity["sha256"], self.config.config_hash(), multiple)
        self.logger = setup_logging(self.job_dir / "logs" / "ayeai.log", verbose=verbose, console=console)
        self.store = StateStore(self.job_dir)
        self.job_id = f"{safe_slug(self.input_path.stem)}-{self.identity['sha256'][:10]}"
        self.store.initialize_job(
            job_id=self.job_id,
            input_path=self.input_path,
            input_hash=self.identity["sha256"],
            config_hash=self.config.config_hash(),
            config_json=self.config.normalized(),
            media_json=self.media,
        )
        atomic_write_json(self.job_dir / "input.json", {"identity": self.identity, "probe": self.probe, "summary": self.media})
        chunk_plan = build_logical_chunks(self.input_path, self.media["duration"], self.config)
        self.store.insert_chunks(chunk_plan)
        self.backend_manager: BackendManager | None = None
        self.monitor: ResourceMonitor | None = None
        self.selector: BackendSelector | None = None

    def _install_signal_handlers(self) -> None:
        if self._signal_installed:
            return
        try:
            signal.signal(signal.SIGINT, self._handle_sigint)
            signal.signal(signal.SIGTERM, self._handle_sigint)
            self._signal_installed = True
        except (ValueError, OSError):
            pass

    def _handle_sigint(self, signum: int, _frame: Any) -> None:
        if not self._stop.is_set():
            self.logger.warning("收到中斷，完成目前 chunk 後安全停止；再次執行同一命令即可續跑")
            self.store.log_event("WARNING", "stop_requested", {"signal": signum})
            self._stop.set()
            self.store.set_job_status("stopping")
        else:
            self.logger.warning("已再次收到中斷；保留 checkpoint 並退出")

    def _control_wait(self) -> bool:
        pause_flag = self.job_dir / "PAUSE"
        stop_flag = self.job_dir / "STOP"
        announced_pause = False
        while pause_flag.exists() and not self._stop.is_set():
            if not announced_pause:
                self.logger.warning("目前為 PAUSED；刪除 %s 或執行 --resume 後繼續", pause_flag)
                self.store.set_job_status("paused")
                self.store.log_event("INFO", "paused", {})
                announced_pause = True
            self.store.write_status_json()
            time.sleep(1.0)
        if stop_flag.exists():
            self._stop.set()
        if announced_pause and not self._stop.is_set():
            self.logger.info("pause 已解除，繼續處理")
            self.store.set_job_status("running")
            self.store.log_event("INFO", "resumed", {})
        return not self._stop.is_set()

    def _startup_backends(self) -> None:
        self.backend_manager = BackendManager(self.config, self.logger)
        if self.config.startup_backend_probe:
            self.logger.info("啟動時實測 CUDA / NPU / CPU backend；首次可能需要模型載入")
            statuses = self.backend_manager.probe_all(np.zeros(16000, dtype=np.float32))
            atomic_write_json(self.job_dir / "backend_probe.json", self.backend_manager.report())
            for name, status in statuses.items():
                level = self.logger.info if status.transcription_ok else self.logger.warning
                level("backend %-4s ready=%s hardware=%s detail=%s", name, status.transcription_ok, status.hardware_ok, status.detail or status.error)
        else:
            for status in self.backend_manager.statuses.values():
                status.transcription_ok = status.hardware_ok and status.import_ok
            npu_status = self.backend_manager.statuses["npu"]
            if npu_status.transcription_ok:
                npu_dir = Path(self.config.npu_model_dir or default_npu_model_dir())
                npu_status.transcription_ok = npu_dir.exists() and any(npu_dir.iterdir())
        available = self.backend_manager.available_for_work()
        if not any(available.values()):
            raise BackendUnavailable("沒有可用的逐字稿 backend；請先執行 --doctor 檢查套件、模型與裝置")
        self.monitor = ResourceMonitor(self.config, npu_available=available.get("npu", False), manual_busy_flag=self.job_dir / "RESOURCE_BUSY")
        self.selector = BackendSelector(self.config, self.monitor, available)

    def _checkpoint_path(self, chunk_id: int) -> Path:
        return self.job_dir / "checkpoints" / f"chunk_{chunk_id:06d}.json"

    def _process_chunk(self, row: Any) -> bool:
        assert self.backend_manager is not None and self.monitor is not None and self.selector is not None
        chunk_id = int(row["id"])
        backend, snapshot, reasons = self.selector.choose(self.config.backend)
        self.store.write_status_json()
        if backend == "pause":
            self.logger.warning("資源保護暫停 chunk %d：%s", chunk_id, ", ".join(reasons))
            self.store.set_job_status("paused_resource")
            for _ in range(60):
                if self._stop.is_set():
                    return False
                sleep_interruptible(5.0, self._stop)
                backend, snapshot, reasons = self.selector.choose(self.config.backend)
                if backend != "pause":
                    self.store.set_job_status("running")
                    break
            if backend == "pause":
                return False
        self.store.set_last_backend(backend)
        if reasons:
            self.store.log_event("INFO", "backend_decision", {"chunk_id": chunk_id, "backend": backend, "reasons": reasons, "resources": snapshot.as_dict()})
        claimed = self.store.claim_chunk(chunk_id, backend)
        start = float(claimed["start"])
        end = float(claimed["end"])
        audio: np.ndarray | None = None
        effective_backend = backend
        try:
            audio = extract_pcm(self.input_path, start, end - start)
            try:
                segments = self.backend_manager.transcribe(backend, audio, start)
            except BackendUnavailable as primary_error:
                # A backend can fail after its startup probe (driver reset, game takeover,
                # transient NPU compile error). Preserve the chunk and try the next safe
                # backend immediately instead of turning a recoverable event into corruption.
                fallback_candidates = ["npu", "cpu"] if backend == "cuda" else (["cpu"] if backend == "npu" else [])
                fallback_error = str(primary_error)
                for candidate in fallback_candidates:
                    candidate_status = self.backend_manager.statuses.get(candidate)
                    if candidate_status and not candidate_status.transcription_ok:
                        continue
                    try:
                        self.logger.warning("backend %s 在 chunk %d 失敗，fallback 到 %s：%s", backend, chunk_id, candidate, primary_error)
                        segments = self.backend_manager.transcribe(candidate, audio, start)
                        effective_backend = candidate
                        self.store.set_last_backend(candidate)
                        self.store.log_event("WARNING", "backend_fallback", {"chunk_id": chunk_id, "from": backend, "to": candidate, "error": fallback_error})
                        break
                    except BackendUnavailable as fallback_exc:
                        fallback_error = f"{fallback_error}; {candidate}: {fallback_exc}"
                else:
                    raise BackendUnavailable(fallback_error) from primary_error
            checkpoint = {
                "job_id": self.job_id,
                "chunk_id": chunk_id,
                "input_hash": self.identity["sha256"],
                "config_hash": self.config.config_hash(),
                "backend": effective_backend,
                "chunk": {"start": start, "end": end, "core_start": claimed["core_start"], "core_end": claimed["core_end"]},
                "segments": segments,
                "created_at": now_iso(),
            }
            checkpoint_path = self._checkpoint_path(chunk_id)
            atomic_write_json(checkpoint_path, checkpoint)
            checkpoint_hash = sha256_file(checkpoint_path)
            self.store.complete_chunk(chunk_id, segments, str(checkpoint_path.relative_to(self.job_dir)), checkpoint_hash)
            self.store.log_event("INFO", "chunk_done", {"chunk_id": chunk_id, "backend": effective_backend, "segments": len(segments), "checkpoint_hash": checkpoint_hash})
            counts = self.store.chunk_counts()
            total = sum(counts.values())
            done = counts.get("done", 0) + counts.get("corrupt", 0)
            self.logger.info("chunk %d/%d 完成 backend=%s transcript=%d progress=%.0f%%", chunk_id + 1, total, effective_backend, len(segments), done / total * 100 if total else 100)
            self.store.write_status_json()
            return True
        except (MediaError, BackendUnavailable, OSError, ValueError) as exc:
            error = str(exc)
            attempts = int(claimed["attempts"])
            retry = attempts <= self.config.max_retries
            self.logger.error("chunk %d backend=%s 失敗（retry=%s）：%s", chunk_id, backend, retry, error)
            if retry:
                self.store.fail_chunk(chunk_id, error, True)
                self.store.log_event("WARNING", "chunk_retry", {"chunk_id": chunk_id, "error": error, "attempt": attempts})
                return True
            review_start = max(0.0, start - self.config.corrupt_review_seconds)
            review_end = min(self.media["duration"], end + self.config.corrupt_review_seconds)
            review_path = export_review_segment(self.input_path, self.job_dir / "review_corrupt", review_start, review_end - review_start, error, chunk_id)
            corrupt_id = self.store.add_corrupt(chunk_id, review_start, review_end, str(review_path) if review_path else None, error)
            self.store.fail_chunk(chunk_id, f"isolated corrupt #{corrupt_id}: {error}", False)
            self.store.log_event("ERROR", "chunk_isolated", {"chunk_id": chunk_id, "corrupt_id": corrupt_id, "review_path": str(review_path) if review_path else None})
            return True

    def _finalize(self) -> str:
        segments = merge_transcript(self.store.segments(), self.media["duration"], self.logger)
        metadata = {
            "job_id": self.job_id,
            "input": str(self.input_path),
            "input_hash": self.identity["sha256"],
            "config_hash": self.config.config_hash(),
            "language": self.config.language,
            "backend_probe": self.backend_manager.report() if self.backend_manager else {},
            "created_at": now_iso(),
        }
        write_transcript_files(self.job_dir, segments, metadata)
        highlights = analyze_highlights(
            self.input_path,
            segments,
            self.media["duration"],
            self.config.highlight_threshold,
            self.config.highlight_max_per_hour,
        )
        self.store.add_highlights(item.as_dict() for item in highlights)
        highlight_payload = {"metadata": metadata, "provider": "heuristic-local", "highlights": [item.as_dict() for item in highlights]}
        atomic_write_json(self.job_dir / "highlights.json", highlight_payload)
        output_records: list[dict[str, Any]] = []
        for index, item in enumerate(self.store.highlights(confirmed_only=True), 1):
            start = float(item["start"])
            end = float(item["end"])
            clip_path = self.job_dir / "clips" / f"candidate_{index:03d}_{start:010.3f}.mp4"
            try:
                if clip_path.exists():
                    summary = validate_candidate(clip_path)
                    self.logger.info("重用已驗證候選：%s", clip_path.name)
                    self.store.update_highlight_clip(int(item["id"]), str(clip_path.relative_to(self.job_dir)), "done")
                else:
                    summary = export_candidate(self.input_path, clip_path, start, end - start)
                    validate_candidate(clip_path)
                    self.store.update_highlight_clip(int(item["id"]), str(clip_path.relative_to(self.job_dir)), "done")
                    self.logger.info("已輸出候選：%s", clip_path)
                output_records.append({"path": str(clip_path.relative_to(self.job_dir)), "start": start, "end": end, "media": summary})
            except Exception as exc:
                self.logger.error("候選 %d 輸出/驗證失敗：%s", index, exc)
                self.store.update_highlight_clip(int(item["id"]), None, "failed")
                output_records.append({"start": start, "end": end, "error": str(exc)})
        atomic_write_json(self.job_dir / "outputs.json", {"transcript": "transcript/transcript.json", "highlights": "highlights.json", "clips": output_records})
        isolated = len(self.store.corrupt_rows())
        failed_clips = sum(1 for item in output_records if "error" in item)
        status = "completed_with_warnings" if isolated or failed_clips else "completed"
        marker = {"status": status, "job_id": self.job_id, "input_hash": self.identity["sha256"], "config_hash": self.config.config_hash(), "outputs": output_records, "corrupt_review_count": isolated, "created_at": now_iso()}
        atomic_write_json(self.job_dir / "COMPLETE.json", marker)
        self.store.set_job_status(status)
        self.store.log_event("INFO", "job_completed", {"status": status, "outputs": len(output_records), "isolated": isolated, "failed_clips": failed_clips})
        self.store.write_status_json()
        return status

    def run(self) -> dict[str, Any]:
        self._install_signal_handlers()
        set_background_priority(self.logger)
        lock = JobLock(self.job_dir)
        try:
            lock.acquire()
        except StateError:
            self.store.close()
            raise
        try:
            self.store.recover_running_chunks()
            if (self.job_dir / "STOP").exists():
                (self.job_dir / "STOP").unlink(missing_ok=True)
            self.store.set_job_status("running")
            self._startup_backends()
            pending = self.store.pending_chunks()
            work: queue.Queue[Any] = queue.Queue(maxsize=self.config.queue_size)
            producer_done = threading.Event()

            def producer() -> None:
                try:
                    for row in pending:
                        if self._stop.is_set():
                            break
                        while not self._stop.is_set():
                            try:
                                work.put(row, timeout=0.5)
                                break
                            except queue.Full:
                                continue
                    producer_done.set()
                finally:
                    producer_done.set()

            producer_thread = threading.Thread(target=producer, name="ayeai-chunk-producer", daemon=True)
            producer_thread.start()
            while not self._stop.is_set():
                if not self._control_wait():
                    break
                try:
                    row = work.get(timeout=0.5)
                except queue.Empty:
                    if producer_done.is_set():
                        break
                    continue
                try:
                    self._process_chunk(row)
                finally:
                    work.task_done()
            self._stop.set() if (self.job_dir / "STOP").exists() else None
            producer_thread.join(timeout=2.0)
            if self._stop.is_set() or (self.job_dir / "PAUSE").exists():
                status = "paused" if (self.job_dir / "PAUSE").exists() and not (self.job_dir / "STOP").exists() else "stopped"
                self.store.set_job_status(status)
                self.store.log_event("INFO", "job_not_finalized", {"status": status})
                self.store.write_status_json()
                return self.store.summary()
            remaining = self.store.pending_chunks()
            if remaining:
                self.store.set_job_status("paused_resource")
                self.store.write_status_json()
                return self.store.summary()
            self._finalize()
            return self.store.summary()
        finally:
            if self.backend_manager:
                self.backend_manager.close()
            lock.release()
            self.store.close()


def run_many(inputs: list[Path], config: RuntimeConfig, output_dir: Path | None = None, verbose: bool = False) -> list[dict[str, Any]]:
    if not inputs:
        raise ValueError("至少需要一個來源影片")
    pending_inputs: queue.Queue[Path] = queue.Queue(maxsize=max(1, config.queue_size))
    producer_done = threading.Event()

    def producer() -> None:
        try:
            for path in inputs:
                pending_inputs.put(path)
        finally:
            producer_done.set()

    thread = threading.Thread(target=producer, name="ayeai-job-producer", daemon=True)
    thread.start()
    results: list[dict[str, Any]] = []
    while not producer_done.is_set() or not pending_inputs.empty():
        try:
            path = pending_inputs.get(timeout=0.5)
        except queue.Empty:
            continue
        runner = JobRunner(path, config, output_dir=output_dir, multiple=len(inputs) > 1, verbose=verbose)
        try:
            results.append(runner.run())
        finally:
            pending_inputs.task_done()
    thread.join(timeout=1.0)
    return results


def control_job(job_dir: Path, action: str) -> dict[str, Any]:
    if not (job_dir / "state.db").exists():
        raise StateError(f"找不到有效 job：{job_dir}（請先執行影片命令建立 job）")
    store = StateStore(job_dir)
    try:
        if action == "pause":
            (job_dir / "PAUSE").write_text(now_iso(), encoding="utf-8")
            store.set_job_status("paused")
        elif action == "resume":
            (job_dir / "PAUSE").unlink(missing_ok=True)
            (job_dir / "STOP").unlink(missing_ok=True)
            store.set_job_status("queued")
        elif action == "stop":
            (job_dir / "STOP").write_text(now_iso(), encoding="utf-8")
            store.set_job_status("stopping")
        else:
            raise ValueError(f"未知控制命令：{action}")
        store.log_event("INFO", f"control_{action}", {})
        store.write_status_json()
        return store.summary()
    finally:
        store.close()


def review_corrupt(job_dir: Path, action: str, item_id: int | None = None, all_items: bool = False) -> dict[str, Any]:
    if not (job_dir / "state.db").exists():
        raise StateError(f"找不到有效 job：{job_dir}")
    store = StateStore(job_dir)
    try:
        rows = store.corrupt_rows()
        targets = [row for row in rows if all_items or item_id is None or int(row["id"]) == item_id]
        if item_id is not None and not targets:
            raise StateError(f"找不到 corrupt review id={item_id}")
        if not targets:
            raise StateError("沒有待處理的 corrupt review")
        for row in targets:
            path = Path(row["review_path"]) if row["review_path"] else None
            if action == "delete" and path:
                path.unlink(missing_ok=True)
                path.with_suffix(".json").unlink(missing_ok=True)
            store.update_corrupt_action(int(row["id"]), action)
            if action == "retry" and row["chunk_id"] is not None:
                store.connection.execute("UPDATE chunks SET status='retry', error=NULL, updated_at=? WHERE id=?", (now_iso(), int(row["chunk_id"])))
        store.write_status_json()
        return store.summary()
    finally:
        store.close()
