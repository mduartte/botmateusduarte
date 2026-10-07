@echo off
rem Instala as dependências (rode uma vez depois de clonar). Requer Python 3.12+.
cd /d "%~dp0"
python -m venv .venv
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" -m playwright install chromium
echo.
echo Pronto. Rode interface.bat para abrir a interface.
pause
