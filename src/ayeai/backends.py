from __future__ import annotations

import dataclasses
import ctypes
import os
import site
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .config import RuntimeConfig, default_npu_model_dir


_CUDA_DLL_HANDLES: list[Any] = []
_CUDA_DLL_PATHS: set[str] = set()


def configure_cuda_dlls() -> None:
    """Make venv-local CUDA DLLs visible without changing the user's PATH."""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    candidates: list[Path] = []
    for site_dir in site.getsitepackages():
        root = Path(site_dir)
        candidates.extend(
            [
                root / "nvidia" / "cublas" / "bin",
                root / "nvidia" / "cuda_runtime" / "bin",
                root / "nvidia" / "cudnn" / "bin",
            ]
        )
    for directory in candidates:
        if directory.is_dir() and str(directory) not in _CUDA_DLL_PATHS:
            try:
                _CUDA_DLL_HANDLES.append(os.add_dll_directory(str(directory)))
                _CUDA_DLL_PATHS.add(str(directory))
                # CTranslate2 loads CUDA dependencies by name. This is a process-local
                # PATH augmentation; the user's system PATH is never persisted or edited.
                current_path = os.environ.get("PATH", "")
                if str(directory) not in current_path.split(os.pathsep):
                    os.environ["PATH"] = str(directory) + os.pathsep + current_path
                for dll_name in ("cublas64_12.dll", "cublasLt64_12.dll"):
                    dll_path = directory / dll_name
                    if dll_path.exists():
                        try:
                            ctypes.WinDLL(str(dll_path))
                        except OSError:
                            pass
            except OSError:
                pass


@dataclasses.dataclass(slots=True)
class BackendStatus:
    name: str
    import_ok: bool = False
    hardware_ok: bool = False
    transcription_ok: bool = False
    probe_seconds: float | None = None
    detail: str = ""
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class BackendUnavailable(RuntimeError):
    pass


