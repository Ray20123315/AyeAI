# AyeAI

[繁體中文](README_zh-tw.md) | [简体中文](README_zh-cn.md) | [English](README_en.md)

AyeAI 是一套為 Windows 11 設計的本機 AI 直播處理工具，可將長時間 MP4/MKV 影片轉成中文逐字稿、找出高光候選，並輸出可直接檢查的短影片。

```text
MP4/MKV
  → 媒體驗證
  → 邏輯分塊
  → AI 轉錄
  → 高光分析
  → 候選 MP4
```

原始影片保持唯讀；處理狀態會持久化，因此長時間工作可暫停、續跑，並在程式或電腦意外中斷後恢復。

## 1. 主要功能

- 中文語音轉錄，輸出 JSON、SRT 與純文字逐字稿。
- 自動分析語音密度、音量/情緒變化、重複/驚嘆語句與上下文，產生高光候選。
- CUDA、Intel NPU/OpenVINO 與 CPU backend。
- 自動資源調度：電腦閒置時可優先使用 GPU；偵測到遊戲或高負載前景工作時，停止將新工作交給 GPU，改由 NPU 或受限 CPU 處理。
- CPU 背景工作限制執行緒並降低優先權，避免長時間吃滿處理器。
- 影片以邏輯 chunks 處理，不會先產生大量中間影片檔。
- SQLite checkpoint/cache、Pause/Resume、Ctrl+C 安全停止、crash recovery。
- 多影片 queue、重試、backend fallback、job lock、input/config hash 與 atomic output。
- 損壞區段隔離：自動匯出問題時間附近的短片供人工檢查，其他區段可繼續處理。
- `--doctor` 環境檢查與 backend 實際 probe。
- `--benchmark` 實測可用 backend，協助自動調整資源策略。
- 支援繁體中文、簡體中文與英文介面文字。

## 2. 系統需求

目前專案以 Windows 11 為主要平台。

從原始碼執行需要：

- Python 3.12
- FFmpeg / ffprobe
- 建議使用 PowerShell

硬體加速為選配：

- NVIDIA GPU：使用 CUDA/faster-whisper 路徑。
- Intel NPU：使用 OpenVINO 路徑，需相容的 Intel NPU 與驅動。
- 沒有 GPU/NPU 時仍可使用 CPU backend。

實際可用 backend 請以 `--doctor` 的推論自測結果為準，不要只以裝置是否被系統列出判斷。

## 3. 快速開始

### 3.1 從原始碼安裝

```powershell
git clone https://github.com/Ray20123315/AyeAI.git
cd AyeAI
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
```

安裝腳本會建立專案內的 `.venv`，不需要覆寫系統 Python 或全域 CUDA 環境。

安裝後先執行：

```powershell
.\.venv\Scripts\python.exe main.py --doctor
```

若要使用 Intel NPU，可匯出 OpenVINO Whisper 模型：

```powershell
.\scripts\export_npu_model.ps1
.\.venv\Scripts\python.exe main.py --doctor
```

### 3.2 最簡單的啟動方式

```powershell
.\AyeAI.bat
```

`AyeAI.bat` 會開啟鍵盤互動式 CLI；可選擇影片、查看狀態、暫停、續跑、停止或處理損壞片段。

也可直接處理影片：

```powershell
.\AyeAI.bat "D:\Videos\stream.mkv"
```

或直接使用 Python CLI：

```powershell
.\.venv\Scripts\python.exe main.py "D:\Videos\stream.mkv"
```

一次排入多部影片：

```powershell
.\.venv\Scripts\python.exe main.py "D:\Videos\stream1.mp4" "D:\Videos\stream2.mkv"
```

## 4. 自動資源調度

預設使用：

```text
--backend auto
```

AyeAI 會在 chunk 邊界重新評估系統狀態。

一般策略：

