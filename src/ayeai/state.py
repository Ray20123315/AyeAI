from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import psutil

from .utils import atomic_write_json, now_iso


SCHEMA_VERSION = 1


class StateError(RuntimeError):
    pass


class JobLock:
    def __init__(self, job_dir: Path):
        self.job_dir = job_dir
        self.path = job_dir / "job.lock"
        self._fd: int | None = None

    def acquire(self) -> None:
        self.job_dir.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "created_at": now_iso(), "path": str(self.job_dir.resolve())}
        try:
            self._fd = os.open(os.fspath(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self._fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            os.fsync(self._fd)
        except FileExistsError as exc:
            existing: dict[str, Any] = {}
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            pid = int(existing.get("pid") or 0)
            if pid and psutil.pid_exists(pid):
                raise StateError(f"此 job 目前已被程序鎖定（PID {pid}）：{self.job_dir}") from exc
            stale = self.path.with_name(f"job.lock.stale.{int(time.time())}")
            try:
                os.replace(self.path, stale)
            except OSError as replace_exc:
                raise StateError(f"找到舊 job lock 且無法安全接管：{self.path}") from replace_exc
            self.acquire()

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
                self.path.unlink(missing_ok=True)

    def __enter__(self) -> "JobLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


class StateStore:
    def __init__(self, job_dir: Path):
        self.job_dir = job_dir
        self.job_dir.mkdir(parents=True, exist_ok=True)
        for name in ("checkpoints", "transcript", "clips", "review_corrupt", "logs"):
            (job_dir / name).mkdir(parents=True, exist_ok=True)
        self.db_path = job_dir / "state.db"
        self.connection = sqlite3.connect(self.db_path, timeout=60, isolation_level=None, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                job_id TEXT NOT NULL,
                input_path TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                config_json TEXT NOT NULL,
                media_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_backend TEXT,
                error_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                start REAL NOT NULL,
                end REAL NOT NULL,
                core_start REAL NOT NULL,
                core_end REAL NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                backend TEXT,
                transcript_path TEXT,
                checkpoint_hash TEXT,
                error TEXT,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status, id);
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id INTEGER NOT NULL REFERENCES chunks(id),
                seq INTEGER NOT NULL,
                start REAL NOT NULL,
                end REAL NOT NULL,
                text TEXT NOT NULL,
                confidence REAL,
                no_speech_prob REAL,
                UNIQUE(chunk_id, seq)
            );
            CREATE INDEX IF NOT EXISTS idx_segments_time ON segments(start, end);
            CREATE TABLE IF NOT EXISTS highlights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start REAL NOT NULL,
                end REAL NOT NULL,
                score REAL NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0,
                reasons_json TEXT NOT NULL,
                transcript TEXT NOT NULL,
                clip_path TEXT,
                clip_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS corrupt (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id INTEGER,
                start REAL NOT NULL,
                end REAL NOT NULL,
                review_path TEXT,
                error TEXT NOT NULL,
                action TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                event TEXT NOT NULL,
                data_json TEXT NOT NULL
            );
            INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');
            """
        )

    def initialize_job(
        self,
        *,
        job_id: str,
        input_path: Path,
        input_hash: str,
        config_hash: str,
        config_json: dict[str, Any],
        media_json: dict[str, Any],
    ) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM job WHERE id = 1").fetchone()
        if row:
            if row["input_hash"] != input_hash:
                raise StateError("這個 job 目錄已屬於不同來源檔案；請改用新的輸出目錄")
            if row["config_hash"] != config_hash:
                self.log_event("WARNING", "config_changed", {"old": row["config_hash"], "new": config_hash})
            self.connection.execute(
                "UPDATE job SET config_hash=?, config_json=?, media_json=?, updated_at=? WHERE id=1",
                (config_hash, json.dumps(config_json, ensure_ascii=False), json.dumps(media_json, ensure_ascii=False), now_iso()),
            )
            return self.connection.execute("SELECT * FROM job WHERE id = 1").fetchone()
        self.connection.execute(
            "INSERT INTO job(id, job_id, input_path, input_hash, config_hash, config_json, media_json, status, created_at, updated_at) VALUES (1,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                str(input_path.resolve()),
                input_hash,
                config_hash,
                json.dumps(config_json, ensure_ascii=False),
                json.dumps(media_json, ensure_ascii=False),
                "queued",
                now_iso(),
                now_iso(),
            ),
        )
        return self.connection.execute("SELECT * FROM job WHERE id = 1").fetchone()

    def job(self) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM job WHERE id=1").fetchone()
        if not row:
            raise StateError("state.db 尚未初始化 job")
        return row

    def set_job_status(self, status: str, error_count: int | None = None) -> None:
        if error_count is None:
            self.connection.execute("UPDATE job SET status=?, updated_at=? WHERE id=1", (status, now_iso()))
        else:
            self.connection.execute("UPDATE job SET status=?, error_count=?, updated_at=? WHERE id=1", (status, error_count, now_iso()))

    def set_last_backend(self, backend: str) -> None:
        self.connection.execute("UPDATE job SET last_backend=?, updated_at=? WHERE id=1", (backend, now_iso()))

    def insert_chunks(self, chunks: Iterable[dict[str, float]]) -> int:
        existing = self.connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"]
        if existing:
            return int(existing)
        rows = list(chunks)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for index, chunk in enumerate(rows):
                self.connection.execute(
                    "INSERT INTO chunks(id,start,end,core_start,core_end,status,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (index, chunk["start"], chunk["end"], chunk["core_start"], chunk["core_end"], "pending", now_iso()),
                )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        self.log_event("INFO", "chunks_created", {"count": len(rows)})
        return len(rows)

    def recover_running_chunks(self) -> int:
        cursor = self.connection.execute(
            "UPDATE chunks SET status='pending', error=COALESCE(error || '; ', '') || 'recovered_after_previous_process', updated_at=? WHERE status='running'",
            (now_iso(),),
        )
        if cursor.rowcount:
            self.log_event("WARNING", "running_chunks_recovered", {"count": cursor.rowcount})
        return cursor.rowcount

    def pending_chunks(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM chunks WHERE status IN ('pending','retry') ORDER BY id"))

    def chunk_counts(self) -> dict[str, int]:
        rows = self.connection.execute("SELECT status, COUNT(*) AS count FROM chunks GROUP BY status").fetchall()
        return {row["status"]: int(row["count"]) for row in rows}

    def claim_chunk(self, chunk_id: int, backend: str) -> sqlite3.Row:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
            if not row or row["status"] not in ("pending", "retry"):
                self.connection.execute("ROLLBACK")
                raise StateError(f"chunk {chunk_id} 不在可執行狀態")
            self.connection.execute(
                "UPDATE chunks SET status='running', attempts=attempts+1, backend=?, started_at=?, updated_at=? WHERE id=?",
                (backend, now_iso(), now_iso(), chunk_id),
            )
            self.connection.execute("COMMIT")
        except Exception:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        return self.connection.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()

    def complete_chunk(
        self,
        chunk_id: int,
        segments: list[dict[str, Any]],
        transcript_path: str,
        checkpoint_hash: str,
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("DELETE FROM segments WHERE chunk_id=?", (chunk_id,))
            for seq, segment in enumerate(segments):
                self.connection.execute(
                    "INSERT INTO segments(chunk_id,seq,start,end,text,confidence,no_speech_prob) VALUES (?,?,?,?,?,?,?)",
                    (
                        chunk_id,
                        seq,
                        float(segment["start"]),
                        float(segment["end"]),
                        str(segment.get("text", "")).strip(),
                        segment.get("confidence"),
                        segment.get("no_speech_prob"),
                    ),
                )
            self.connection.execute(
                "UPDATE chunks SET status='done', transcript_path=?, checkpoint_hash=?, error=NULL, completed_at=?, updated_at=? WHERE id=?",
                (transcript_path, checkpoint_hash, now_iso(), now_iso(), chunk_id),
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def fail_chunk(self, chunk_id: int, error: str, retry: bool) -> None:
        status = "retry" if retry else "corrupt"
        self.connection.execute(
            "UPDATE chunks SET status=?, error=?, updated_at=? WHERE id=?",
            (status, error[:4000], now_iso(), chunk_id),
        )

    def segments(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM segments ORDER BY start, end, id"))

    def add_corrupt(self, chunk_id: int | None, start: float, end: float, review_path: str | None, error: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO corrupt(chunk_id,start,end,review_path,error,action,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (chunk_id, start, end, review_path, error[:4000], "pending", now_iso(), now_iso()),
        )
        return int(cursor.lastrowid)

    def corrupt_rows(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM corrupt ORDER BY start, id"))

    def update_corrupt_action(self, corrupt_id: int, action: str) -> None:
        if action not in {"keep", "skip", "retry", "delete", "pending"}:
            raise StateError(f"不支援的 review action：{action}")
        self.connection.execute("UPDATE corrupt SET action=?, updated_at=? WHERE id=?", (action, now_iso(), corrupt_id))
        self.log_event("INFO", "corrupt_action", {"id": corrupt_id, "action": action})

    def add_highlights(self, highlights: Iterable[dict[str, Any]]) -> None:
        self.connection.execute("DELETE FROM highlights")
        for item in highlights:
            self.connection.execute(
                "INSERT INTO highlights(start,end,score,confirmed,reasons_json,transcript,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    item["start"],
                    item["end"],
                    item["score"],
                    int(bool(item.get("confirmed"))),
                    json.dumps(item.get("reasons", []), ensure_ascii=False),
                    item.get("transcript", ""),
                    now_iso(),
                ),
            )

    def highlights(self, confirmed_only: bool = False) -> list[sqlite3.Row]:
        query = "SELECT * FROM highlights"
        if confirmed_only:
            query += " WHERE confirmed=1"
        query += " ORDER BY score DESC, start"
        return list(self.connection.execute(query))

    def update_highlight_clip(self, highlight_id: int, path: str | None, status: str) -> None:
        self.connection.execute("UPDATE highlights SET clip_path=?, clip_status=? WHERE id=?", (path, status, highlight_id))

    def log_event(self, level: str, event: str, data: dict[str, Any] | None = None) -> None:
        self.connection.execute(
            "INSERT INTO events(created_at,level,event,data_json) VALUES (?,?,?,?)",
            (now_iso(), level, event, json.dumps(data or {}, ensure_ascii=False)),
        )

    def recent_events(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(self.connection.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)))

    def summary(self) -> dict[str, Any]:
        job = self.job()
        counts = self.chunk_counts()
        total = sum(counts.values())
        done = counts.get("done", 0) + counts.get("corrupt", 0)
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "input_path": job["input_path"],
            "last_backend": job["last_backend"],
            "chunks": counts,
            "total_chunks": total,
            "completed_or_isolated": done,
            "progress": (done / total) if total else 0.0,
            "highlights": len(self.highlights()),
            "confirmed_highlights": len(self.highlights(True)),
            "corrupt_review": len(self.corrupt_rows()),
            "updated_at": job["updated_at"],
        }

    def write_status_json(self) -> None:
        atomic_write_json(self.job_dir / "status.json", self.summary())
