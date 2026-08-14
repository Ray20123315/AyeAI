# AyeAI：本機 AI 直播剪輯工具

本 README 依序提供繁體中文、简体中文與 English 說明。AyeAI 是無 GUI 的 Windows 11 CLI；EXE 會在每次啟動時顯示授權聲明與 GitHub 連結。

## 繁體中文

AyeAI 是 Windows 11 的無 GUI CLI。它把長 MP4/MKV 直播依照邏輯時間窗處理成：

```text
影片 → ffprobe 驗證 → VAD-aware 邏輯 chunks → 中文逐字稿 → 多訊號高光 → 候選 MP4
```

原始影片只讀不改。每個 chunk 做完就寫入 SQLite、checkpoint 與 cache；當機、睡眠、斷電或 Ctrl+C 後，重新執行同一命令會跳過已完成工作。

## 1. 第一次安裝

請先安裝 Python 3.12（不要使用系統 Python 3.13 來建立這個環境）以及 FFmpeg。確認下列兩個命令能執行：

```powershell
py -3.12 --version
ffmpeg -version
ffprobe -version
```

在本專案根目錄執行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
```

安裝只會寫入本專案的 `.venv`，不會改動系統 Python、CUDA 或 PATH。

### Intel NPU 模型

NPU 需要 OpenVINO Whisper IR 模型；它不是 `faster-whisper` 的同一份 cache。安裝完成後執行：

```powershell
.\.venv\Scripts\python.exe main.py --doctor
.\scripts\export_npu_model.ps1
.\.venv\Scripts\python.exe main.py --doctor
```

模型 export 會下載 `openai/whisper-small` 並輸出 int8 OpenVINO 模型到 `models/whisper-small-int8-ov`。首次下載/export 可能需要較久；完成後會重用本地檔案。

若 NPU driver 或 OpenVINO 不可用，doctor 會明確標記失敗，程式仍可用 CUDA/CPU；不要把「OpenVINO 套件可 import」誤認為 NPU 推論成功。

## 2. 最簡單的使用方式

```powershell
.\.venv\Scripts\python.exe main.py "D:\video\直播.mkv"
```

也可以一次排入多部影片：

```powershell
.\.venv\Scripts\python.exe main.py "D:\video\早場.mp4" "D:\video\晚場.mkv"
```

預設會在來源旁建立 `直播_ayeai`。若指定 `--output-dir`，job 會寫到該目錄；輸入含中文、空白與一般 Windows 長路徑都可直接傳入。

預設模型是 `small`，兼顧中文品質、CUDA/CPU/NPU 可用性。RTX 5070 可在 benchmark 後選用較大的模型，例如：

```powershell
.\.venv\Scripts\python.exe main.py --benchmark "D:\video\直播.mkv"
.\.venv\Scripts\python.exe main.py --model large-v3-turbo "D:\video\直播.mkv"
```

`runtime/benchmark.json` 會記錄各 backend 的實測 real-time factor，並自動建議 60/90/120 秒 chunk 與 CPU thread 限制。GPU 只有在資源策略允許時才接新 chunk。

## 3. 檢查、進度與輸出

### Doctor

```powershell
.\.venv\Scripts\python.exe main.py --doctor
.\.venv\Scripts\python.exe main.py --doctor --json
```

doctor 會檢查 Python、FFmpeg/ffprobe、NVIDIA driver/GPU/VRAM/溫度、OpenVINO device/NPU、CPU backend、模型目錄、RAM 與磁碟。預設也會對已裝好的 CUDA、NPU、CPU backend 做實際短推論自測，首次會載入模型。

### 查進度

```powershell
.\.venv\Scripts\python.exe main.py --status "D:\video\直播_ayeai"
```

執行中的 console 會顯示 chunk、目前 backend、完成百分比與錯誤；完整 log 在 job 的 `logs/ayeai.log`，狀態摘要在 `status.json`。

輸出位置：

```text
直播_ayeai/
  transcript/transcript.json   # 含 timestamp 的逐字稿
  transcript/transcript.srt    # 可直接放入播放器
  transcript/transcript.txt
  highlights.json              # 分數、理由、上下文與確認狀態
  clips/candidate_*.mp4        # 通過 ffprobe 的候選影片
  review_corrupt/              # 解碼/timestamp 損壞的人工檢查片段
  checkpoints/                 # 每 chunk 的 atomic checkpoint
  state.db                     # SQLite 持久狀態
  backend_probe.json           # 啟動時 CUDA/NPU/CPU 實測
  COMPLETE.json                # 最後才寫入的完成 marker
