from __future__ import annotations

from pathlib import Path

from ayeai.chunks import build_logical_chunks
from ayeai.config import RuntimeConfig
from ayeai.highlighter import HeuristicLLMProvider
from ayeai.pipeline import merge_transcript
from ayeai.state import StateStore


def test_config_hash_is_stable(tmp_path: Path) -> None:
    first = RuntimeConfig(npu_model_dir=tmp_path / "模型")
    second = RuntimeConfig(npu_model_dir=tmp_path / "模型")
    assert first.config_hash() == second.config_hash()


def test_logical_chunks_have_overlap_but_no_physical_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("ayeai.chunks.find_quiet_boundary", lambda *_args, **_kwargs: 90.0)
    config = RuntimeConfig(chunk_seconds=90, overlap_seconds=6, npu_model_dir=tmp_path / "npu")
    chunks = build_logical_chunks(tmp_path / "來源.mp4", 200, config)
    assert len(chunks) == 3
    assert chunks[0]["core_start"] == 0.0
    assert chunks[1]["start"] < chunks[1]["core_start"]
    assert not list(tmp_path.iterdir())


def test_merge_transcript_deduplicates_only_exact_overlap() -> None:
    class Row(dict):
        pass

    rows = [
        Row(id=1, start=0.0, end=2.0, text="你好世界", confidence=-0.1, no_speech_prob=0.0),
        Row(id=2, start=1.5, end=3.0, text="你好世界", confidence=-0.2, no_speech_prob=0.0),
        Row(id=3, start=2.5, end=4.0, text="保留不同語句", confidence=-0.2, no_speech_prob=0.0),
    ]
    result = merge_transcript(rows, 10.0, _Logger())
    assert [item["text"] for item in result] == ["你好世界", "保留不同語句"]
    assert result[0]["end"] == 3.0


def test_state_store_checkpoint_and_recovery(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "job")
    try:
        store.initialize_job(
            job_id="j1",
            input_path=tmp_path / "input.mp4",
            input_hash="input-hash",
            config_hash="config-hash",
            config_json={},
            media_json={"duration": 10},
        )
        store.insert_chunks([{"start": 0, "end": 10, "core_start": 0, "core_end": 10}])
        claimed = store.claim_chunk(0, "cpu")
        assert claimed["status"] == "running"
        assert store.recover_running_chunks() == 1
        assert store.pending_chunks()[0]["status"] == "pending"
        claimed = store.claim_chunk(0, "cpu")
        store.complete_chunk(0, [{"start": 0, "end": 1, "text": "測試", "confidence": None, "no_speech_prob": None}], "checkpoints/chunk_000000.json", "hash")
        assert store.summary()["completed_or_isolated"] == 1
    finally:
        store.close()


def test_heuristic_provider_uses_multiple_signals() -> None:
    score, reasons = HeuristicLLMProvider().score_context(
        "哇 太扯了！！！太扯了！！！",
        {"density": 6.0, "loudness_change": 0.8, "exclaim": 4, "repetition": 1.0, "context": 0.8},
    )
    assert score > 0.5
    assert len(reasons) >= 3


class _Logger:
    def warning(self, *_args, **_kwargs):
        pass

