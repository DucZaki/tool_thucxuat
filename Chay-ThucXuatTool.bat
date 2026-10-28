@echo off
cd /d "%~dp0"

if not exist "%~dp0ThucXuatTool.exe" (
  echo LOI: Khong thay ThucXuatTool.exe trong folder nay.
  pause
  exit /b 1
)

if not exist "%~dp0_internal\python312.dll" (
  echo LOI: Thieu folder _internal ^(hoac python312.dll^).
  echo.
  echo Ban chi copy file .exe la KHONG DU.
  echo Hay giai nen LAI toan bo file zip, giu nguyen:
  echo   ThucXuatTool.exe
  echo   _internal\
  echo.
  echo Neu da co _internal ma van loi: cai VC++ Redistributable x64
  echo https://aka.ms/vs/17/release/vc_redist.x64.exe
  pause
  exit /b 1
)

start "" "%~dp0ThucXuatTool.exe"