```

高光預設使用本地 deterministic provider，綜合逐字稿密度、音量/情緒變化、驚嘆語句、重複語句與上下文。`highlighter.py` 保留 provider 介面，可替換為本地或 OpenAI-compatible LLM，不需要改 chunk 狀態與輸出格式。只有分數達到門檻的確認高光才會從原始影片重新由 FFmpeg 輸出候選 MP4，成品輸出後再次 ffprobe 驗證。

## 4. 暫停、續跑、停止

從另一個 PowerShell 視窗控制 job：

```powershell
# 暫停：目前 chunk 完成後不接新的工作
.\.venv\Scripts\python.exe main.py --pause "D:\video\直播_ayeai"

# 續跑：刪除 pause/stop flag，從未完成 chunk 開始
.\.venv\Scripts\python.exe main.py --resume "D:\video\直播_ayeai"

# 安全停止：保留目前與已完成 checkpoint
.\.venv\Scripts\python.exe main.py --stop "D:\video\直播_ayeai"
```

在正在執行的視窗按一次 Ctrl+C 也會安全停止；再執行原本的影片命令即可續跑。job lock 會阻止同一 job 同時被兩個程序處理；上一個程序已不存在時才會安全接管 stale lock。

## 5. 遊戲與資源保護

程式每個 chunk 邊界重新取樣 CPU、RAM、磁碟、GPU utilization/VRAM/溫度與 Windows 前景程序。偵測到 Steam、遊戲、Discord、瀏覽器、OBS 或高 CPU 前景負載時，GPU 暫停接新工作，優先嘗試已通過自測的 NPU；NPU 不可用或不適合時才使用受限 CPU。GPU/NPU 轉換有 cooldown/hysteresis，避免來回切換。

背景程序會嘗試設成 Windows `BELOW_NORMAL_PRIORITY_CLASS`；CPU backend 最多使用預設 4 threads 且以 CPU/RAM 門檻降速或暫停。磁碟剩餘、RAM、GPU 溫度或 VRAM 達危險門檻時，主流程安全暫停，不會刪除原始影片。

若遊戲使用特殊 launcher、前景程序名稱未被內建清單辨識，可在 job 目錄建立空白檔 `RESOURCE_BUSY`，下一個 chunk 起會停止接收 GPU 工作並優先轉 NPU；刪除該檔案後恢復一般自動策略。這是人工 emergency override，不會改動系統設定。

## 6. 損壞區段與人工處理

如果 ffmpeg 解碼、timestamp 或 backend 在某個 chunk 失敗，程式會有限次 retry；仍失敗則：

1. 記錄 chunk、位置、錯誤與 attempt 到 `state.db`。
2. 嘗試輸出前後約 15 秒到 `review_corrupt`。
3. 將該 chunk 隔離，主流程繼續處理其他 chunk。

先用播放器查看片段，再選擇：

```powershell
# 保留片段並標記已查看
.\.venv\Scripts\python.exe main.py --review "D:\video\直播_ayeai" --id 1 --action keep

# 明確跳過此區段；紀錄仍保留
.\.venv\Scripts\python.exe main.py --review "D:\video\直播_ayeai" --id 1 --action skip

# 修復來源後，讓 chunk 回到 queue
.\.venv\Scripts\python.exe main.py --review "D:\video\直播_ayeai" --id 1 --action retry

