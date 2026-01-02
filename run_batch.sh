#!/bin/bash
# Lecture to Anki - Batch Processing Script
# For use on VPS or local development

set -e

# Configuration
INPUT_DIR="${INPUT_DIR:-./lectures}"
OUTPUT_DIR="${OUTPUT_DIR:-./output}"
TEMP_DIR="${TEMP_DIR:-./temp}"

# Check for API key
if [ -z "$POE_API_KEY" ]; then
    echo "Error: POE_API_KEY environment variable not set"
    echo "Usage: POE_API_KEY=your_key ./run_batch.sh"
    exit 1
fi

# Check for FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "Error: FFmpeg is not installed"
    exit 1
fi

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Create directories
mkdir -p "$INPUT_DIR" "$OUTPUT_DIR" "$TEMP_DIR"

echo "========================================"
echo "Lecture to Anki - Batch Processor"
echo "========================================"
echo "Input:  $INPUT_DIR"
echo "Output: $OUTPUT_DIR"
echo ""

# Count videos
VIDEO_COUNT=$(find "$INPUT_DIR" -maxdepth 1 -type f \( -name "*.mp4" -o -name "*.mkv" -o -name "*.avi" \) | wc -l)
echo "Found $VIDEO_COUNT video files"

if [ "$VIDEO_COUNT" -eq 0 ]; then
    echo "No video files found in $INPUT_DIR"
    echo "Please add .mp4 files with matching .vtt subtitle files"
    exit 1
fi

# Run the processor
python lecture_to_anki.py \
    --input-dir "$INPUT_DIR" \
    --output-dir "$OUTPUT_DIR" \
    --temp-dir "$TEMP_DIR" \
    --api-key "$POE_API_KEY"

echo ""
echo "========================================"
echo "Processing complete!"
echo "Output files are in: $OUTPUT_DIR"
echo "========================================"

