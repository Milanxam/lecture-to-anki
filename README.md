# Lecture to Anki

Automatically convert lecture videos with subtitles into Anki flashcard decks.

This tool processes lecture recordings, detects slide changes, extracts the spoken content, and uses AI to generate exam-style Q&A flashcards. Perfect for students who want to create study materials from recorded lectures.

## Features

- **Batch Processing**: Process multiple lectures in one run (10-20+ hours of content)
- **Slide Detection**: Automatically detects slide changes using computer vision (SSIM)
- **AI Card Generation**: Uses Poe API (Claude-3.5-Sonnet) to create exam-style flashcards
- **Media Attachments**: Each card includes screenshots and audio clips
- **Keyword Highlighting**: Answer keywords are highlighted in the transcript
- **Timestamps**: Each card shows the exact timestamp in the lecture
- **Offline Deck Generation**: Creates `.apkg` files that can be imported into Anki

## Requirements

- Python 3.8+
- FFmpeg (must be installed and in PATH)
- Poe API key

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/lecture-to-anki.git
cd lecture-to-anki
```

### 2. Create virtual environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Install FFmpeg

**Ubuntu/Debian:**
```bash
sudo apt update && sudo apt install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

**Windows:**
Download from https://ffmpeg.org/download.html and add to PATH.

## Usage

### Basic Usage

Place your lecture videos and subtitle files in a directory:

```
lectures/
├── lecture01.mp4
├── lecture01.vtt
├── lecture02.mp4
├── lecture02.vtt
└── ...
```

Run the processor:

```bash
python lecture_to_anki.py \
    --input-dir ./lectures \
    --output-dir ./output \
    --api-key YOUR_POE_API_KEY
```

### Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--input-dir, -i` | Directory with video + VTT files | Required |
| `--output-dir, -o` | Output directory for .apkg files | `./output` |
| `--temp-dir, -t` | Temporary files directory | `./temp` |
| `--api-key, -k` | Poe API key | `$POE_API_KEY` env var |
| `--prompt-file, -p` | Custom prompt for card generation | `./prompts/baugeschichte_exam_prompt.txt` |
| `--ssim-threshold` | Slide detection sensitivity (0-1) | `0.85` |

### Environment Variables

```bash
export POE_API_KEY="your_api_key_here"
```

## File Naming Convention

The script automatically pairs video and subtitle files:

| Video File | Subtitle File Options |
|------------|----------------------|
| `lecture01.mp4` | `lecture01.vtt`, `lecture01_subtitles.vtt`, `lecture01_captions.vtt` |

## Output

Each lecture generates:

1. **`lecture_name.apkg`** - Anki deck file with all cards and media
2. **`processing_log.json`** - Processing summary and statistics

### Card Format

**Front:**
```
[Folie 5] 00:12:34
Woran erkennt man die Romanik?
```

**Back:**
```
Rundbogenfenster, massive Mauern, Würfelkapitelle

[Screenshot from lecture]
[Audio clip]

▶ Sprechtext (Folie 5)
  "Die Romanik erkennt man an den typischen Rundbogenfenstern..."
```

## VPS Deployment

### Digital Ocean Setup

1. Create a Droplet (recommended: 4GB RAM, 2 vCPUs)

2. SSH into your server:
```bash
ssh root@your_server_ip
```

3. Install system dependencies:
```bash
apt update && apt install -y python3 python3-pip python3-venv ffmpeg git
```

4. Clone and setup:
```bash
git clone https://github.com/YOUR_USERNAME/lecture-to-anki.git
cd lecture-to-anki
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

5. For headless servers, use opencv-python-headless:
```bash
pip uninstall opencv-python
pip install opencv-python-headless
```

6. Upload your lectures:
```bash
scp -r ./lectures root@your_server_ip:/root/lecture-to-anki/lectures
```

7. Run in background with screen/tmux:
```bash
screen -S anki
python lecture_to_anki.py -i ./lectures -o ./output -k YOUR_API_KEY
# Ctrl+A, D to detach
```

8. Download results:
```bash
scp -r root@your_server_ip:/root/lecture-to-anki/output ./
```

### Running with Docker (Optional)

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip uninstall -y opencv-python && pip install opencv-python-headless

COPY . .

ENTRYPOINT ["python", "lecture_to_anki.py"]
```

Build and run:
```bash
docker build -t lecture-to-anki .
docker run -v $(pwd)/lectures:/app/lectures -v $(pwd)/output:/app/output \
    -e POE_API_KEY=your_key lecture-to-anki -i /app/lectures -o /app/output
```

## Customizing the Prompt

Edit `prompts/baugeschichte_exam_prompt.txt` to customize card generation for your subject.

The default prompt is optimized for architectural history exams (Baugeschichte) and generates cards in German.

## Processing Time Estimates

| Lecture Length | Estimated Time | Cards Generated |
|----------------|----------------|-----------------|
| 1 hour | ~10-15 min | 50-100 cards |
| 2 hours | ~20-30 min | 100-200 cards |
| 10 hours | ~2-3 hours | 500-1000 cards |

*Times depend on API response speed and slide density.*

## Troubleshooting

### "No subtitle file found"
Ensure your VTT file has the same base name as the video file.

### FFmpeg errors
Make sure FFmpeg is installed and accessible: `ffmpeg -version`

### API rate limits
The script makes one API call per slide. For large batches, consider adding delays.

### Memory issues on VPS
Reduce batch size or use a larger instance. Frame extraction is memory-intensive.

## License

MIT License - see LICENSE file.

## Contributing

Pull requests welcome! Please open an issue first to discuss major changes.

