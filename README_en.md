# AyeAI

[繁體中文](README_zh-tw.md) | [简体中文](README_zh-cn.md) | [English](README_en.md)

AyeAI is a local AI live-stream processing tool for Windows 11. It can turn long MP4/MKV recordings into Chinese transcripts, detect highlight candidates, and export short clips for review.

```text
MP4/MKV
  → media validation
  → logical chunks
  → AI transcription
  → highlight analysis
  → candidate MP4 clips
```

Source videos are treated as read-only. Processing state is persisted so long-running jobs can be paused, resumed, and recovered after interruptions.

## 1. Features

- Chinese speech transcription with JSON, SRT, and plain-text output.
- Highlight scoring based on transcript density, volume/emotion changes, repeated or exclamatory speech, and surrounding context.
- CUDA, Intel NPU/OpenVINO, and CPU backends.
- Automatic resource scheduling: when the system is idle, new work can prefer the GPU; when a game or other heavy foreground workload is detected, new GPU work is stopped and processing falls back to NPU or a limited CPU backend.
- CPU background work uses a limited thread count and lower process priority instead of consuming all CPU resources.
- Long videos are processed as logical chunks without creating large numbers of temporary video files first.
- SQLite checkpoint/cache, pause/resume, safe Ctrl+C handling, and crash recovery.
- Multi-video queue, retries, backend fallback, job locking, input/config hashes, and atomic output handling.
- Damaged-region quarantine: export a short review clip around a problematic timestamp while allowing other chunks to continue.
- `--doctor` environment checks with actual backend probes.
- `--benchmark` backend benchmarking for automatic resource tuning.
- Traditional Chinese, Simplified Chinese, and English interface text.

## 2. Requirements

AyeAI currently targets Windows 11.

Running from source requires:

- Python 3.12
- FFmpeg / ffprobe
- PowerShell is recommended

Hardware acceleration is optional:

- NVIDIA GPU: CUDA/faster-whisper path.
- Intel NPU: OpenVINO path with a compatible Intel NPU and driver.
- CPU backend remains available when GPU/NPU acceleration is unavailable.

Use the inference tests reported by `--doctor` as the source of truth for backend availability. Device detection alone does not guarantee that a backend can run the selected model.

## 3. Quick start

### 3.1 Install from source

```powershell
git clone https://github.com/Ray20123315/AyeAI.git
cd AyeAI
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
```

The installer creates an isolated `.venv` inside the project directory. It does not require replacing the system Python installation or global CUDA environment.

After installation, run:

```powershell
.\.venv\Scripts\python.exe main.py --doctor
```

To use an Intel NPU, export the OpenVINO Whisper model and run Doctor again:

```powershell
.\scripts\export_npu_model.ps1
.\.venv\Scripts\python.exe main.py --doctor
```

### 3.2 Easiest way to start

```powershell
.\AyeAI.bat
```

`AyeAI.bat` opens the keyboard-driven CLI interface for choosing videos, checking status, pausing, resuming, stopping jobs, and reviewing damaged regions.

You can also start with a video directly:

```powershell
.\AyeAI.bat "D:\Videos\stream.mkv"
```

Or use the Python CLI directly:

```powershell
.\.venv\Scripts\python.exe main.py "D:\Videos\stream.mkv"
```

Multiple videos can be queued in one command:

```powershell
.\.venv\Scripts\python.exe main.py "D:\Videos\stream1.mp4" "D:\Videos\stream2.mkv"
```

## 4. Automatic resource scheduling

The default mode is:

```text
--backend auto
```

AyeAI reevaluates system conditions at chunk boundaries.

The general policy is:

1. When the system is idle and the CUDA backend is healthy, new work prefers the GPU.
2. When a game, heavy foreground workload, or GPU/VRAM/temperature risk is detected, the GPU stops receiving new chunks.
3. If the NPU passed its self-test, new work prefers the NPU.
4. If the NPU is unavailable or fails, processing falls back to a limited CPU backend.
5. Cooldown/hysteresis prevents rapid backend switching caused by short-lived load spikes.

