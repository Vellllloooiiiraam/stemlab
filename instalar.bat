@echo off
setlocal
title StemLab - instalacao
cd /d "%~dp0"

echo ===============================================
echo  StemLab - instalacao do ambiente
echo ===============================================
echo.

REM ---------------------------------------------------------------------
REM  O "uv" instala o Python sozinho, entao ele vem antes de tudo.
REM  Este script NAO exige que o Python ja esteja instalado no PC.
REM ---------------------------------------------------------------------
set "UV="
where uv >nul 2>nul
if not errorlevel 1 set "UV=uv"

if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "UV=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"

if not defined UV (
  echo Instalando o "uv" ^(baixador de Python^)...
  where winget >nul 2>nul
  if not errorlevel 1 (
    winget install --id astral-sh.uv -e --source winget --accept-source-agreements --accept-package-agreements
  )
  if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
  if not defined UV if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "UV=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
  if not defined UV (
    where uv >nul 2>nul
    if not errorlevel 1 set "UV=uv"
  )
)

if not defined UV (
  echo Baixando o instalador oficial do "uv"...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
)

if not defined UV (
  echo.
  echo ERRO: nao foi possivel instalar o "uv".
  echo Confira a conexao com a internet e tente novamente.
  goto :erro
)

echo uv: %UV%
echo.

echo [1/4] Baixando o Python 3.13 ^(isolado, nao mexe no Python do sistema^)...
"%UV%" python install 3.13 || goto :erro

if exist ".venv\Scripts\python.exe" (
  echo [2/4] Ambiente virtual ja existe, reutilizando...
) else (
  echo [2/4] Criando o ambiente virtual...
  "%UV%" venv --python 3.13 ".venv" || goto :erro
)

echo [3/4] Instalando as dependencias (requirements.txt)...
echo        ^(PyTorch CPU + Demucs + FastAPI; o download passa de 500 MB^)...
"%UV%" pip install ^
  --python ".venv\Scripts\python.exe" ^
  --index-url https://download.pytorch.org/whl/cpu ^
  --extra-index-url https://pypi.org/simple ^
  -r requirements.txt || goto :erro

echo [4/4] Verificando as dependencias...
".venv\Scripts\python.exe" -c "import torch, torchaudio, demucs, fastapi, uvicorn, soundfile, numpy, multipart" || goto :erro
echo        dependencias: OK

echo.
echo Verificando o ffmpeg...
where ffmpeg >nul 2>nul
if not errorlevel 1 goto :ffmpeg_ok

where winget >nul 2>nul
if not errorlevel 1 (
  echo Instalando o ffmpeg via winget...
  winget install --id Gyan.FFmpeg -e --source winget --accept-source-agreements --accept-package-agreements
)

where ffmpeg >nul 2>nul
if not errorlevel 1 goto :ffmpeg_ok

echo AVISO: ffmpeg nao encontrado. O StemLab abre, mas alguns formatos
echo        ^(mp3, m4a...^) podem falhar. Instale com:
echo          winget install Gyan.FFmpeg
goto :fim

:ffmpeg_ok
echo ffmpeg: OK

:fim
echo.
echo ===============================================
echo  Instalacao concluida!
echo ===============================================
echo Agora abra "Iniciar StemLab.bat".
pause
exit /b 0

:erro
echo.
echo FALHA na instalacao. Veja as mensagens acima.
pause
exit /b 1
