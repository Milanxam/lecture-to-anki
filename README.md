# Lecture to Anki

Turn lecture videos + subtitles into high-quality Anki decks, driven by a
**single course-level exam context** file.

## Course folder structure (input)

Point `-i` at the **course folder**. Exam info lives once at the course root;
each week only needs video + subtitles:

```
Course Name/                                    ← pass this to -i
├── context/                                    ← shared by every lecture
│   ├── README.txt
│   ├── exam_context.txt                        ← exam format / competencies / card guidance
│   ├── exam_context.TEMPLATE.txt
│   └── additional_context/                     ← optional PDFs, notes, images
├── 01 - 2026-02-20 - Lecture title/
│   ├── video_720p.mp4
│   └── subtitles_en-US.vtt
├── 02 - 2026-02-27 - Lecture title/
│   ├── video_720p.mp4
│   └── subtitles_en-US.vtt
└── …
```

`context/exam_context.txt` is the primary filter for every week’s cards
(MCQ factual recall vs essay-comparison prep, dating detail, what to ignore).

Card **formulation** follows SuperMemo’s *Twenty rules of formulating knowledge*
(`prompts/supermemo_formulating_knowledge.txt`): minimum information (one fact per
card), short answers, prefer cloze, avoid sets/long enumerations, combat
interference. Exam context decides *what* is worth a card; SuperMemo rules decide
*how* each card is written.

If it is missing, the script asks you to write it, generate it from
`additional_context/`, or generate it from a short description.

## Output folder structure

```
output/
├── lecture_index.json
└── Course_Name/
    ├── 01_-_2026-02-20_-_Lecture_title.apkg
    ├── analysis/
    │   └── 01_…_analysis.pdf
    └── ocr/
        └── 01_-_2026-02-20_-_Lecture_title/
            ├── timestamps.txt
            └── 01_00-12-34.png
```

## Setup

Python 3.11+ and `ffmpeg` / `ffprobe` on `PATH` (screenshots and card audio).

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Put your Poe API key in the environment. Do not commit it or paste it into a source file.

```powershell
copy .env.example .env
# edit .env, then in PowerShell:
$env:POE_API_KEY = (Get-Content .env | Where-Object { $_ -match '^POE_API_KEY=' }) -replace '^POE_API_KEY=',''
```

Or set it for the current session only:

```powershell
$env:POE_API_KEY = "your_key"
```

## Usage

```powershell
python lecture_to_anki.py `
  -i "C:\path\to\Course Folder" `
  -o .\output

# Also summarize context/additional_context/ into card generation
python lecture_to_anki.py -i "…\Course" -o .\output --use-additional-context

# Auto-create missing exam_context.txt (non-interactive)
python lecture_to_anki.py -i "…\Course" -o .\output --auto-context

python lecture_to_anki.py -i "…\Course" -o .\output --only-deck 1 --force
```

A later run skips any lecture that already has a valid `.apkg`. New lecture folders in the same course are generated; finished ones are left alone. `output/lecture_index.json` records each lecture, its deck path, and its card count. Pass `--force` to rebuild a deck that already exists.

Check the key without generating a deck:

```powershell
python test_poe_api.py
```

## Models (token-optimized)

| Role | Default | Flag |
|------|---------|------|
| Cards + lecture analysis | `Claude-Sonnet-4.6` | `--model` |
| Summaries (`additional_context`) | `GPT-5.2-Instant` | `--model-fast` |
| Creating `exam_context.txt` | `Claude-Opus-4.7` | `--model-powerful` |

## OCR frames (per lecture)

No SSIM slide detection. When the model (or a transcript heuristic) flags
on-screen image narration — plans/maps *or* dense visual description / pointing
language (“hier links…”, “you see…”) — a frame is grabbed at that timestamp into
`output/<course>/ocr/<lecture>/` with a lecture-wide `timestamps.txt` listing
every timeframe. Use those PNGs for OCR or Anki **Image Occlusion Enhanced**.