# 刪除 review 暫存檔，但 state.db 仍保留處理紀錄
.\.venv\Scripts\python.exe main.py --review "D:\video\直播_ayeai" --id 1 --action delete
```

程式不會因 overlap 中兩段文字不同就直接刪除；只有確認為相同文字的 overlap 才去重，時間戳異常則保留並記錄，避免誤刪語音。

## 7. 驗收與常見問題

最小驗收：

```powershell
.\.venv\Scripts\python.exe main.py --help
.\.venv\Scripts\python.exe main.py --doctor --json
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q .
```

## 8. BAT 與鍵盤互動 CLI UI

最簡單的方式是在專案資料夾雙擊 `AyeAI.bat`，或在 PowerShell 執行：

```powershell
.\AyeAI.bat
```

畫面會顯示水平選單。用左／右鍵選擇，再按 Enter 執行；Esc 返回上一層或離開。影片處理仍在背景執行，主畫面會定時顯示進度、目前 backend、損壞片段數與輸出資料夾。

控制目前 Job 時可選：

- `暫停`：目前 chunk 完成後暫停，不會刪除 checkpoint。
- `續跑`：清除 pause flag，從未完成 chunk 繼續。
- `停止`：送出安全停止要求，完成目前可安全收尾的工作後停止。
- `狀態`、`最近 Log`：查看進度與 `job\logs\ayeai.log`。

也可以把影片直接拖到命令提示字元，或使用：

```powershell
.\AyeAI.bat "D:\video\直播.mkv"
```

這會先啟動互動 UI，再自動開始該影片。`AyeAI.bat --doctor`、`AyeAI.bat --benchmark "D:\video\直播.mkv"` 等原本的 CLI 命令仍保持非互動模式；需要完整實測時也可直接使用 `python main.py --ui`。

若 UI 被關閉或電腦重開，重新執行 BAT 後選 `控制既有 Job`，輸入原本的 `*_ayeai` 資料夾，即可查看狀態並續跑；已完成 chunk 不會重算。原本的 `--pause`、`--resume`、`--stop` 與 `--review` 命令仍可使用。

若沒有 CUDA/NPU：doctor 仍應顯示 CPU backend；若 CPU 模型也不在 cache，第一次執行會由 faster-whisper 下載模型。若 CTranslate2 報 CUDA/cuDNN DLL 不相容，請先查看 `backend_probe.json` 與 doctor 的錯誤，不要修改系統 CUDA；可暫時用 `--backend cpu` 完成工作。

若輸入檔已損壞，`ffprobe` 可能在最前面拒絕整個來源；這是「沒有任何可安全定位時間軸」的情況。對能正常開啟但中途損壞的檔案，才會依 chunk 隔離 `review_corrupt` 並繼續。

## 9. 便攜版 EXE、三語與自動補全（繁體中文）

### 9.1 不安裝 Python 的使用方式

建置完成後，主要檔案是 `dist\AyeAI.exe`。`dist`、`build`、模型與 runtime 都刻意列入 `.gitignore`，不會被提交到 GitHub；GitHub 上保留可重建的原始碼與建置腳本。

最簡單的互動操作：

```powershell
.\AyeAI.bat
```

用左／右鍵移動選項，按 Enter 執行，Esc 返回或離開。也可以直接把影片路徑交給 BAT：

```powershell
.\AyeAI.bat "D:\video\直播.mkv"
```

若要直接執行單檔 EXE：

```powershell
.\dist\AyeAI.exe "D:\video\直播.mkv"
.\dist\AyeAI.exe --license --lang zh-TW
.\dist\AyeAI.exe --license --lang zh-CN
.\dist\AyeAI.exe --license --lang en
.\dist\AyeAI.exe --help
.\dist\AyeAI.exe --doctor
.\dist\AyeAI.exe --download-only
```

`AyeAI.bat` 是方便入口：沒有參數或傳入影片時進入鍵盤 UI；`--doctor`、`--benchmark`、`--status`、`--pause`、`--resume`、`--stop`、`--review` 等管理命令會保留 CLI 行為。

直接雙擊 `dist\\AyeAI.exe`（不帶參數）也會進入鍵盤 UI；只有 `--help`、`--license`、`--doctor`、`--download-only` 等一次性命令完成後才會返回並關閉 console。左右鍵切換時會使用 Windows 原生 `cls` 清屏，因此畫面只保留目前選單。

### 9.2 語言

支援三種語言：

- `zh-TW`：繁體中文。
- `zh-CN`：简体中文。
- `en`：English。
- `auto`：預設值，依 `AYEAI_LANG` 或 Windows 使用者地區自動選擇；無法判斷時使用繁體中文。

例如：

```powershell
.\AyeAI.bat --lang zh-TW
.\AyeAI.bat --lang zh-CN "D:\video\直播.mkv"
.\AyeAI.bat --lang en
.\.venv\Scripts\python.exe main.py --doctor --lang en --json
```

技術名稱、backend 名稱、檔名與 JSON key 保留英文，方便查錯與跨電腦比對；互動選單、啟動提示、授權聲明與主要狀態文字會依語言切換。

### 9.3 自動檢測與自動下載

EXE 不要求目標電腦預先安裝 Python、CUDA toolkit、OpenVINO toolkit 或 FFmpeg。啟動時會檢查：

1. Windows、Python runtime、CPU logical processors、RAM 與可用磁碟。
2. NVIDIA driver、GPU、VRAM，以及 CTranslate2/CUDA 是否能實際建立 CUDA backend。
3. OpenVINO CPU、GPU、NPU device；NPU 只在 OpenVINO 與對應驅動可見時列為可用。
4. CPU faster-whisper、CUDA faster-whisper、OpenVINO NPU 模型與 FFmpeg/ffprobe。

缺少檔案時會自動下載到使用者資料目錄，不修改系統 PATH、系統 CUDA、Python 或 registry：

```text
%LOCALAPPDATA%\AyeAI\
  tools\                 # FFmpeg/ffprobe
  models\                # OpenVINO IR 模型
  huggingface\           # faster-whisper 模型 cache
  runtime_manifest.json  # 版本、檢查結果與可恢復資訊
