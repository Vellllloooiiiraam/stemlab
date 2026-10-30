# Gera o executavel portatil do StemLab.
#
#   .\build_exe.ps1
#
# Resultado:
#   dist\StemLab\StemLab.exe      -> abra este arquivo
#   StemLab-portatil.zip          -> envie este para outra pessoa
$ErrorActionPreference = "Continue"
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proj

Write-Output "=============================================="
Write-Output " StemLab - build do executavel"
Write-Output "=============================================="

$py = "$proj\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Output "Ambiente .venv nao encontrado. Rode instalar.bat antes."
    exit 1
}

# [1/5] ffmpeg portatil ------------------------------------------------
Write-Output ""
Write-Output " [1/5] preparando ffmpeg..."
$binDir = "$proj\build_bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$ff = (& $py -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" 2>$null | Select-Object -First 1)
if ($ff) { $ff = $ff.Trim() }
if (-not (Test-Path $ff)) {
    Write-Output "ffmpeg do imageio-ffmpeg nao encontrado. Rode: uv pip install imageio-ffmpeg"
    exit 1
}
Copy-Item $ff "$binDir\ffmpeg.exe" -Force
Write-Output ("         ffmpeg {0:N1} MB" -f ((Get-Item "$binDir\ffmpeg.exe").Length / 1MB))

# [2/5] limpar ---------------------------------------------------------
Write-Output ""
Write-Output " [2/5] limpando builds anteriores..."
Remove-Item "$proj\build", "$proj\dist" -Recurse -Force -ErrorAction SilentlyContinue

# [3/5] PyInstaller ----------------------------------------------------
Write-Output ""
Write-Output " [3/5] rodando PyInstaller (demora alguns minutos)..."
& $py -m PyInstaller --noconfirm --clean "stemlab.spec" *> "$proj\build_pyinstaller.log"
if ($LASTEXITCODE -ne 0) {
    Write-Output "PyInstaller falhou (exit $LASTEXITCODE). Veja build_pyinstaller.log"
    Get-Content "$proj\build_pyinstaller.log" -Tail 25
    exit 1
}
Remove-Item "$proj\build_pyinstaller.log" -Force -ErrorAction SilentlyContinue
Write-Output "         concluido"

# [4/5] limpar restos --------------------------------------------------
Write-Output ""
Write-Output " [4/5] limpando o pacote..."
Remove-Item "$proj\build" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$proj\dist\StemLab\StemLab dados" -Recurse -Force -ErrorAction SilentlyContinue

# [5/5] zip ------------------------------------------------------------
Write-Output ""
Write-Output " [5/5] gerando o zip para enviar..."
$zip = "$proj\StemLab-portatil.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path "$proj\dist\StemLab" -DestinationPath $zip -CompressionLevel Optimal

$distMB = [math]::Round((Get-ChildItem "$proj\dist\StemLab" -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 0)
$zipMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Output ""
Write-Output "=============================================="
Write-Output " PRONTO"
Write-Output "=============================================="
Write-Output "  executavel   : $proj\dist\StemLab\StemLab.exe"
Write-Output "  pasta dist   : $distMB MB (descompactado)"
Write-Output "  zip p/ enviar: $zip  ($zipMB MB)"
Write-Output ""
Write-Output "  Envie o .zip. A pessoa extrai e abre StemLab.exe."