class TranscriptionBackend:
    name = "unknown"

    def transcribe(self, audio: np.ndarray, offset: float, language: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def close(self) -> None:
        return


class FasterWhisperBackend(TranscriptionBackend):
    def __init__(self, config: RuntimeConfig, device: str, logger: Any):
        self.name = "cuda" if device == "cuda" else "cpu"
        self.logger = logger
        configure_cuda_dlls()
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            raise BackendUnavailable(f"faster-whisper import 失敗：{exc}") from exc
        compute_type = "float16" if device == "cuda" else "int8"
        try:
            self.model = WhisperModel(
                config.model_size,
                device=device,
                compute_type=compute_type,
                cpu_threads=config.cpu_threads if device == "cpu" else 0,
                num_workers=1,
            )
        except Exception as exc:
            raise BackendUnavailable(f"{self.name} WhisperModel 初始化失敗：{exc}") from exc

    def transcribe(self, audio: np.ndarray, offset: float, language: str) -> list[dict[str, Any]]:
        try:
            segments, _info = self.model.transcribe(
                audio,
                language=language,
                task="transcribe",
                beam_size=1,
                best_of=1,
                temperature=0.0,
                vad_filter=True,
                condition_on_previous_text=False,
                word_timestamps=False,
            )
            output: list[dict[str, Any]] = []
            for segment in segments:
                text = str(getattr(segment, "text", "")).strip()
                start = float(getattr(segment, "start", 0.0)) + offset
                end = float(getattr(segment, "end", start)) + offset
                if not text or end <= start:
                    continue
                output.append(
                    {
                        "start": max(offset, start),
                        "end": max(start, end),
                        "text": text,
                        "confidence": float(getattr(segment, "avg_logprob", 0.0)),
                        "no_speech_prob": float(getattr(segment, "no_speech_prob", 0.0)),
                    }
                )
            return output
        except Exception as exc:
            raise BackendUnavailable(f"{self.name} transcription 失敗：{exc}") from exc


class OpenVINOWhisperBackend(TranscriptionBackend):
    name = "npu"

    def __init__(self, config: RuntimeConfig, logger: Any):
        self.logger = logger
        self.model_dir = Path(config.npu_model_dir or default_npu_model_dir()).resolve()
        if not self.model_dir.exists():
            raise BackendUnavailable(f"NPU Whisper model 不存在：{self.model_dir}")
        try:
            import openvino_genai as ov_genai
        except Exception as exc:
            raise BackendUnavailable(f"openvino-genai import 失敗：{exc}") from exc
        try:
            self.pipe = ov_genai.WhisperPipeline(str(self.model_dir), "NPU", word_timestamps=False)
            self.ov_genai = ov_genai
        except Exception as exc:
            raise BackendUnavailable(f"NPU WhisperPipeline 初始化失敗：{exc}") from exc

    @staticmethod
    def _value(obj: Any, *names: str, default: Any = None) -> Any:
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return default

    def transcribe(self, audio: np.ndarray, offset: float, language: str) -> list[dict[str, Any]]:
        try:
            generation = self.pipe.get_generation_config()
            for attr, value in (("language", f"<|{language}|>"), ("task", "transcribe"), ("return_timestamps", True)):
                try:
                    setattr(generation, attr, value)
                except Exception:
                    pass
            result = self.pipe.generate(audio.astype(np.float32).tolist(), generation)
            chunks = self._value(result, "chunks", "segments", default=None)
            output: list[dict[str, Any]] = []
            if chunks is not None:
                for chunk in chunks:
                    text = str(self._value(chunk, "text", default="")).strip()
                    start = float(self._value(chunk, "start_ts", "start", default=0.0)) + offset
                    end = float(self._value(chunk, "end_ts", "end", default=start - offset)) + offset
                    if text and end > start:
                        output.append({"start": start, "end": end, "text": text, "confidence": None, "no_speech_prob": None})
            if not output:
                texts = self._value(result, "texts", "text", default=None)
                if isinstance(texts, str):
                    texts = [texts]
                if texts:
                    for text in texts:
                        text = str(text).strip()
                        if text:
                            output.append({"start": offset, "end": offset + len(audio) / 16000.0, "text": text, "confidence": None, "no_speech_prob": None})
            return output
        except Exception as exc:
            raise BackendUnavailable(f"npu transcription 失敗：{exc}") from exc


class BackendManager:
    def __init__(self, config: RuntimeConfig, logger: Any):
        self.config = config
        self.logger = logger
        self.statuses: dict[str, BackendStatus] = {
            "cuda": BackendStatus("cuda"),
            "npu": BackendStatus("npu"),
            "cpu": BackendStatus("cpu"),
        }
        self.instances: dict[str, TranscriptionBackend] = {}
        self._detect_imports_and_hardware()

    def _detect_imports_and_hardware(self) -> None:
        configure_cuda_dlls()
        try:
            import faster_whisper  # noqa: F401
            self.statuses["cuda"].import_ok = True
            self.statuses["cpu"].import_ok = True
        except Exception as exc:
            self.statuses["cuda"].detail = str(exc)
            self.statuses["cpu"].detail = str(exc)
        try:
            import ctranslate2

            try:
                self.statuses["cuda"].hardware_ok = int(ctranslate2.get_cuda_device_count()) > 0
                self.statuses["cuda"].detail = f"CTranslate2 CUDA devices={ctranslate2.get_cuda_device_count()}"
            except Exception as exc:
                self.statuses["cuda"].detail = f"CTranslate2 imported, CUDA probe unavailable: {exc}"
        except Exception as exc:
            self.statuses["cuda"].detail = f"ctranslate2 import failed: {exc}"
        try:
            from openvino import Core

            devices = list(Core().available_devices)
            self.statuses["npu"].import_ok = True
            self.statuses["npu"].hardware_ok = any(str(device).upper().startswith("NPU") for device in devices)
            self.statuses["npu"].detail = f"OpenVINO devices={devices}"
        except Exception as exc:
            self.statuses["npu"].detail = f"OpenVINO import/device probe failed: {exc}"
        self.statuses["cpu"].hardware_ok = self.statuses["cpu"].import_ok
        if self.statuses["cpu"].import_ok:
            self.statuses["cpu"].detail = "faster-whisper CPU"

    def available_for_work(self) -> dict[str, bool]:
        return {name: status.transcription_ok for name, status in self.statuses.items()}

    def _make(self, name: str) -> TranscriptionBackend:
        if name in self.instances:
            return self.instances[name]
        if name == "cuda":
            backend = FasterWhisperBackend(self.config, "cuda", self.logger)
        elif name == "cpu":
            backend = FasterWhisperBackend(self.config, "cpu", self.logger)
        elif name == "npu":
            backend = OpenVINOWhisperBackend(self.config, self.logger)
        else:
            raise BackendUnavailable(f"未知 backend：{name}")
        self.instances[name] = backend
        return backend

    def _probe_npu_hardware(self) -> tuple[bool, str]:
        try:
            from openvino import Core, Model, opset8

            parameter = opset8.parameter([1], np.float32, name="input")
            result = opset8.result(parameter)
            model = Model([result], [parameter], "ayeai_npu_probe")
            compiled = Core().compile_model(model, "NPU")
            compiled({"input": np.ones([1], dtype=np.float32)})
            return True, "OpenVINO NPU identity inference passed"
        except Exception as exc:
            return False, str(exc)

    def probe_all(self, audio: np.ndarray | None = None) -> dict[str, BackendStatus]:
        audio = audio if audio is not None else np.zeros(16000, dtype=np.float32)
        for name in ("cuda", "npu", "cpu"):
            status = self.statuses[name]
            started = time.perf_counter()
            try:
                if name == "npu" and status.hardware_ok:
                    hardware_ok, detail = self._probe_npu_hardware()
                    status.hardware_ok = hardware_ok
                    status.detail = detail
                    if not hardware_ok:
                        status.error = detail
                        continue
                elif name == "npu":
                    status.error = status.detail or "NPU device not enumerated"
                    continue
                if name == "cuda" and not status.hardware_ok:
                    status.error = status.detail or "CUDA device not enumerated"
                    continue
                backend = self._make(name)
                backend.transcribe(audio, 0.0, self.config.language)
                status.transcription_ok = True
                status.detail = (status.detail + "; " if status.detail else "") + "actual inference passed"
            except Exception as exc:
                status.error = str(exc)
                status.transcription_ok = False
                self.logger.warning("backend %s self-test 失敗：%s", name, exc)
            finally:
                status.probe_seconds = round(time.perf_counter() - started, 3)
        # A CPU import can still be useful even if the model could not load; do not call it ready.
        return self.statuses

    def transcribe(self, name: str, audio: np.ndarray, offset: float) -> list[dict[str, Any]]:
        # Startup probes may be skipped; instantiate lazily at the first chunk boundary.
        backend = self.instances.get(name) or self._make(name)
        return backend.transcribe(audio, offset, self.config.language)

    def close(self) -> None:
        for backend in self.instances.values():
            try:
                backend.close()
            except Exception:
                pass

    def report(self) -> dict[str, dict[str, Any]]:
        return {name: status.as_dict() for name, status in self.statuses.items()}
