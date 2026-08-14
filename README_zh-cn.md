# AyeAI

[繁體中文](README_zh-tw.md) | [简体中文](README_zh-cn.md) | [English](README_en.md)

AyeAI 是一套面向 Windows 11 的本地 AI 直播处理工具，可将长时间 MP4/MKV 视频转换为中文逐字稿、自动寻找高光候选，并输出可直接检查的短视频。

```text
MP4/MKV
  → 媒体验证
  → 逻辑分块
  → AI 转录
  → 高光分析
  → 候选 MP4
```

原始视频保持只读；处理状态会持久化，因此长时间任务可以暂停、续跑，并在程序或电脑意外中断后恢复。

## 1. 主要功能

- 中文语音转录，输出 JSON、SRT 与纯文本逐字稿。
- 自动分析语音密度、音量/情绪变化、重复/感叹语句与上下文，生成高光候选。
- CUDA、Intel NPU/OpenVINO 与 CPU backend。
- 自动资源调度：电脑空闲时可优先使用 GPU；检测到游戏或高负载前台工作时，停止向 GPU 分配新任务，改由 NPU 或受限 CPU 处理。
- CPU 后台任务限制线程并降低优先级，避免长时间占满处理器。
- 视频采用逻辑 chunks 处理，不会预先生成大量中间视频文件。
- SQLite checkpoint/cache、Pause/Resume、Ctrl+C 安全停止、crash recovery。
- 多视频 queue、重试、backend fallback、job lock、input/config hash 与 atomic output。
- 损坏区段隔离：自动导出问题时间附近的短片供人工检查，其余区段可继续处理。
- `--doctor` 环境检查与 backend 实际 probe。
- `--benchmark` 实测可用 backend，帮助自动调整资源策略。
- 支持繁体中文、简体中文与英文界面文本。

## 2. 系统要求

当前项目以 Windows 11 为主要平台。

从源代码运行需要：

- Python 3.12
- FFmpeg / ffprobe
- 推荐使用 PowerShell

硬件加速为可选：

- NVIDIA GPU：使用 CUDA/faster-whisper 路径。
- Intel NPU：使用 OpenVINO 路径，需要兼容的 Intel NPU 与驱动。
- 没有 GPU/NPU 时仍可使用 CPU backend。

实际可用 backend 请以 `--doctor` 的推理自测结果为准，不要仅根据系统是否列出设备进行判断。

## 3. 快速开始

### 3.1 从源代码安装

```powershell
git clone https://github.com/Ray20123315/AyeAI.git
cd AyeAI
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
```

安装脚本会在项目目录创建独立 `.venv`，无需覆盖系统 Python 或全局 CUDA 环境。

安装后先执行：

```powershell
.\.venv\Scripts\python.exe main.py --doctor
```

若要使用 Intel NPU，可导出 OpenVINO Whisper 模型：

```powershell
.\scripts\export_npu_model.ps1
.\.venv\Scripts\python.exe main.py --doctor
```

### 3.2 最简单的启动方式

```powershell
.\AyeAI.bat
```

`AyeAI.bat` 会打开键盘交互式 CLI，可选择视频、查看状态、暂停、续跑、停止或处理损坏片段。

也可直接处理视频：

```powershell
.\AyeAI.bat "D:\Videos\stream.mkv"
```

或直接使用 Python CLI：

```powershell
.\.venv\Scripts\python.exe main.py "D:\Videos\stream.mkv"
```

一次加入多个视频：

```powershell
.\.venv\Scripts\python.exe main.py "D:\Videos\stream1.mp4" "D:\Videos\stream2.mkv"
```

## 4. 自动资源调度

默认使用：

```text
--backend auto
```

AyeAI 会在 chunk 边界重新评估系统状态。

一般策略：