1. 系統空閒且 CUDA backend 可用時，優先將新工作交給 GPU。
2. 偵測到遊戲、前景高負載或 GPU/VRAM/溫度風險時，GPU 不再接收新 chunk。
3. 若 NPU 通過自測，優先改由 NPU 處理。
4. NPU 不可用或失敗時，使用受限 CPU backend。
5. 使用 cooldown/hysteresis 避免 backend 因瞬間負載反覆切換。

如需診斷，可手動指定：

```powershell
--backend cuda
--backend npu
--backend cpu
--backend auto
```

正式長時間處理通常建議保留 `auto`。

## 5. 暫停、續跑與意外中斷

每個完成的 chunk 都會寫入持久狀態與 checkpoint。

控制既有 job：

```powershell
# 查看狀態
.\.venv\Scripts\python.exe main.py --status "D:\Videos\stream_ayeai"

# 暫停：目前工作安全收尾後不再接新 chunk
.\.venv\Scripts\python.exe main.py --pause "D:\Videos\stream_ayeai"

# 解除暫停
.\.venv\Scripts\python.exe main.py --resume "D:\Videos\stream_ayeai"

# 安全停止
.\.venv\Scripts\python.exe main.py --stop "D:\Videos\stream_ayeai"
```

在執行視窗按 `Ctrl+C` 也會要求安全停止。重新執行同一來源後，已成功完成的 chunk 會被重用，不會從頭全部重算。

此設計也用於處理程式崩潰、強制關閉、睡眠、重開機或突然斷電後的恢復；尚未成功提交的工作可能需要重做，但已完成 checkpoint 會保留。

## 6. 損壞影片與人工檢查

來源會先由 ffprobe 檢查。若影片可開啟，但中途出現 decode、timestamp 或其他媒體異常，AyeAI 會嘗試有限次重試；仍失敗時會：

- 記錄錯誤時間與原因。
- 匯出錯誤位置前後約 10～20 秒到 `review_corrupt`。
- 隔離該區段，讓其他 chunk 繼續處理。

人工查看後可選擇：

```powershell
# 保留並標記已查看
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action keep

# 跳過
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action skip

# 重新排入處理
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action retry

# 刪除人工檢查用暫存片段，但保留處理紀錄
.\.venv\Scripts\python.exe main.py --review "D:\Videos\stream_ayeai" --id 1 --action delete
```

若來源檔已損壞到 ffprobe 無法建立可用時間軸，程式可能會直接拒絕來源；這與「影片可讀但中途局部損壞」是不同情況。

## 7. 輸出內容

預設會在來源附近建立 `<影片名稱>_ayeai` job 目錄，例如：

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

候選影片輸出後會再次使用 ffprobe 驗證。原始來源不會被覆寫。

## 8. 診斷與 Benchmark

### Doctor

```powershell
.\.venv\Scripts\python.exe main.py --doctor
.\.venv\Scripts\python.exe main.py --doctor --json
```

用來檢查 Python、FFmpeg、CUDA/GPU、NPU/OpenVINO、CPU backend、模型與系統資源。

### Benchmark

```powershell
.\.venv\Scripts\python.exe main.py --benchmark "D:\Videos\stream.mkv"
```

Benchmark 用相同來源測試可用 backend，結果可供自動調整 chunk 與資源策略使用。

### 完整參數

```powershell
.\.venv\Scripts\python.exe main.py --help
```

## 9. 建置 EXE

若你具有相應的建置與使用權限，可使用專案提供的腳本建立 Windows EXE：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build_windows_exe.ps1 -Clean
```

輸出位置為：

```text
dist\AyeAI.exe
```

`build/`、`dist/`、runtime cache 與模型檔不會作為一般原始碼提交內容。

## 10. 授權

AyeAI 不是 MIT、Apache、GPL 或其他 OSI 開源授權專案。GitHub 上可查看原始碼，不代表自動取得修改、散布、fork、商業使用或建立衍生作品的權利。

使用、建置、散布或其他權限請以 [LICENSE](LICENSE) 為準。第三方套件、FFmpeg、CUDA、OpenVINO、模型及其相關資源仍受各自授權條款約束。

---

Copyright © 2026 Ray20123315. All rights reserved.
