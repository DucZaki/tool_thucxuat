@echo off
setlocal
cd /d "%~dp0"

echo === Dong goi Thuc Xuat Tool ===
echo.

py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo LOI: Can Python 3.12 64-bit. Tai tu python.org
  pause
  exit /b 1
)

echo [1/5] Cai thu vien...
py -3.12 -m pip install -r requirements.txt -q
py -3.12 -m pip install -r build-requirements.txt -q
if errorlevel 1 (
  echo LOI pip install
  pause
  exit /b 1
)

echo [2/5] Build .exe (PyInstaller)...
py -3.12 -m PyInstaller --noconfirm --clean ThucXuatTool.spec
if errorlevel 1 (
  echo LOI build
  pause
  exit /b 1
)

set OUT=dist\ThucXuatTool
echo [3/5] Them file huong dan...
copy /Y config.json.example "%OUT%\config.json.example" >nul
copy /Y HUONG-DAN-NGUOI-DUNG.txt "%OUT%\" >nul
copy /Y Chay-ThucXuatTool.bat "%OUT%\" >nul
REM Bo file cua may dev neu co (khong dua vao ban phat hanh)
if exist "%OUT%\config.json" del "%OUT%\config.json"
if exist "%OUT%\KetQua" rmdir /S /Q "%OUT%\KetQua"

echo [4/5] Nen file zip ^(giu nguyen 1 folder^)...
set ZIP=dist\ThucXuatTool-win64.zip
if exist "%ZIP%" del "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path '%OUT%' -DestinationPath '%ZIP%' -Force"

echo [5/5] Build bo cai mot cham (ThucXuatTool-Setup.exe)...
py -3.12 build_installer.py --no-app
if errorlevel 1 (
  echo LOI build bo cai
  pause
  exit /b 1
)

echo.
echo XONG!
echo   Thu muc:      %OUT%
echo   File zip:     %ZIP%              ^(cho tu cap nhat / chay truc tiep^)
echo   Bo cai:       dist\ThucXuatTool-Setup.exe  ^(gui nguoi dung moi^)
echo.
echo Nguoi dung moi: chay ThucXuatTool-Setup.exe — tu cai Ollama + model.
echo Ban cu: app tu hien nut «Cap nhat» khi mo.
pause