```

可用 `--data-dir` 改到其他磁碟；例如模型很大時：

```powershell
.\dist\AyeAI.exe --data-dir "D:\AyeAI-data" --download-only
```

第一次執行需要網路。已有 cache 時可離線執行；`--no-download` 會禁止補檔，缺檔就清楚回報而不偷偷改系統。若沒有 NPU，NPU 模型不會阻擋 CPU/CUDA 工作。

### 9.4 硬體策略與流暢度保護

`auto` 不硬編碼「哪個硬體一定最快」，而是用啟動實測與 benchmark 選擇 backend、chunk size、CPU thread 與並行度：

- 閒置時優先使用已通過實測的 CUDA GPU。
- 偵測到遊戲、前景高負載、GPU/VRAM/溫度風險時，停止 GPU 接受新 chunk，保留合理 cooldown 後切換到 NPU。
- NPU 不可用或不適合目前工作時，改用受限 CPU；CPU 使用低優先權、有限 thread 與 bounded queue。
- 每個 chunk 完成後重新評估資源；cooldown/hysteresis 防止 CUDA、NPU、CPU 頻繁震盪。
- 滑鼠、桌面、瀏覽器、Discord 與遊戲優先；危險的 RAM、VRAM、磁碟、溫度或負載會降速、暫停或等待，而不是吃滿 CPU。

可用 `--backend auto|cuda|npu|cpu` 做診斷或固定測試；正式長片建議保留 `auto`。

### 9.5 長片、佇列、checkpoint 與續跑

影片不會先切成大量實體檔案。系統用約 60–120 秒、少量 overlap、VAD-aware 的邏輯 chunk 建立 bounded queue；chunk 做完立即寫 SQLite 狀態、checkpoint、transcript cache 與 backend evidence。

下列情況重新執行原命令即可續跑：

- Ctrl+C、安全停止、BAT 關閉、工作管理員終止。
- 程式或電腦當機、斷電、睡眠、重開機。
- 遊戲啟動導致暫停或 backend 降級。
- 網路短暫中斷或模型下載中斷。

`input hash + config hash + model/backend identity` 會綁定工作；已完成 chunk 不會因為檔名相同而盲目重用，也不會因為重新啟動而重算。job lock、chunk state machine、atomic output 與 completion marker 會避免兩個程序同時處理同一工作。

### 9.6 損壞片段與人工檢查

來源先經 `ffprobe` 驗證。若中段 decode、timestamp 或媒體資料異常，系統會記錄時間位置，將錯誤前後約 10–20 秒匯出到：

```text
<job>\review_corrupt\
```

主流程繼續；合併 transcript 時依原始 timestamp、overlap 去重與異常 timestamp 規則處理，疑似語音寧可保留，不冒險誤刪。人工檢查後：

```powershell
.\dist\AyeAI.exe --review "D:\video\直播_ayeai" --id 1 --action keep
.\dist\AyeAI.exe --review "D:\video\直播_ayeai" --id 1 --action skip
.\dist\AyeAI.exe --review "D:\video\直播_ayeai" --id 1 --action retry
.\dist\AyeAI.exe --review "D:\video\直播_ayeai" --id 1 --action delete
```

`delete` 只刪除 review 暫存檔，`state.db` 仍保留處理紀錄，不會讓系統下次把同一損壞位置當成全新工作。

### 9.7 逐字稿、高光與候選影片

高光分析至少使用逐字稿、語音密度、音量/情緒變化、重複或驚嘆語句與上下文；分析完成並確認候選時間區段後，才讓 FFmpeg 從原始唯讀影片輸出 MP4。原始檔不會被覆蓋或重新編碼。

Job 目錄常見內容：

```text
<job>\
  transcript\transcript.json|srt|txt
  highlights.json
  clips\candidate_*.mp4
  review_corrupt\
  checkpoints\
  state.db
  backend_probe.json
  logs\ayeai.log
  status.json
  COMPLETE.json