For diagnostics, a backend can be selected manually:

```powershell
--backend cuda
--backend npu
--backend cpu
--backend auto
```

For normal long-running jobs, `auto` is generally the intended mode.

## 5. Pause, resume, and recovery

Every completed chunk is committed to persistent state and checkpoints.

Control an existing job with:

```powershell
# Status
.\.venv\Scripts\python.exe main.py --status "D:\Videos\stream_ayeai"

# Pause after the current work reaches a safe boundary
.\.venv\Scripts\python.exe main.py --pause "D:\Videos\stream_ayeai"

# Clear pause/stop flags
.\.venv\Scripts\python.exe main.py --resume "D:\Videos\stream_ayeai"

# Request a safe stop
.\.venv\Scripts\python.exe main.py --stop "D:\Videos\stream_ayeai"
```

Pressing `Ctrl+C` in the active processing window also requests a safe stop. Running the same source again reuses completed chunks instead of recomputing the whole job.

The same recovery design is used after crashes, forced termination, sleep, reboot, or unexpected power loss. Work that was not successfully committed may need to be repeated, but completed checkpoints are preserved.

## 6. Damaged media review

Sources are checked with ffprobe before processing. If the file is readable but a later chunk encounters decode, timestamp, or other media errors, AyeAI retries the chunk a limited number of times. If it still fails, AyeAI will:

- record the error and timestamp,
- export roughly 10–20 seconds around the problematic region into `review_corrupt`, and
- quarantine that region while allowing other chunks to continue.

After reviewing the exported clip, choose an action:

```powershell
# Keep the review clip and mark it reviewed
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action keep

# Skip the region
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action skip

# Requeue it for processing
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action retry

# Delete the temporary review clip while preserving the decision record
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action delete
```

If a source is damaged badly enough that ffprobe cannot establish a usable timeline at all, AyeAI may reject it before chunk processing. This is different from localized corruption in an otherwise readable file.

## 7. Output layout

By default, AyeAI creates a `<video-name>_ayeai` job directory near the source, for example:

```text
stream_ayeai/
├─ transcript/
│  ├─ transcript.json
│  ├─ transcript.srt
│  └─ transcript.txt
├─ highlights.json
├─ clips/
│  └─ candidate_*.mp4
├─ review_corrupt/
├─ checkpoints/
├─ logs/
│  └─ ayeai.log
├─ state.db
├─ backend_probe.json
├─ status.json
└─ COMPLETE.json
```

Candidate clips are checked with ffprobe after export. The original source file is not overwritten.

## 8. Doctor and benchmark

### Doctor

```powershell
.\.venv\Scripts\python.exe main.py --doctor
.\.venv\Scripts\python.exe main.py --doctor --json
```

Doctor checks Python, FFmpeg, CUDA/GPU, NPU/OpenVINO, the CPU backend, models, and system resources.

### Benchmark

```powershell
.\.venv\Scripts\python.exe main.py --benchmark "D:\Videos\stream.mkv"
```

Benchmark tests available backends against the same source and provides data used by automatic chunk/resource tuning.

### Full CLI reference

```powershell
.\.venv\Scripts\python.exe main.py --help
```

## 9. Building the EXE

If you have the required build and usage permissions, the repository includes a Windows build script:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build_windows_exe.ps1 -Clean
```

The output is written to:

```text
dist\AyeAI.exe
```

`build/`, `dist/`, runtime caches, and model files are not treated as normal source files in the repository.

## 10. License

AyeAI is not licensed under MIT, Apache, GPL, or another OSI open-source license. Public source visibility on GitHub does not automatically grant permission to modify, redistribute, fork, commercially use, or create derivative works from the project.

See [LICENSE](LICENSE) for the actual permissions and restrictions. Third-party packages, FFmpeg, CUDA, OpenVINO, models, and related resources remain subject to their own licenses and terms.

---

Copyright © 2026 Ray20123315. All rights reserved.
