@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo AyeAI .venv was not found.
    echo Run: powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
    endlocal & exit /b 2
)

pushd "%ROOT%"
if "%~1"=="" (
    "%PYTHON%" "%ROOT%main.py" --ui
) else if /I "%~1"=="--help" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~1"=="--license" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~1"=="--doctor" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~1"=="--benchmark" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~1"=="--download-only" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~1"=="--status" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~1"=="--pause" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~1"=="--resume" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~1"=="--stop" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~1"=="--review" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~1"=="--ui" (
    "%PYTHON%" "%ROOT%main.py" %*
) else if /I "%~x1"==".mp4" (
    "%PYTHON%" "%ROOT%main.py" --ui %*
) else if /I "%~x1"==".mkv" (
    "%PYTHON%" "%ROOT%main.py" --ui %*
) else (
    "%PYTHON%" "%ROOT%main.py" --ui %*
)
set "EXIT_CODE=%ERRORLEVEL%"
popd
endlocal & exit /b %EXIT_CODE%
