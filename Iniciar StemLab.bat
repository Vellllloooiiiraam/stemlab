@echo off
title StemLab
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  Ambiente nao encontrado. Rode primeiro:  instalar.bat
  echo.
  pause
  exit /b 1
)

echo.
echo  Iniciando StemLab... o navegador vai abrir sozinho.
echo.
".venv\Scripts\python.exe" run.py

echo.
echo  StemLab encerrado.
pause