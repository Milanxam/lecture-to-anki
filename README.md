# Lecture to Anki

Turn lecture **videos + VTT subtitles** into high-quality Anki `.apkg` decks, driven by a
**single course-level exam context** file and SuperMemo-style card formulation.

## Install

```powershell
git clone https://github.com/milanxam/lecture-to-anki.git
cd lecture-to-anki

python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Also install **ffmpeg** (and `ffprobe`) on your PATH — used for screenshots and short audio clips.

Get a Poe API key at https://poe.com/api_key and set it:

```powershell
$env:POE_API_KEY = "your_key"
```

## Course folder structure (input)

Point `-i` at the **course folder**. Exam info lives once under `context/`; each week needs video + subtitles:

```text
My Course/
├── context/
│   ├── exam_context.txt          ← PRIMARY filter for what cards to create
│   ├── exam_context.TEMPLATE.txt
│   └── additional_context/       ← optional PDFs / notes
├── 01 - 2026-02-20 - Week title/
│   ├── video_720p.mp4
│   └── subtitles_en-US.vtt
└── 02 - ...
```

Flat layout also works (`lecture01.mp4` + `lecture01.vtt` in one folder).

`exam_context.txt` decides **whether** a fact becomes a card. Card wording follows
SuperMemo’s twenty rules (`prompts/supermemo_formulating_knowledge.txt`): one fact per card,
short answers, prefer cloze, avoid sets.

## Usage

```powershell
# Full course
python lecture_to_anki.py -i "C:\path\to\Course" -o .\output

# Auto-create missing exam_context.txt
python lecture_to_anki.py -i "…\Course" -o .\output --auto-context

# Include summaries of additional_context/
python lecture_to_anki.py -i "…\Course" -o .\output --use-additional-context

# One week only / re-run
python lecture_to_anki.py -i "…\Course" -o .\output --only-deck 3 --force
python lecture_to_anki.py -i "…\Course" -o .\output --from-deck 2
```

Import `.apkg` files from `output/<course>/` into Anki (**File → Import**).

## Output

```text
output/<course>/
├── 01_….apkg
├── analysis/          # per-lecture analysis PDF
└── ocr/<lecture>/     # diagram frames + timestamps.txt
```

## Models (defaults)

| Role | Model | Flag |
|------|--------|------|
| Analysis + cards | Claude-Sonnet-4.6 | `--model` |
| Summaries | GPT-5.2-Instant | `--model-fast` |
| Creating exam_context | Claude-Opus-4.7 | `--model-powerful` |

## Related tools

- [lecture-scraper](https://github.com/milanxam/lecture-scraper) — download ETH lecture videos + VTT
- [anki-from-text](https://github.com/milanxam/anki-from-text) — decks from plain text / PDF