1. 系统空闲且 CUDA backend 可用时，优先将新任务交给 GPU。
2. 检测到游戏、前台高负载或 GPU/VRAM/温度风险时，GPU 不再接收新的 chunk。
3. 若 NPU 通过自测，优先改由 NPU 处理。
4. NPU 不可用或失败时，使用受限 CPU backend。
5. 使用 cooldown/hysteresis 避免 backend 因瞬时负载反复切换。

诊断时可手动指定：

```powershell
--backend cuda
--backend npu
--backend cpu
--backend auto
```

正式长时间处理通常建议保留 `auto`。

## 5. 暂停、续跑与意外中断

每个完成的 chunk 都会写入持久状态与 checkpoint。

控制已有 job：

```powershell
# 查看状态
.\.venv\Scripts\python.exe main.py --status "D:\Videos\stream_ayeai"

# 暂停：当前工作安全收尾后不再接收新 chunk
.\.venv\Scripts\python.exe main.py --pause "D:\Videos\stream_ayeai"

# 解除暂停
.\.venv\Scripts\python.exe main.py --resume "D:\Videos\stream_ayeai"

# 安全停止
.\.venv\Scripts\python.exe main.py --stop "D:\Videos\stream_ayeai"
```

在运行窗口按 `Ctrl+C` 也会请求安全停止。重新执行同一来源后，已成功完成的 chunk 会被复用，不会从头全部重算。

该设计同样用于程序崩溃、强制关闭、睡眠、重启或突然断电后的恢复；尚未成功提交的工作可能需要重做，但已完成 checkpoint 会保留。

## 6. 损坏视频与人工检查

来源会先由 ffprobe 检查。若视频可打开，但中途出现 decode、timestamp 或其他媒体异常，AyeAI 会尝试有限次数重试；仍失败时会：

- 记录错误时间与原因。
- 导出错误位置前后约 10～20 秒到 `review_corrupt`。
- 隔离该区段，让其他 chunk 继续处理。

人工查看后可选择：

```powershell
# 保留并标记已查看
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action keep

# 跳过
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action skip

# 重新加入处理队列
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action retry

# 删除人工检查用临时片段，但保留处理记录
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action delete
```

若源文件已经损坏到 ffprobe 无法建立可用时间轴，程序可能直接拒绝该来源；这与“视频可读但中途局部损坏”是不同情况。

## 7. 输出内容

默认会在来源附近创建 `<视频名称>_ayeai` job 目录，例如：

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

候选视频输出后会再次使用 ffprobe 验证。原始来源不会被覆盖。

## 8. 诊断与 Benchmark

### Doctor

```powershell
.\.venv\Scripts\python.exe main.py --doctor
.\.venv\Scripts\python.exe main.py --doctor --json
```

用于检查 Python、FFmpeg、CUDA/GPU、NPU/OpenVINO、CPU backend、模型与系统资源。

### Benchmark

```powershell
.\.venv\Scripts\python.exe main.py --benchmark "D:\Videos\stream.mkv"
```

Benchmark 使用相同来源测试可用 backend，结果可供自动调整 chunk 与资源策略。

### 完整参数

```powershell
.\.venv\Scripts\python.exe main.py --help
```

## 9. 构建 EXE

如果你具有相应的构建与使用权限，可使用项目提供的脚本构建 Windows EXE：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build_windows_exe.ps1 -Clean
```

输出位置：

```text
dist\AyeAI.exe
```

`build/`、`dist/`、runtime cache 与模型文件不会作为常规源代码提交内容。

## 10. 许可

AyeAI 不是 MIT、Apache、GPL 或其他 OSI 开源许可项目。GitHub 上可以查看源代码，并不代表自动获得修改、分发、fork、商业使用或创建衍生作品的权利。

使用、构建、分发及其他权限请以 [LICENSE](LICENSE) 为准。第三方软件包、FFmpeg、CUDA、OpenVINO、模型及其相关资源仍受各自许可条款约束。

---

Copyright © 2026 Ray20123315. All rights reserved.
