# Lecture to Anki - Batch Processing Script (Windows PowerShell)

$ErrorActionPreference = "Stop"

# Configuration
$InputDir = if ($env:INPUT_DIR) { $env:INPUT_DIR } else { ".\lectures" }
$OutputDir = if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { ".\output" }
$TempDir = if ($env:TEMP_DIR) { $env:TEMP_DIR } else { ".\temp" }

# Check for API key
if (-not $env:POE_API_KEY) {
    Write-Host "Error: POE_API_KEY environment variable not set" -ForegroundColor Red
    Write-Host 'Usage: $env:POE_API_KEY="your_key"; .\run_batch.ps1'
    exit 1
}

# Check for FFmpeg
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "Error: FFmpeg is not installed or not in PATH" -ForegroundColor Red
    exit 1
}

# Activate virtual environment if it exists
if (Test-Path "venv\Scripts\Activate.ps1") {
    & "venv\Scripts\Activate.ps1"
}

# Create directories
New-Item -ItemType Directory -Force -Path $InputDir | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

Write-Host "========================================"
Write-Host "Lecture to Anki - Batch Processor"
Write-Host "========================================"
Write-Host "Input:  $InputDir"
Write-Host "Output: $OutputDir"
Write-Host ""

# Count videos
$VideoCount = (Get-ChildItem -Path $InputDir -Filter "*.mp4" -File).Count
$VideoCount += (Get-ChildItem -Path $InputDir -Filter "*.mkv" -File -ErrorAction SilentlyContinue).Count
Write-Host "Found $VideoCount video files"

if ($VideoCount -eq 0) {
    Write-Host "No video files found in $InputDir" -ForegroundColor Yellow
    Write-Host "Please add .mp4 files with matching .vtt subtitle files"
    exit 1
}

# Run the processor
python lecture_to_anki.py `
    --input-dir $InputDir `
    --output-dir $OutputDir `
    --temp-dir $TempDir `
    --api-key $env:POE_API_KEY

Write-Host ""
Write-Host "========================================"
Write-Host "Processing complete!" -ForegroundColor Green
Write-Host "Output files are in: $OutputDir"
Write-Host "========================================"

