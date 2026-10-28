@echo off
title StemLab - instalacao
cd /d "%~dp0"

echo ===============================================
echo  StemLab - instalacao do ambiente
echo ===============================================
echo.

where uv >nul 2>nul
if errorlevel 1 (
  echo Instalando uv...
  python -m pip install --user uv || goto :erro
)

echo [1/3] Baixando Python 3.13 (isolado, nao altera o seu Python)...
python -m uv python install 3.13 || goto :erro

echo [2/3] Criando ambiente virtual...
python -m uv venv --python 3.13 ".venv" || goto :erro

echo [3/3] Instalando PyTorch (CPU) + Demucs + FastAPI...
python -m uv pip install ^
  --python ".venv\Scripts\python.exe" ^
  --index-url https://download.pytorch.org/whl/cpu ^
  --extra-index-url https://pypi.org/simple ^
  torch torchaudio demucs fastapi "uvicorn[standard]" soundfile python-multipart numpy || goto :erro

echo.
echo Verificando ffmpeg...
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo AVISO: ffmpeg nao encontrado.
  echo Instale com:  winget install Gyan.FFmpeg
) else (
  echo ffmpeg: OK
)

echo.
echo Instalacao concluida. Agora abra "Iniciar StemLab.bat".
pause
exit /b 0

:erro
echo.
echo FALHA na instalacao. Veja as mensagens acima.
pause
exit /b 1