```

每個候選 MP4 輸出後會再次 `ffprobe` 驗證；驗證失敗會留下錯誤證據，不宣稱完成。

### 9.8 建置單一 EXE

在開發機上只需：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build_windows_exe.ps1 -Clean
```

輸出為 `dist\AyeAI.exe`。建置腳本使用 PyInstaller one-file，將 Python runtime、AyeAI 程式、必要 backend、FFmpeg binaries、LICENSE、README 與可選 NPU 模型放入單檔；`build`/`dist` 不進 Git。

若要縮小初始檔案，可使用：

```powershell
.\scripts\build_windows_exe.ps1 -Clean -NoNpuModel
```

這會把 NPU 模型改為目標電腦第一次需要時自動下載。GPU/NPU 驅動是作業系統與硬體層，不能合法或安全地封裝進 EXE；目標電腦仍須安裝相容的 NVIDIA/Intel 驅動。這是單一 EXE 能跨電腦可靠工作的必要界線。

若 Windows 顯示 `was blocked by your organization's Device Guard policy`，代表本機組織政策拒絕未簽署的自建 EXE；這不是 AyeAI runtime 錯誤。請由系統管理員核准該檔案、提供受信任簽署版，或改用原始碼 `.venv`/BAT 測試；不要關閉 Device Guard。

### 9.9 嚴格授權聲明

本專案使用 `LICENSE` 的 **AyeAI Proprietary Software License — All Rights Reserved**，不是 MIT、Apache、GPL，也不是 OSI 開源授權。它只明確允許一名使用者在自己控制的裝置上，以未修改 EXE 作個人、內部、非商業使用；複製、公開鏡像、修改、fork、翻譯、散布、販售、出租、再授權、逆向工程、移除聲明、提供 SaaS/代處理服務等，都需要版權持有人事前書面許可。

公開 GitHub 原始碼只代表可查看，不代表授予開源使用權。FFmpeg、Python 套件、CUDA、OpenVINO、Hugging Face 與模型仍受各自第三方授權約束，請在使用前閱讀其條款。

在無 GUI 的 EXE 中，授權以啟動時的 console banner 顯示；`--license` 可單獨查看完整提醒，兩者都包含：

```text
https://github.com/Ray20123315/AyeAI
```

