@echo off
rem Genera portable\VibranceAMD.exe (requiere Python 3.9+ de 64 bits)
cd /d "%~dp0"
python -m pip install pystray pillow pyinstaller || exit /b 1
python vibrance_amd.py --ico icon.ico || exit /b 1
python -m PyInstaller --noconfirm --onefile --noconsole --name VibranceAMD --icon "%~dp0icon.ico" --distpath portable vibrance_amd.py || exit /b 1
copy /y config.json portable\config.json
echo Listo: portable\VibranceAMD.exe
