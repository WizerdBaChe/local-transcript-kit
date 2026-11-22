# setup-runtime.ps1 -- build the speech runtime that `ltm decode` calls (Windows PowerShell 5.1 or 7).
#
#   powershell -ExecutionPolicy Bypass -File setup-runtime.ps1            # asks before downloading
#   powershell -ExecutionPolicy Bypass -File setup-runtime.ps1 -Yes       # you already agreed
#   powershell -ExecutionPolicy Bypass -File setup-runtime.ps1 -SkipModel # interpreter only
#
# What it does, and nothing else:
#   1. a Python 3.12 venv at reference-impl\runtime\asr-py (the path ltm looks at by default,
#      src\ltm\engine\contract.py DEFAULT_SIDECAR_PYTHON), with faster-whisper==1.2.1 from PyPI;
#   2. the weights of Hugging Face Systran/faster-whisper-large-v3 (MIT) into
#      reference-impl\runtime\models\faster-whisper-large-v3 (DEFAULT_MODEL_DIR), about 3.1 GB,
#      each file checked against the SHA-256 recorded below (taken from the copy the author decodes with).
# It changes no PATH and no environment variable; what it installs lands in reference-impl\runtime
# (pip and huggingface_hub may still use their own caches under your profile).
# ltm itself needs none of this to install, run its tests, or open the review page; only decode does.
param([switch]$Yes, [switch]$SkipModel)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Here "reference-impl\runtime"
$Venv = Join-Path $Runtime "asr-py"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$ModelDir = Join-Path $Runtime "models\faster-whisper-large-v3"
$Pins = [ordered]@{
    "config.json"              = "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9"
    "model.bin"                = "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"
    "preprocessor_config.json" = "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"
    "tokenizer.json"           = "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca"
    "vocabulary.json"          = "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"
}

Write-Host "This will download from the network:"
Write-Host "  - faster-whisper 1.2.1 and its dependencies from PyPI (several hundred MB, includes ctranslate2 and PyAV)"
if (-not $SkipModel) { Write-Host "  - Systran/faster-whisper-large-v3 from Hugging Face (about 3.1 GB)" }
Write-Host "into $Runtime"
if (-not $Yes) {
    $answer = Read-Host "Continue? (y/N)"
    if ($answer -notmatch '^(y|yes)$') { Write-Host "Nothing downloaded."; exit 1 }
}

# 1. the interpreter -----------------------------------------------------------------------------
if (-not (Test-Path $VenvPy)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { & py -3.12 -m venv $Venv } else { & python -m venv $Venv }
    if ($LASTEXITCODE -ne 0) { throw "could not create the venv (Python 3.12 is required: https://www.python.org/downloads/)" }
}
$ver = & $VenvPy -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($ver -ne "3.12") { throw "the runtime venv is Python $ver; ltm was tested with 3.12 only. Delete $Venv and install Python 3.12." }
& $VenvPy -m pip install --disable-pip-version-check "faster-whisper==1.2.1"
if ($LASTEXITCODE -ne 0) { throw "pip install faster-whisper==1.2.1 failed" }

# 2. the weights -----------------------------------------------------------------------------------
if (-not $SkipModel) {
    New-Item -ItemType Directory -Force $ModelDir | Out-Null
    $files = ($Pins.Keys | ForEach-Object { "'$_'" }) -join ","
    & $VenvPy -c "from huggingface_hub import hf_hub_download as d; [d('Systran/faster-whisper-large-v3', f, local_dir=r'$ModelDir') for f in [$files]]"
    if ($LASTEXITCODE -ne 0) { throw "download from Hugging Face failed" }
    foreach ($name in $Pins.Keys) {
        $got = (Get-FileHash (Join-Path $ModelDir $name) -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $Pins[$name]) { throw "$name has SHA-256 $got, expected $($Pins[$name]): the upstream files changed or the download is damaged" }
        Write-Host "ok  $name"
    }
}

Write-Host ""
Write-Host "Runtime ready. Check it with:"
Write-Host "  `$env:PYTHONPATH = 'reference-impl\src'; python -m ltm agent which"
Write-Host "ffprobe (from ffmpeg) must also be on PATH: decode measures each recording's length with it."