### 9.10 驗證結果與疑難排解

目前版本已驗證：

- Python 原始碼 `compileall` 通過。
- Python 測試 `15 passed`。
- CUDA、OpenVINO NPU、CPU backend 實際 probe；長片 transcript、高光與候選 MP4 end-to-end。
- pause/resume、Ctrl+C/強制中斷後續跑、資源退讓、損壞尾段隔離、`review_corrupt`、輸出 MP4 `ffprobe` 驗證。
- EXE `--help` exit 0；繁中、簡中、英文 `--license` 均 exit 0 且含 GitHub link。
- EXE `--doctor --no-full-probe --no-download --json` 會輸出檢測報告；在刻意不做完整轉錄 probe 的模式下 exit 2 是預期結果，不代表 EXE 無法啟動。

常用診斷：

```powershell
.\dist\AyeAI.exe --doctor --json
.\dist\AyeAI.exe --doctor --no-full-probe --no-download --json
.\dist\AyeAI.exe --benchmark "D:\video\直播.mkv" --json
```

若 CUDA DLL 不相容，先看 `doctor`、`backend_probe.json` 與 log；可使用 `--backend npu` 或 `--backend cpu`，不要為了 AyeAI 改動現有系統 CUDA。若下載失敗，確認網路與 `%LOCALAPPDATA%\AyeAI` 磁碟空間；若已有完整 cache，可用 `--no-download` 離線執行。

原始碼與發布位置：<https://github.com/Ray20123315/AyeAI>

## 简体中文

### 10.1 这是什么

AyeAI 是 Windows 11 的无 GUI 本地 AI 直播剪辑 CLI：`MP4/MKV → ffprobe 验证 → VAD-aware 逻辑分块 → 中文逐字稿 → 多信号高光分析 → 候选 MP4`。原始视频始终只读，完成的 chunk 会立即写入 SQLite、checkpoint 和 cache，电脑重启、断电、睡眠或 Ctrl+C 后可从未完成位置继续。

### 10.2 直接使用 EXE

```powershell
.\AyeAI.bat
.\AyeAI.bat "D:\video\直播.mkv"
.\dist\AyeAI.exe "D:\video\直播.mkv"
.\dist\AyeAI.exe --license --lang zh-CN
.\dist\AyeAI.exe --doctor --json
.\dist\AyeAI.exe --download-only
```

BAT 的交互菜单使用左右方向键和 Enter，Esc 返回或退出。`--lang` 支持 `zh-TW`、`zh-CN`、`en` 与 `auto`；`auto` 会读取 `AYEAI_LANG` 或 Windows 用户地区。

直接双击不带参数的 `dist\\AyeAI.exe` 会进入键盘 UI；`--help`、`--license`、`--doctor`、`--download-only` 是一次性 CLI 命令，完成后关闭 console。每次左右切换会使用 Windows 原生 `cls` 清屏，不会不断追加旧菜单。

### 10.3 自动检测与补全文件

EXE 会检测 CPU、RAM、磁盘、NVIDIA GPU/VRAM/驱动、CUDA backend、OpenVINO CPU/GPU/NPU device、FFmpeg/ffprobe 和模型。缺少文件时自动下载到 `%LOCALAPPDATA%\AyeAI`，不会修改系统 Python、CUDA、PATH 或 registry：

```text
%LOCALAPPDATA%\AyeAI\tools
%LOCALAPPDATA%\AyeAI\models
%LOCALAPPDATA%\AyeAI\huggingface
%LOCALAPPDATA%\AyeAI\runtime_manifest.json
```

可以用 `--data-dir "D:\AyeAI-data"` 改变位置。第一次运行需要网络；`--no-download` 用于离线和严格环境，只使用已有文件。

闲置时优先 CUDA；检测到游戏或系统高负载时停止 GPU 接受新任务并尝试 NPU；NPU 不适合时使用低优先级、有限线程的 CPU。bounded queue、cooldown/hysteresis、RAM/VRAM/温度/磁盘保护会避免影响游戏、鼠标、桌面、Discord 和浏览器。

