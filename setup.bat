@echo off
rem Instala as dependencias (rode uma vez depois de baixar). Requer Python 3.12+.
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
  echo ERRO: Python nao encontrado.
  echo Instale o Python 3.12 ou mais novo em https://www.python.org/downloads/
  echo e marque a opcao "Add python.exe to PATH" na primeira tela do instalador.
  echo Depois feche esta janela e rode o setup.bat de novo.
  pause
  exit /b 1
)

echo [1/3] Criando ambiente Python...
if not exist ".venv\Scripts\python.exe" (python -m venv .venv || goto erro)
echo [2/3] Instalando bibliotecas...
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto erro
echo [3/3] Baixando o navegador do robo (pode demorar alguns minutos)...
".venv\Scripts\python.exe" -m playwright install chromium || goto erro

echo.
echo Pronto. Rode interface.bat para abrir a interface.
pause
exit /b 0

:erro
echo.
echo ERRO na instalacao. Feche a interface (janela preta do interface.bat), verifique
echo sua internet e rode o setup.bat de novo.
pause
exit /b 1
