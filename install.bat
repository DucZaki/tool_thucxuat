@echo off
cd /d "%~dp0"
echo Cai dat thuc-xuat-tool (can Python 3.12 64-bit)...
py -3.12 -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo LOI: Dung Python 3.12 64-bit. Kiem tra: py -0p
  pause
  exit /b 1
)
echo.
echo Xong. Chay: run.bat
pause