### 10.4 续跑、损坏片段与输出

长视频按约 60–120 秒逻辑 chunk 处理，不预先制造大量切片。每个 chunk 完成后保存状态；同一个 input/config/model hash 不会重复计算。解码或 timestamp 异常会记录位置，把前后约 10–20 秒放到 `review_corrupt`，主流程继续。使用 `keep`、`skip`、`retry`、`delete` 处理；`delete` 只删 review 暂存，不删状态记录。

高光确认后才从原视频输出候选 MP4，并再次用 ffprobe 验证。结果在 job 目录的 `transcript`、`highlights.json`、`clips`、`review_corrupt`、`state.db` 和 `logs` 中。

### 10.5 建置与授权

```powershell
.\scripts\install_windows.ps1
.\scripts\build_windows_exe.ps1 -Clean
# 可选：不把 NPU 模型放入 EXE，首次使用时自动下载
.\scripts\build_windows_exe.ps1 -Clean -NoNpuModel
```

`LICENSE` 是 AyeAI Proprietary Software License — All Rights Reserved，不是开源许可证。只允许一名用户使用未修改 EXE 进行个人、内部、非商业处理；复制、修改、分发、销售、出租、再授权、逆向工程、移除声明、fork、翻译和提供 SaaS/代处理服务都需要版权所有者书面许可。第三方组件和模型仍遵守各自许可证。

EXE 每次启动会在 console 显示授权声明和 GitHub 链接；`--license` 可以单独显示：<https://github.com/Ray20123315/AyeAI>。目标电脑必须有相容的 NVIDIA/Intel 驱动；驱动不能安全地封装在 EXE 内。

### 10.6 检查命令

```powershell
.\dist\AyeAI.exe --help
.\dist\AyeAI.exe --doctor --json
.\.venv\Scripts\python.exe -m pytest -q
```

没有 CUDA/NPU 时仍可使用 CPU。遇到 CUDA DLL 不兼容，先看 doctor、`backend_probe.json` 和 log，再尝试 `--backend npu` 或 `--backend cpu`，不要修改系统 CUDA。

## English

### 11.1 What AyeAI does

AyeAI is a Windows 11, no-GUI local AI live-stream clipping CLI:

```text
MP4/MKV → ffprobe validation → VAD-aware logical chunks → Chinese transcript
       → multi-signal highlight analysis → verified candidate MP4 clips
```

The source video is always read-only. Each completed chunk is persisted to SQLite, checkpoints, and cache. Re-running the same job after Ctrl+C, a crash, power loss, sleep, or reboot resumes from the first unfinished chunk.

### 11.2 Run the portable EXE

```powershell
.\AyeAI.bat
.\AyeAI.bat "D:\video\live-stream.mkv"
.\dist\AyeAI.exe "D:\video\live-stream.mkv"
.\dist\AyeAI.exe --license --lang en
.\dist\AyeAI.exe --doctor --json
.\dist\AyeAI.exe --download-only
```

The BAT launcher opens the keyboard CLI UI. Use Left/Right to select an action, Enter to confirm, and Esc to go back or exit. `--lang` accepts `zh-TW`, `zh-CN`, `en`, and `auto`; `auto` uses `AYEAI_LANG` or the Windows user locale.

Double-clicking `dist\\AyeAI.exe` without arguments also opens the keyboard UI. One-shot commands such as `--help`, `--license`, `--doctor`, and `--download-only` exit after completion, so a console window launched by double-click will close normally. Windows redraws use native `cls`, keeping only the current menu visible.

### 11.3 Hardware detection and automatic completion

At startup AyeAI checks the Windows/Python runtime, CPU and RAM, disk space, NVIDIA driver/GPU/VRAM, the real CUDA backend, OpenVINO CPU/GPU/NPU devices, FFmpeg/ffprobe, and the required models. Missing files are downloaded into the per-user data directory without changing system Python, CUDA, PATH, or the registry:

```text
%LOCALAPPDATA%\AyeAI\tools
%LOCALAPPDATA%\AyeAI\models
%LOCALAPPDATA%\AyeAI\huggingface
%LOCALAPPDATA%\AyeAI\runtime_manifest.json
```

