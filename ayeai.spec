# PyInstaller one-file build for Windows. Python, code, native runtimes and
# FFmpeg are packaged; models are completed in %LOCALAPPDATA%\\AyeAI.
from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_all

ROOT = Path(globals().get("SPECPATH") or os.getcwd()).resolve()
datas = [(str(ROOT / "LICENSE"), "."), (str(ROOT / "README.md"), ".")]
binaries = []
hiddenimports = []

for package in ("faster_whisper", "ctranslate2", "openvino", "openvino_genai", "huggingface_hub"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
        datas.extend(package_datas)
        binaries.extend(package_binaries)
        hiddenimports.extend(package_hidden)
    except Exception:
        # Optional NPU/CUDA packages may be absent in a CPU-only build host.
        pass

ffmpeg_dir = ROOT / "build" / "vendor" / "ffmpeg"
for filename in ("ffmpeg.exe", "ffprobe.exe"):
    source = ffmpeg_dir / filename
    if source.exists():
        binaries.append((str(source), "vendor/ffmpeg"))

include_npu_model = os.environ.get("AYEAI_EXE_INCLUDE_NPU_MODEL", "1") == "1"
npu_model = ROOT / "models" / "whisper-small-int8-ov"
if include_npu_model and npu_model.exists():
    datas.append((str(npu_model), "models/whisper-small-int8-ov"))

datas = list(dict.fromkeys(datas))
binaries = list(dict.fromkeys(binaries))
hiddenimports = sorted(set(hiddenimports) | {"ayeai.bootstrap", "ayeai.i18n", "ayeai.license", "ayeai.runtime", "ayeai.ui"})

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT), str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AyeAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