Use `--data-dir "D:\AyeAI-data"` to relocate this data. The first run needs network access; `--no-download` makes missing resources an explicit offline error instead of downloading anything.

The automatic policy prefers CUDA while the machine is idle, stops accepting new GPU work when a game or a high-load foreground task is detected, then tries OpenVINO NPU. If NPU is unavailable or unsuitable, it falls back to a low-priority, thread-limited CPU backend. Bounded queues, cooldown/hysteresis, RAM/VRAM/temperature/disk guards, and per-chunk re-evaluation protect games, the mouse, the desktop, Discord, and browsers.

### 11.4 Recovery and corrupt media

Long videos are processed as approximately 60–120 second logical chunks with small overlap; AyeAI does not create a huge set of physical slices in advance. Input/config/model hashes, a job lock, a chunk state machine, atomic promotion, and completion markers prevent stale or duplicate work.

If decoding, timestamps, or media data fail in the middle of a playable source, AyeAI records the location, exports roughly 10–20 seconds around it to `review_corrupt`, and continues. Review items support `keep`, `skip`, `retry`, and `delete`; `delete` removes only the temporary review media and retains the processed record.

Highlights use at least transcript content, speech density, volume/emotion changes, repeated or exclamatory phrases, and surrounding context. Only confirmed highlight intervals are sent to FFmpeg to create candidate MP4 files from the read-only source. Every candidate is validated with ffprobe before it is considered usable.

### 11.5 Build one EXE

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build_windows_exe.ps1 -Clean
# Optional smaller first download:
.\scripts\build_windows_exe.ps1 -Clean -NoNpuModel
```

The build produces `dist\AyeAI.exe` as a PyInstaller one-file executable containing the Python runtime, AyeAI code, required backend runtime, FFmpeg binaries, LICENSE, README, and optionally the NPU model. Generated `build`, `dist`, model, and runtime directories are ignored by Git. Hardware drivers cannot be bundled into a safe portable executable, so the target computer still needs compatible NVIDIA and/or Intel drivers.

### 11.6 Strict proprietary license

This project uses `LICENSE`: **AyeAI Proprietary Software License — All Rights Reserved**. It is intentionally not MIT, Apache, GPL, or an OSI open-source license. It permits one individual to run an unmodified executable for personal, internal, non-commercial use on devices they control. Copying, mirroring, modifying, forking, translating, redistributing, selling, renting, sublicensing, reverse engineering, removing notices, or providing hosted/SaaS/bureau processing requires prior written permission from the copyright holder.

Public source visibility on GitHub grants no open-source rights. FFmpeg, Python packages, CUDA, OpenVINO, Hugging Face components, and models remain subject to their own third-party licenses. Review those licenses before redistribution or commercial use.

Every frozen EXE startup prints the license notice in the console; `--license` prints it on demand, including the project link:

```text
https://github.com/Ray20123315/AyeAI
```

### 11.7 Verification and troubleshooting

The current release has passed Python compilation, 13 automated tests, real CUDA/OpenVINO NPU/CPU probes, Chinese transcript/highlight/candidate end-to-end processing, pause/resume and forced-interruption recovery, resource fallback, corrupt-tail isolation, review actions, and playable-output ffprobe validation. The rebuilt EXE has passed `--help`, all three language license banners, and the no-full-probe doctor smoke test.

```powershell
.\dist\AyeAI.exe --doctor --json
.\dist\AyeAI.exe --doctor --no-full-probe --no-download --json
.\dist\AyeAI.exe --benchmark "D:\video\live-stream.mkv" --json
```

If CUDA DLLs are incompatible, inspect doctor output, `backend_probe.json`, and the job log, then use `--backend npu` or `--backend cpu`; do not replace the system CUDA installation just for AyeAI. If downloads fail, check network access and free space under `%LOCALAPPDATA%\AyeAI`; a complete cache can be used with `--no-download`.

Source repository: <https://github.com/Ray20123315/AyeAI>
