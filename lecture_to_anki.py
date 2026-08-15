#!/usr/bin/env python3
"""
Lecture to Anki - Batch Video Processing for Flashcard Generation

Processes multiple lecture videos with subtitles and generates Anki decks
using genanki for offline deck creation. Designed for VPS deployment.

Usage:
    python lecture_to_anki.py --input-dir ./lectures --output-dir ./output
    python lecture_to_anki.py --config config.yaml
"""

import os
import re
import subprocess
import argparse
import json
import hashlib
import random
import shutil
import time
import zipfile
import sqlite3
import tempfile
from pathlib import Path

from openai import (
    OpenAI,
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    RateLimitError,
)
from datetime import datetime
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

import genanki

# ========================
# CONFIGURATION
# ========================

CONTEXT_FILENAME = "exam_context.txt"
CONTEXT_DIRNAME = "context"
ADDITIONAL_CONTEXT_DIRNAME = "additional_context"


@dataclass
class LectureConfig:
    """Configuration for a single lecture (one week / video folder)."""
    video_path: str
    vtt_path: str
    deck_name: str
    output_name: str  # Output .apkg filename (without extension)
    lecture_dir: str = ""  # week folder containing video + vtt
    course_dir: str = ""   # parent course folder (holds shared context/)
    course_name: str = ""


@dataclass 
class AppConfig:
    """Global application configuration"""
    poe_api_key: str
    output_dir: str = "./output"
    temp_dir: str = "./temp"
    prompt_file: str = "./prompts/lecture_exam_prompt.txt"
    
    # Card media: audio window around a card timestamp when no next card exists
    card_audio_seconds: float = 45.0

    # OCR frame export: image-narration heuristic on the transcript
    ocr_keyword_window_s: float = 30.0
    ocr_keyword_min_score: int = 6
    ocr_keyword_min_spacing_s: float = 18.0

    # Tiered models (token / cost optimization)
    # - fast: summaries
    # - default (model_name): card generation, lecture analysis
    # - powerful: creating exam_context from scratch when needed
    model_name: str = "Claude-Sonnet-4.6"
    model_fast: str = "GPT-5.2-Instant"
    model_powerful: str = "Claude-Opus-4.7"
    max_tokens: int = 12000
    analysis_max_tokens: int = 3500
    temperature: float = 0.2

    # Whole-lecture generation: transcripts larger than this are split into
    # overlapping chunks (each chunk still receives the full lecture analysis)
    chunk_chars: int = 50000

    # Context options
    use_additional_context: bool = False
    auto_context: bool = False  # non-interactive: build exam_context if missing


POE_MODEL_FALLBACKS = (
    "Claude-Sonnet-4.6",
    "GPT-5.4",
    "Claude-Opus-4.7",
    "GPT-5.2-Instant",
)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
TEXT_CONTEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_CONTEXT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


# ========================
# UTILITIES
# ========================

def seconds_to_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS format"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _parse_time_component(value: str) -> float:
    """Parse one HH/MM/SS fragment; tolerate corrupt VTT like '00.-490'."""
    value = value.strip().replace(",", ".")
    # e.g. "00.-490" / ".-490" (negative fractional garbage) → 0
    if ".-" in value or value.startswith("-"):
        return 0.0
    try:
        return float(value)
    except ValueError:
        cleaned = re.sub(r"[^\d.]", "", value)
        if not cleaned or cleaned == ".":
            return 0.0
        try:
            return float(cleaned)
        except ValueError:
            return 0.0


def time_to_seconds(t: str) -> float:
    """Convert HH:MM:SS.mmm (or MM:SS) to seconds. Tolerates bad subtitle cues."""
    t = t.strip()
    # Drop VTT cue settings after the timestamp ("00:00:01.000 align:start")
    t = t.split()[0] if t else "0"
    parts = t.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return (
                int(_parse_time_component(h)) * 3600
                + int(_parse_time_component(m)) * 60
                + _parse_time_component(s)
            )
        if len(parts) == 2:
            m, s = parts
            return int(_parse_time_component(m)) * 60 + _parse_time_component(s)
        return _parse_time_component(parts[0])
    except (TypeError, ValueError):
        return 0.0


def clean_text(text: str) -> str:
    """Normalize whitespace in text"""
    return re.sub(r"\s+", " ", text).strip()


def find_system_font() -> Optional[str]:
    """Pick a Unicode TTF available on Windows / common systems (for PDF export)."""
    candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def write_analysis_pdf(title: str, analysis: str, output_path: Path) -> Path:
    """Write lecture analysis as a readable PDF (fpdf2)."""
    from fpdf import FPDF

    font_path = find_system_font()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    has_bold = False
    if font_path:
        pdf.add_font("Body", "", font_path)
        bold_path = font_path.replace("arial.ttf", "arialbd.ttf").replace(
            "Arial.ttf", "Arial Bold.ttf"
        )
        if Path(bold_path).exists():
            pdf.add_font("Body", "B", bold_path)
            has_bold = True
        font_family = "Body"
    else:
        font_family = "Helvetica"

    def set_body(size: int = 11, bold: bool = False):
        style = "B" if bold and has_bold else ""
        pdf.set_font(font_family, style, size)

    def write_para(text: str, size: int = 11, bold: bool = False, line_h: float = 6):
        # fpdf2 can leave x past the right margin after some cells
        pdf.set_x(pdf.l_margin)
        set_body(size, bold=bold)
        # Replace characters Helvetica can't render; Arial usually fine
        safe = text.replace("\t", "    ")
        pdf.multi_cell(pdf.epw, line_h, safe)

    write_para(title, size=16, bold=True, line_h=9)
    pdf.ln(2)
    pdf.set_text_color(110, 110, 110)
    write_para("Lecture analysis", size=9, line_h=6)
    pdf.set_text_color(25, 25, 25)
    pdf.ln(2)
    pdf.set_draw_color(180, 180, 180)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(6)

    blocks = re.split(r"\n\s*\n", analysis.strip())
    for block in blocks:
        text = block.strip()
        if not text:
            continue
        first_line, _, rest = text.partition("\n")
        is_heading = (
            first_line.endswith(":")
            or (first_line.isupper() and len(first_line) < 80)
            or bool(re.match(r"^[A-Z][A-Z0-9 /&_-]{2,}$", first_line))
        )
        if is_heading:
            pdf.set_text_color(20, 20, 20)
            write_para(first_line, size=12, bold=True, line_h=7)
            if rest.strip():
                pdf.set_text_color(25, 25, 25)
                write_para(rest.strip(), size=11, line_h=6)
        else:
            pdf.set_text_color(25, 25, 25)
            write_para(text, size=11, line_h=6)
        pdf.ln(3)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))
    return output_path


def generate_deck_id(name: str) -> int:
    """Generate a consistent deck ID from name"""
    hash_val = hashlib.md5(name.encode()).hexdigest()
    return int(hash_val[:8], 16) + 1000000000


def generate_model_id(name: str) -> int:
    """Generate a consistent model ID from name"""
    hash_val = hashlib.md5(f"model_{name}".encode()).hexdigest()
    return int(hash_val[:8], 16) + 2000000000


def lecture_number(output_name: str) -> Optional[int]:
    """Extract a numeric lecture index from names like lecture03."""
    match = re.search(r"(\d+)", output_name)
    return int(match.group(1)) if match else None


# ========================
# VTT PARSING
# ========================

def parse_vtt(path: str) -> List[Dict]:
    """Parse VTT subtitle file"""
    entries = []
    with open(path, "r", encoding="utf8") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            start, end = line.split(" --> ")
            start_s = time_to_seconds(start)
            end_s = time_to_seconds(end)

            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1

            entries.append({
                "start": start_s,
                "end": end_s,
                "text": " ".join(text_lines)
            })
        i += 1

    return entries


def transcript_for_range(vtt_entries: List[Dict], start: float, end: Optional[float]) -> str:
    """Get transcript text for a time range."""
    texts = []
    for e in vtt_entries:
        if e["end"] >= start and e["start"] <= (end or 1e9):
            texts.append(e["text"])
    return clean_text(" ".join(texts))


# ========================
# VIDEO PROCESSING
# ========================

def get_video_duration(video_path: str) -> Optional[float]:
    """Return the video duration in seconds via ffprobe, or None if unknown."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (ValueError, FileNotFoundError):
        pass
    return None


def media_file_valid(path: str) -> bool:
    """Return True when a media file exists and is non-empty."""
    file_path = Path(path)
    return file_path.is_file() and file_path.stat().st_size > 0


def extract_screenshot(video_path: str, timestamp: float, output_path: str) -> bool:
    """Extract a single screenshot from video"""
    result = subprocess.run([
        "ffmpeg", "-ss", str(timestamp),
        "-i", video_path,
        "-frames:v", "1",
        output_path, "-y"
    ], capture_output=True)
    return result.returncode == 0 and media_file_valid(output_path)


def extract_audio(video_path: str, start: float, end: float, output_path: str) -> bool:
    """Extract audio segment from video"""
    result = subprocess.run([
        "ffmpeg", "-ss", str(start),
        "-to", str(end),
        "-i", video_path,
        "-q:a", "0", "-map", "a",
        output_path, "-y"
    ], capture_output=True)
    return result.returncode == 0 and media_file_valid(output_path)


# ========================
# AI CARD GENERATION
# ========================

POE_BASE_URL = "https://api.poe.com/v1"
RETRYABLE_STATUSES = {429, 500, 502, 503, 529}

_poe_client: Optional[OpenAI] = None


def get_poe_client(api_key: str) -> OpenAI:
    """Return a cached OpenAI-compatible client pointed at the Poe API."""
    global _poe_client
    if _poe_client is None:
        # Long timeout: whole-lecture analysis/generation responses are large
        _poe_client = OpenAI(api_key=api_key, base_url=POE_BASE_URL, timeout=600)
    return _poe_client


def _poe_models_to_try(primary_model: str) -> List[str]:
    """Build model list with fallbacks when the configured model is unavailable."""
    models = [primary_model]
    for model in POE_MODEL_FALLBACKS:
        if model not in models:
            models.append(model)
    return models


def _create_response(client: OpenAI, model: str, prompt: str, config: AppConfig,
                     max_tokens: Optional[int] = None):
    """Call the Responses API, dropping optional params the model rejects."""
    kwargs = {
        "model": model,
        "input": prompt,
        "max_output_tokens": max_tokens or config.max_tokens,
        "temperature": config.temperature,
    }
    while True:
        try:
            return client.responses.create(**kwargs)
        except APIStatusError as e:
            message = str(e).lower()
            # Some models (e.g. GPT-5.2-Instant) reject optional params.
            if "temperature" in kwargs and "temperature" in message:
                del kwargs["temperature"]
                continue
            raise


def call_poe_api(prompt: str, config: AppConfig, max_retries: int = 3,
                 max_tokens: Optional[int] = None,
                 model: Optional[str] = None) -> Optional[str]:
    """Call Poe API (OpenAI-compatible Responses API) with retry and model fallback."""
    client = get_poe_client(config.poe_api_key)
    primary = model or config.model_name

    for candidate in _poe_models_to_try(primary):
        for attempt in range(max_retries):
            try:
                response = _create_response(client, candidate, prompt, config, max_tokens)
                text = (response.output_text or "").strip()
                if text:
                    if candidate != primary:
                        print(f"    Using fallback model: {candidate}")
                    return text
                print(f"    API returned empty response ({candidate})")
                break

            except (APIConnectionError, APITimeoutError) as e:
                detail = str(e).replace("\n", " ")[:200]
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt * 2, 30)
                    print(f"    API error ({candidate}, attempt {attempt + 1}/{max_retries}): {detail}")
                    print(f"    Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"    API error ({candidate}) after {max_retries} attempts: {detail}")

            except (RateLimitError, APIStatusError) as e:
                status = getattr(e, "status_code", None)
                detail = str(e).replace("\n", " ")[:200]
                if status in RETRYABLE_STATUSES and attempt < max_retries - 1:
                    wait_time = min(2 ** attempt * 2, 30)
                    print(
                        f"    API error ({candidate}, attempt {attempt + 1}/{max_retries}): "
                        f"HTTP {status} - {detail}"
                    )
                    print(f"    Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                print(f"    API error ({candidate}): HTTP {status} - {detail}")
                break

    print("    Error calling Poe API: all configured and fallback models failed")
    return None


# ========================
# COURSE-LEVEL CONTEXT
# ========================
# One shared context/ folder lives on the COURSE root (not per week):
#   Course/
#     context/exam_context.txt
#     context/additional_context/
#     01 - week/...
#     02 - week/...

# Cache resolved course context for the duration of a batch run
_course_context_cache: Dict[str, Optional[str]] = {}
_course_additional_summary_cache: Dict[str, str] = {}


def course_root_for(lecture: LectureConfig) -> Path:
    """Return the course folder that holds the shared context/ directory."""
    if lecture.course_dir:
        return Path(lecture.course_dir)
    if lecture.lecture_dir:
        return Path(lecture.lecture_dir).parent
    return Path(lecture.video_path).parent


def context_dir(course_dir: Path) -> Path:
    return course_dir / CONTEXT_DIRNAME


def additional_context_dir(course_dir: Path) -> Path:
    return context_dir(course_dir) / ADDITIONAL_CONTEXT_DIRNAME


def exam_context_path(course_dir: Path) -> Path:
    return context_dir(course_dir) / CONTEXT_FILENAME


def ensure_context_folders(course_dir: Path) -> None:
    """Create course-level context/ and context/additional_context/."""
    ctx = context_dir(course_dir)
    add = additional_context_dir(course_dir)
    ctx.mkdir(parents=True, exist_ok=True)
    add.mkdir(parents=True, exist_ok=True)

    readme = ctx / "README.txt"
    if not readme.exists():
        readme.write_text(
            "COURSE-LEVEL context (applies to every week/lecture in this series).\n\n"
            f"Put the exam structure and study goals in {CONTEXT_FILENAME}.\n"
            "Optional supporting PDFs, images, or notes go in additional_context/.\n"
            "If exam_context.txt is missing, the script will ask how to create it.\n"
            "See exam_context.TEMPLATE.txt for a starter outline.\n",
            encoding="utf8",
        )

    template = ctx / "exam_context.TEMPLATE.txt"
    if not template.exists():
        template.write_text(
            "EXAM FORMAT:\n"
            "- (e.g. 25 MCQs + 1 essay from 3 choices)\n"
            "- Languages allowed, open/closed book, duration\n\n"
            "COMPETENCIES TESTED:\n"
            "- …\n\n"
            "HOW TO PREPARE / STUDY AIDS:\n"
            "- …\n\n"
            "CARD GENERATION GUIDANCE:\n"
            "- What kinds of facts belong in Anki (MCQ-style factual recall)\n"
            "- What belongs in essay prep (comparisons, themes across lectures)\n"
            "- Level of dating / detail expected\n\n"
            "WHAT TO IGNORE:\n"
            "- organizational chatter, pure logistics, …\n",
            encoding="utf8",
        )


def extract_text_from_pdf(path: Path, max_chars: int = 30000) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
            if sum(len(p) for p in parts) >= max_chars:
                break
        text = clean_text("\n".join(parts))
        return text[:max_chars]
    except Exception as e:
        return f"[Could not read PDF {path.name}: {e}]"


def load_additional_context_raw(course_dir: Path, max_total_chars: int = 80000) -> str:
    """Load text from files in course context/additional_context/ (PDF/txt/md)."""
    add_dir = additional_context_dir(course_dir)
    if not add_dir.exists():
        return ""

    chunks = []
    used = 0
    files = sorted(
        p for p in add_dir.iterdir()
        if p.is_file() and not p.name.startswith(".")
    )
    for path in files:
        ext = path.suffix.lower()
        if ext in TEXT_CONTEXT_EXTENSIONS:
            try:
                text = path.read_text(encoding="utf8", errors="replace")
            except Exception as e:
                text = f"[Could not read {path.name}: {e}]"
        elif ext in PDF_EXTENSIONS:
            text = extract_text_from_pdf(path)
        elif ext in IMAGE_CONTEXT_EXTENSIONS:
            text = (
                f"[Image file present: {path.name}. "
                f"Describe/label-heavy slides are better handled via OCR frame export.]"
            )
        else:
            continue

        text = clean_text(text)
        if not text:
            continue
        block = f"### FILE: {path.name}\n{text}\n"
        if used + len(block) > max_total_chars:
            remain = max_total_chars - used
            if remain > 200:
                chunks.append(block[:remain] + "\n[...truncated...]")
            break
        chunks.append(block)
        used += len(block)

    return "\n".join(chunks)


def summarize_additional_context(raw_text: str, config: AppConfig,
                                 course_name: str) -> Optional[str]:
    """Compress additional_context materials with a fast model to save tokens."""
    if not raw_text.strip():
        return None
    prompt = (
        "Summarize the following supplementary COURSE materials into a compact exam-prep "
        "brief (bullet points). Keep exam structure, competencies, must-know lists, "
        "preparation advice, and distinctions. Drop fluff. "
        f"Course: {course_name}\n\nMATERIALS:\n{raw_text}"
    )
    print("    Summarizing course additional_context/ with fast model...")
    return call_poe_api(
        prompt, config,
        max_tokens=2500,
        model=config.model_fast,
    )


def generate_exam_context_from_materials(
    materials: str,
    config: AppConfig,
    course_name: str,
    user_notes: str = "",
) -> Optional[str]:
    """Create course-level exam_context.txt using the powerful model."""
    prompt = (
        "Create a concise COURSE-LEVEL EXAM CONTEXT file that will drive flashcard "
        "generation for every lecture in a lecture series. Write clear sections:\n"
        "EXAM FORMAT:\nCOMPETENCIES TESTED:\nHOW TO PREPARE:\n"
        "CARD GENERATION GUIDANCE:\n"
        "  (what belongs in MCQ-style factual Anki cards vs essay-comparison prep)\n"
        "WHAT TO IGNORE:\nSPECIAL RULES:\n\n"
        "Be specific and actionable.\n\n"
        f"COURSE: {course_name}\n"
        f"USER NOTES:\n{user_notes or '(none)'}\n\n"
        f"SOURCE MATERIALS:\n{materials or '(none — invent a sensible generic exam-prep template)'}"
    )
    print("    Creating course exam_context.txt with powerful model...")
    return call_poe_api(
        prompt, config,
        max_tokens=3500,
        model=config.model_powerful,
    )


def resolve_exam_context(lecture: LectureConfig, config: AppConfig) -> Optional[str]:
    """
    Ensure course-level exam_context.txt exists and return its contents.
    Resolved once per course and cached for the batch run.
    """
    course_dir = course_root_for(lecture)
    cache_key = str(course_dir.resolve())
    if cache_key in _course_context_cache:
        return _course_context_cache[cache_key]

    ensure_context_folders(course_dir)
    path = exam_context_path(course_dir)
    course_label = lecture.course_name or course_dir.name

    if path.exists() and path.stat().st_size > 0:
        print(f"  Using course exam context: {path}")
        text = path.read_text(encoding="utf8")
        _course_context_cache[cache_key] = text
        return text

    print(f"\n  [!] Missing course-level {CONTEXT_FILENAME}")
    print(f"      Expected at: {path}")
    print(f"      (One file for the entire lecture series: {course_label})")
    raw_extra = load_additional_context_raw(course_dir)
    has_extra = bool(raw_extra.strip())

    choice = None
    if config.auto_context:
        choice = "2" if has_extra else "3"
        print(f"      --auto-context: choosing option {choice}")
    else:
        print("      How should the course exam context be created?")
        print("        1) Pause — I'll write exam_context.txt manually, then press Enter")
        print("        2) Generate from files in context/additional_context/")
        print("        3) Generate from a short description I type now")
        print("        4) Abort (cannot generate cards without course context)")
        if not has_extra:
            print("      (Note: additional_context/ is empty — option 2 needs files there)")
        try:
            choice = input("      Choice [1/2/3/4]: ").strip() or "1"
        except EOFError:
            choice = "4"

    text: Optional[str] = None

    if choice == "1":
        print("      Add your exam info to the file, then press Enter to continue...")
        try:
            input()
        except EOFError:
            pass
        if path.exists() and path.stat().st_size > 0:
            text = path.read_text(encoding="utf8")
        else:
            print("      Still missing exam_context.txt.")

    elif choice == "2":
        if not has_extra:
            print("      No usable files in additional_context/ — cannot generate.")
        else:
            summary = summarize_additional_context(raw_extra, config, course_label) or raw_extra[:20000]
            text = generate_exam_context_from_materials(summary, config, course_label)
            if text:
                path.write_text(text, encoding="utf8")
                print(f"      Wrote {path}")

    elif choice == "3":
        try:
            user_notes = input("      Short description of exam/goals: ").strip()
        except EOFError:
            user_notes = ""
        materials = ""
        if has_extra:
            materials = summarize_additional_context(raw_extra, config, course_label) or ""
        text = generate_exam_context_from_materials(
            materials, config, course_label, user_notes=user_notes
        )
        if text:
            path.write_text(text, encoding="utf8")
            print(f"      Wrote {path}")

    else:
        print("      Aborting without course context.")

    _course_context_cache[cache_key] = text
    return text


def resolve_additional_context_summary(lecture: LectureConfig, config: AppConfig) -> str:
    """Optionally summarize course additional_context/ once per course."""
    if not config.use_additional_context:
        return ""

    course_dir = course_root_for(lecture)
    cache_key = str(course_dir.resolve())
    if cache_key in _course_additional_summary_cache:
        return _course_additional_summary_cache[cache_key]

    raw_extra = load_additional_context_raw(course_dir)
    if not raw_extra.strip():
        print("  --use-additional-context set, but course additional_context/ is empty")
        _course_additional_summary_cache[cache_key] = ""
        return ""

    course_label = lecture.course_name or course_dir.name
    summary = summarize_additional_context(raw_extra, config, course_label) or ""
    if summary:
        summary_path = context_dir(course_dir) / "additional_context_summary.txt"
        summary_path.write_text(summary, encoding="utf8")
        print(f"  [OK] Course additional context summary: {summary_path}")
    _course_additional_summary_cache[cache_key] = summary
    return summary


# ========================
# WHOLE-LECTURE ANALYSIS & GENERATION
# ========================

@dataclass
class Card:
    """A generated flashcard with an optional lecture timestamp."""
    question: str
    answer: str
    timestamp_s: Optional[float] = None


@dataclass
class OcrCandidate:
    """A lecture moment whose on-screen visual is worth saving for OCR / occlusion."""
    timestamp_s: float
    reason: str
    end_s: Optional[float] = None
    source: str = "model"  # model | keywords


def format_transcript_with_timestamps(vtt_entries: List[Dict]) -> str:
    """Render VTT entries as '[HH:MM:SS] text' lines for the model."""
    lines = []
    for e in vtt_entries:
        text = clean_text(e["text"])
        if text:
            lines.append(f"[{seconds_to_timestamp(e['start'])}] {text}")
    return "\n".join(lines)


def chunk_vtt_entries(vtt_entries: List[Dict], max_chars: int,
                      overlap_entries: int = 12) -> List[List[Dict]]:
    """Split VTT entries into chunks under max_chars with a small overlap,
    so no fact is lost at a chunk boundary."""
    chunks = []
    current: List[Dict] = []
    size = 0

    for entry in vtt_entries:
        entry_len = len(entry["text"]) + 12  # timestamp prefix overhead
        if current and size + entry_len > max_chars:
            chunks.append(current)
            current = current[-overlap_entries:]
            size = sum(len(e["text"]) + 12 for e in current)
        current.append(entry)
        size += entry_len

    if current:
        chunks.append(current)
    return chunks


ANALYSIS_PROMPT = """You are an expert university tutor. Analyze the following complete lecture transcript and produce a concise LECTURE ANALYSIS that will later be used to generate exam-preparation flashcards.

Work for ANY subject: identify what kind of lecture this is and adapt.
If an EXAM CONTEXT was provided above, it is BINDING: KEY CONTENT TO TEST must only list
items that fit that exam's CARD GENERATION GUIDANCE / competencies. Do not pad with
interesting lecture material that the exam context would ignore.

Structure your analysis exactly like this:

SUBJECT: <field of study and course name if identifiable>
LECTURE TOPIC: <main topic / title of this specific lecture>
LECTURE TYPE: <e.g. historical survey, theory, methods, case studies, calculation-heavy, ...>
LANGUAGE: <language of the lecture>

MAIN THEMES:
- <theme 1 with 1-2 sentence description>
- <theme 2 ...>

KEY CONTENT TO TEST (only items that match the EXAM CONTEXT card guidance — names, dates,
buildings, terms, comparisons, etc. the exam would actually reward):
- <item>
- <item>

TYPICAL EXAM QUESTION STYLES (prefer styles from EXAM CONTEXT when given):
- <style 1>
- <style 2>

CONTENT TO IGNORE (include EXAM CONTEXT ignore-list items plus organizational remarks,
greetings, technical issues, off-topic digressions found in this transcript):
- <item>

Write the analysis in the language of the lecture.

TRANSCRIPT:
"""


def analyze_lecture(transcript: str, config: AppConfig,
                    exam_context: str = "") -> Optional[str]:
    """Pass 1: analyze the entire lecture transcript into a reusable analysis prompt."""
    # Safety net for extremely long transcripts: keep start and end
    max_analysis_chars = 200000
    if len(transcript) > max_analysis_chars:
        half = max_analysis_chars // 2
        transcript = (
            transcript[:half]
            + "\n\n[... transcript shortened for analysis ...]\n\n"
            + transcript[-half:]
        )

    if not exam_context.strip():
        raise RuntimeError(
            "exam_context.txt is required before lecture analysis; "
            "refusing to analyze without the primary card-generation criteria."
        )

    prefix = (
        "EXAM CONTEXT is the PRIMARY filter for what counts as exam-relevant.\n"
        "Only material that fits CARD GENERATION GUIDANCE / COMPETENCIES belongs in "
        "KEY CONTENT TO TEST. Interesting but off-exam material goes under CONTENT TO IGNORE.\n\n"
        f"=== EXAM CONTEXT ===\n{exam_context}\n\n"
        + ANALYSIS_PROMPT
    )

    return call_poe_api(
        prefix + transcript,
        config,
        max_tokens=config.analysis_max_tokens,
        model=config.model_name,
    )


def load_supermemo_rules(prompt_file: str = "") -> str:
    """Load SuperMemo formulating-knowledge rules next to the card prompt."""
    candidates = [
        Path(__file__).resolve().parent / "prompts" / "supermemo_formulating_knowledge.txt",
    ]
    if prompt_file:
        candidates.insert(0, Path(prompt_file).resolve().parent / "supermemo_formulating_knowledge.txt")
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf8")
    return (
        "SUPERMEMO: Stick to the minimum information principle — one fact per card, "
        "short answers, no unordered sets, split complex topics, combat interference."
    )


def generate_cards_for_chunk(
    chunk_transcript: str,
    analysis: str,
    base_prompt: str,
    chunk_info: str,
    config: AppConfig,
    exam_context: str = "",
    additional_summary: str = "",
) -> Optional[str]:
    """Pass 2: generate cards for one transcript chunk with full-lecture context."""
    if not exam_context.strip():
        raise RuntimeError(
            "exam_context.txt is required before card generation; "
            "refusing to generate cards without the primary criteria."
        )

    supermemo = load_supermemo_rules(config.prompt_file)
    parts = [base_prompt, ""]
    parts.append(
        "=== EXAM CONTEXT (PRIMARY GATE — decide whether each possible card is allowed) ==="
    )
    parts.append(exam_context)
    parts.append("")
    parts.append(
        "=== SUPERMEMO FORMULATION RULES (binding for HOW every allowed card is written) ==="
    )
    parts.append(supermemo)
    parts.append("")
    if additional_summary.strip():
        parts.append(
            "=== ADDITIONAL CONTEXT SUMMARY (supporting notes only; "
            "never overrides EXAM CONTEXT) ==="
        )
        parts.append(additional_summary)
        parts.append("")
    parts.append(
        "=== LECTURE ANALYSIS (secondary helper — only use items that already fit "
        "EXAM CONTEXT) ==="
    )
    parts.append(analysis)
    parts.append("")
    parts.append(f"=== TRANSCRIPT SECTION ({chunk_info}) ===")
    parts.append(chunk_transcript)
    parts.append("")
    parts.append(
        "Create flashcards for this transcript section now.\n"
        "RULES:\n"
        "1) EXAM CONTEXT is the main criterion: a card is allowed ONLY if it matches "
        "CARD GENERATION GUIDANCE / competencies and is not in WHAT TO IGNORE.\n"
        "2) SUPERMEMO RULES bind formulation: ONE fact per card, minimum information, "
        "short answers, prefer cloze, avoid sets/long enumerations, combat interference "
        "with context cues. SPLIT multi-fact topics into many cards.\n"
        "3) Use the lecture analysis only as a checklist of candidate topics that "
        "already fit the exam context — do NOT create a card merely because it appears "
        "in the analysis.\n"
        "4) Facts must come from this transcript section.\n"
        "5) If little or nothing in this section matches the exam context, output few "
        "or zero cards (that is correct).\n"
        "6) Follow the output format strictly, including any OCR: lines when the "
        "lecturer is describing something on screen (image/slide narration)."
    )
    return call_poe_api(
        "\n".join(parts),
        config,
        model=config.model_name,
    )


def parse_cards(cards_text: str) -> List[Card]:
    """Parse Q/A/T card blocks from the AI response."""
    cards = []
    # Strip OCR / occlusion section before parsing Q/A blocks
    main_text = re.split(
        r"\n(?:OCR|OCCLUSION)(?:_CANDIDATES)?:\s*\n",
        cards_text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    blocks = re.split(r"\n(?=Q:)", "\n" + main_text)

    for block in blocks:
        block = block.strip()
        if not block.startswith("Q:") or "\nA:" not in block:
            continue

        question_part, answer_part = block.split("\nA:", 1)
        question = question_part[2:].strip()

        timestamp_s = None
        ts_match = re.search(r"\nT:\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*$", answer_part)
        if ts_match:
            timestamp_s = time_to_seconds(ts_match.group(1))
            answer_part = answer_part[:ts_match.start()]

        # Drop stray markdown headers/separators the model may add
        answer = "\n".join(
            line for line in answer_part.strip().splitlines()
            if not re.match(r"^\s*(#{1,6}\s|---+\s*$)", line)
        ).strip()

        if question and answer:
            cards.append(Card(question=question, answer=answer, timestamp_s=timestamp_s))

    return cards


def parse_ocr_candidates(text: str) -> List[OcrCandidate]:
    """Parse OCR: / OCCLUSION: lines from model output."""
    candidates = []
    section_match = re.search(
        r"(?:OCR|OCCLUSION)(?:_CANDIDATES)?:\s*\n(.*)\Z",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    body = section_match.group(1) if section_match else text

    for line in body.splitlines():
        line = line.strip().lstrip("-").strip()
        match = re.match(
            r"(?:(?:OCR|OCCLUSION):\s*)?T:\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\|\s*(?:reason:\s*)?(.+)$",
            line,
            re.IGNORECASE,
        )
        if match:
            candidates.append(OcrCandidate(
                timestamp_s=time_to_seconds(match.group(1)),
                reason=match.group(2).strip(),
                source="model",
            ))
    return candidates


# Explicit references to an on-screen visual (strong signal)
_OCR_VISUAL_ANCHORS = (
    "diagram", "diagramm", "figure", "abbildung", "abb.", "chart", "schema",
    "schematic", "map", "karte", "grundriss", "aufriss", "schnitt", "facade",
    "fassade", "elevation", "typology", "typologie", "skizze", "zeichnung",
    "darstellung", "übersicht", "uebersicht", "foto", "photograph", "photo",
    "image", "bild", "folie", "slide", "powerpoint", "screen", "leinwand",
    "illustration", "painting", "gemälde", "gemaelde", "relief", "statue",
    "skulptur", "modell", "model", "rendering", "visualisierung", "grafik",
    "graphik", "infographic", "legende", "beschrift", "label", "labels",
    "this image", "diese abbildung", "diese zeichnung", "this drawing",
    "this plan", "dieser plan", "on the slide", "auf der folie",
    "auf dieser folie", "dieses bild", "this picture", "diese folie",
)

# Lecturer pointing at / walking through what is visible
_OCR_POINTING = (
    "look at", "see here", "you see", "you can see", "as you can see",
    "shown here", "shown on", "depicted", "illustrated", "hier sehen",
    "schauen sie", "sehen sie", "wie sie sehen", "wie sie hier",
    "da sehen", "hier sehen sie", "hier haben wir", "hier haben sie",
    "hier ist", "hier sind", "hier oben", "hier unten", "hier links",
    "hier rechts", "hier in der mitte", "this here", "over here",
    "right here", "down here", "up here", "notice here", "note here",
    "pay attention", "achten sie", "beachten sie", "schauen wir",
    "gucken wir", "sehen wir uns", "take a look", "let's look",
    "if you look", "wenn sie schauen", "wenn sie sehen",
)

# Spatial / part-of-image language (works with anchors or dense deixis)
_OCR_SPATIAL = (
    "on the left", "on the right", "to the left", "to the right",
    "in the middle", "in the center", "at the top", "at the bottom",
    "in the foreground", "in the background", "upper left", "upper right",
    "lower left", "lower right", "links", "rechts", "oben", "unten",
    "mittig", "in der mitte", "im zentrum", "im vordergrund",
    "im hintergrund", "links oben", "rechts oben", "links unten",
    "rechts unten", "daneben", "darüber", "darueber", "darunter",
    "zwischen", "neben", "gegenüber", "gegenueber", "umgeben",
    "above", "below", "beside", "behind", "in front of", "surrounded",
    "next to", "this part", "dieser teil", "dieser bereich", "this area",
    "this detail", "dieses detail", "diese seite", "this side",
    "the left side", "the right side", "die linke", "die rechte",
    "marked", "highlighted", "circled", "arrow", "pfeil", "nummeriert",
    "numbered", "erstens", "zweitens", "drittens", "zum einen",
    "zum anderen", "and here", "und hier", "dann hier", "weiter hier",
    "hier noch", "also here", "again here",
)


def _count_phrase_hits(text_lower: str, phrases: Tuple[str, ...]) -> Tuple[int, List[str]]:
    """Count phrase occurrences; longer phrases checked first to reduce double-count."""
    ordered = sorted(phrases, key=len, reverse=True)
    remaining = text_lower
    hits = 0
    matched: List[str] = []
    for phrase in ordered:
        count = remaining.count(phrase)
        if count:
            hits += count
            matched.append(phrase)
            remaining = remaining.replace(phrase, " ")
    return hits, matched


def _score_image_narration(window_text: str) -> Tuple[int, List[str], Dict[str, int]]:
    """
    Score how strongly a transcript window sounds like on-screen image narration.
    Not limited to maps/plans — any dense visual description + pointing language.
    """
    lower = window_text.lower()
    words = re.findall(r"[a-zA-ZäöüÄÖÜß]{2,}", lower)
    word_count = len(words)

    anchor_hits, anchor_m = _count_phrase_hits(lower, _OCR_VISUAL_ANCHORS)
    point_hits, point_m = _count_phrase_hits(lower, _OCR_POINTING)
    spatial_hits, spatial_m = _count_phrase_hits(lower, _OCR_SPATIAL)

    # Weights: explicit visual > pointing > spatial layout talk
    score = anchor_hits * 3 + point_hits * 2 + spatial_hits * 1

    # Dense descriptive talk while pointing at something visible
    if word_count >= 45 and (point_hits + spatial_hits) >= 3:
        score += 3
    if word_count >= 70 and (anchor_hits + point_hits) >= 1:
        score += 2

    # Listing many short clauses ("hier…, hier…, und hier…") implies walking a slide
    here_like = len(re.findall(
        r"\b(hier|here|links|rechts|oben|unten|this|diese[rs]?|dieser)\b",
        lower,
    ))
    if here_like >= 4 and word_count >= 35:
        score += 3

    matched = (anchor_m + point_m + spatial_m)[:8]
    breakdown = {
        "anchors": anchor_hits,
        "pointing": point_hits,
        "spatial": spatial_hits,
        "words": word_count,
        "deixis": here_like,
    }
    return score, matched, breakdown


def find_diagram_moments_from_transcript(
    vtt_entries: List[Dict],
    window_s: float = 30.0,
    min_score: int = 6,
    min_spacing_s: float = 18.0,
    min_hits: Optional[int] = None,  # legacy alias → treated as min_score if given
) -> List[OcrCandidate]:
    """Flag timeframes where the lecturer is narrating / describing an on-screen image."""
    if min_hits is not None:
        min_score = min_hits
    if not vtt_entries:
        return []

    candidates: List[OcrCandidate] = []
    n = len(vtt_entries)
    i = 0
    while i < n:
        start_t = vtt_entries[i]["start"]
        end_t = start_t + window_s
        j = i
        parts: List[str] = []
        while j < n and vtt_entries[j]["start"] <= end_t:
            parts.append(vtt_entries[j]["text"])
            j += 1

        window_text = " ".join(parts)
        score, matched, br = _score_image_narration(window_text)

        # Require some visual/pointing signal — pure spatial words alone are weak
        has_signal = (br["anchors"] + br["pointing"]) >= 1 or br["deixis"] >= 4
        if score >= min_score and has_signal:
            window_end = vtt_entries[j - 1]["end"] if j > i else end_t
            mid = (start_t + window_end) / 2.0
            reason = (
                f"image narration (score {score}: "
                f"{br['anchors']} visual, {br['pointing']} pointing, "
                f"{br['spatial']} spatial, {br['words']} words)"
            )
            if matched:
                reason += " — " + ", ".join(matched[:6])
            if (
                not candidates
                or mid - candidates[-1].timestamp_s >= min_spacing_s
            ):
                candidates.append(OcrCandidate(
                    timestamp_s=mid,
                    end_s=window_end,
                    reason=reason,
                    source="keywords",
                ))
            i = max(j, i + 1)
        else:
            i += 1

    return candidates


def dedupe_cards(cards: List[Card]) -> List[Card]:
    """Remove duplicate cards produced by chunk overlaps (matched by question)."""
    seen = set()
    unique = []
    for card in cards:
        key = re.sub(r"[^\w]", "", card.question.lower())
        if key and key in seen:
            continue
        seen.add(key)
        unique.append(card)
    return unique


def dedupe_ocr(candidates: List[OcrCandidate],
               window_s: float = 12.0) -> List[OcrCandidate]:
    """Merge OCR candidates that point at nearly the same moment."""
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: c.timestamp_s)
    merged = [ordered[0]]
    for cand in ordered[1:]:
        if abs(cand.timestamp_s - merged[-1].timestamp_s) <= window_s:
            # Prefer model-tagged reasons; otherwise keep the richer reason
            keep_new = (
                (cand.source == "model" and merged[-1].source != "model")
                or (
                    cand.source == merged[-1].source
                    and len(cand.reason) > len(merged[-1].reason)
                )
            )
            if keep_new:
                if merged[-1].end_s and not cand.end_s:
                    cand.end_s = merged[-1].end_s
                merged[-1] = cand
            else:
                if cand.end_s and (
                    merged[-1].end_s is None or cand.end_s > merged[-1].end_s
                ):
                    merged[-1].end_s = cand.end_s
        else:
            merged.append(cand)
    return merged


# ========================
# KEYWORD HIGHLIGHTING
# ========================

def highlight_keywords_in_text(answer: str, full_transcript: str) -> str:
    """Highlight keywords from the answer in the transcript with red color"""
    stop_words = {
        'der', 'die', 'das', 'den', 'dem', 'des', 'ein', 'eine', 'einer', 'eines', 
        'und', 'oder', 'aber', 'auch', 'als', 'an', 'auf', 'aus', 'bei', 'bis',
        'für', 'in', 'mit', 'nach', 'über', 'um', 'unter', 'von', 'vor', 'zu',
        'ist', 'sind', 'war', 'waren', 'wird', 'werden', 'hat', 'haben', 'kann',
        'the', 'a', 'an', 'and', 'or', 'but', 'is', 'are', 'was', 'were', 'be',
        'ca', 'ca.', 'bzw', 'z.b.', 'etc', 'sowie', 'durch', 'wurden',
        'sehr', 'mehr', 'noch', 'schon', 'nur', 'nicht', 'sich', 'so'
    }
    
    answer_clean = re.sub(r'[^\w\s]', ' ', answer.lower())
    keywords = [w for w in answer_clean.split() if len(w) > 2 and w not in stop_words]
    
    # Extract 2-word phrases
    answer_words = answer_clean.split()
    phrases = []
    for i in range(len(answer_words) - 1):
        phrase = ' '.join(answer_words[i:i+2])
        if len(phrase) > 6:
            phrases.append(phrase)
    
    highlighted = full_transcript
    
    # Highlight phrases first
    for phrase in phrases:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        highlighted = pattern.sub(
            lambda m: f'<span style="color: #dc2626; font-weight: bold;">{m.group(0)}</span>',
            highlighted
        )
    
    # Then individual keywords
    for keyword in keywords:
        pattern = re.compile(
            r'(?<!</span>)\b(' + re.escape(keyword) + r')\b(?![^<]*</span>)',
            re.IGNORECASE
        )
        highlighted = pattern.sub(
            r'<span style="color: #dc2626; font-weight: bold;">\1</span>',
            highlighted
        )
    
    return highlighted


# ========================
# GENANKI DECK CREATION
# ========================

def create_anki_model(deck_name: str) -> genanki.Model:
    """Create a custom Anki note model with styling"""
    model_id = generate_model_id(deck_name)
    
    return genanki.Model(
        model_id,
        f'{deck_name} Model',
        fields=[
            {'name': 'Front'},
            {'name': 'Back'},
        ],
        templates=[
            {
                'name': 'Card 1',
                'qfmt': '{{Front}}',
                'afmt': '''{{FrontSide}}<hr id="answer">{{Back}}''',
            },
        ],
        css='''
        .card {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-size: 18px;
            text-align: left;
            color: #1a1a1a;
            background-color: #fafafa;
            padding: 20px;
            line-height: 1.5;
        }
        .slide-info {
            font-size: 12px;
            color: #666;
            margin-bottom: 8px;
        }
        .timestamp {
            font-family: monospace;
            background: #e5e7eb;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 11px;
        }
        .answer {
            margin-bottom: 16px;
        }
        details {
            margin-top: 12px;
            background: #f3f4f6;
            border-radius: 8px;
            padding: 12px;
        }
        summary {
            cursor: pointer;
            font-weight: 600;
            color: #374151;
        }
        .transcript {
            margin-top: 10px;
            font-size: 14px;
            color: #4b5563;
            line-height: 1.6;
        }
        '''
    )


def collect_media_refs_from_deck(deck: genanki.Deck) -> Tuple[set, set]:
    """Collect image and sound filenames referenced by deck notes."""
    img_refs = set()
    sound_refs = set()
    for note in deck.notes:
        for field in note.fields:
            img_refs.update(re.findall(r'<img src="([^"]+)"', field))
            sound_refs.update(re.findall(r"\[sound:([^\]]+)\]", field))
    return img_refs, sound_refs


def validate_apkg_media(apkg_path: str) -> Tuple[bool, str]:
    """Verify all media referenced in notes are present in the .apkg archive."""
    try:
        with zipfile.ZipFile(apkg_path) as archive:
            media_names = set(json.loads(archive.read("media")).values())
            db_data = archive.read("collection.anki2")
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        return False, f"Invalid .apkg archive: {exc}"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".anki2")
    try:
        tmp.write(db_data)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        cur = conn.cursor()
        cur.execute("SELECT flds FROM notes")
        rows = cur.fetchall()
        conn.close()
    finally:
        os.unlink(tmp.name)

    img_refs = set()
    sound_refs = set()
    for (flds,) in rows:
        img_refs.update(re.findall(r'<img src="([^"]+)"', flds))
        sound_refs.update(re.findall(r"\[sound:([^\]]+)\]", flds))

    if not rows:
        return False, "Deck contains no notes"

    missing = (img_refs | sound_refs) - media_names
    if missing:
        sample = ", ".join(sorted(missing)[:5])
        return False, f"Missing {len(missing)} media file(s), e.g. {sample}"

    return True, "OK"


def create_note(
    model: genanki.Model,
    question: str,
    answer: str,
    lecture_name: str,
    timestamp: Optional[str] = None,
    slide_num: Optional[int] = None,
    slide_transcript: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    audio_path: Optional[str] = None
) -> genanki.Note:
    """Create an Anki note with all content"""
    
    media_html = ""
    
    if screenshot_path and media_file_valid(screenshot_path):
        filename = os.path.basename(screenshot_path)
        media_html += f'<br><img src="{filename}" style="max-width: 100%; margin-top: 12px;">'
    
    if audio_path and media_file_valid(audio_path):
        filename = os.path.basename(audio_path)
        media_html += f'<br>[sound:{filename}]'
    
    # Front: lecture name plus timestamp when the card maps to a moment in the lecture
    info_parts = [lecture_name]
    if timestamp:
        info_parts.append(f'<span class="timestamp">{timestamp}</span>')
    front_html = f'''<div class="slide-info">{" ".join(info_parts)}</div>
<div class="question">{question}</div>'''
    
    # Back: answer, media, and collapsible transcript context if available
    transcript_html = ""
    if slide_transcript:
        highlighted = highlight_keywords_in_text(answer, slide_transcript)
        transcript_html = f'''
<details>
<summary>Sprechtext ({timestamp or "Kontext"})</summary>
<div class="transcript">{highlighted}</div>
</details>'''
    
    back_html = f'''<div class="answer"><b>{answer}</b></div>
{media_html}{transcript_html}'''
    
    tags = [lecture_name.lower().replace(" ", "_")]
    if slide_num is not None:
        tags.append(f"folie_{slide_num}")
    
    return genanki.Note(
        model=model,
        fields=[front_html, back_html],
        tags=tags
    )


# ========================
# LECTURE PROCESSOR
# ========================

class LectureProcessor:
    """Processes a single lecture video into an Anki deck"""
    
    def __init__(self, lecture: LectureConfig, config: AppConfig):
        self.lecture = lecture
        self.config = config
        self.lecture_id = hashlib.md5(lecture.video_path.encode()).hexdigest()[:8]

        course_slug = _slug(lecture.course_name) if lecture.course_name else "course"
        self.course_output_dir = Path(config.output_dir) / course_slug
        
        self.temp_base = Path(config.temp_dir) / self.lecture_id
        self.media_staging_dir = self.course_output_dir / f".media_{self.lecture_id}"
        self.staged_media_files: List[str] = []
        # One OCR folder per lecture: frames + timestamps.txt
        self.ocr_dir = self.course_output_dir / "ocr" / self.lecture.output_name
        
    def setup_dirs(self):
        """Create necessary directories"""
        self.media_staging_dir.mkdir(parents=True, exist_ok=True)
        self.course_output_dir.mkdir(parents=True, exist_ok=True)

    def media_path(self, media_idx: int, extension: str) -> str:
        """Return the persistent staged media path for a timestamp clip."""
        filename = f"{self.lecture_id}_t{media_idx}.{extension}"
        return str(self.media_staging_dir / filename)

    def register_staged_media(self, path: Optional[str]) -> Optional[str]:
        """Track a staged media file for packaging."""
        if path and media_file_valid(path):
            if path not in self.staged_media_files:
                self.staged_media_files.append(path)
            return path
        return None
    
    def cleanup(self):
        """Remove temporary files"""
        if self.temp_base.exists():
            shutil.rmtree(self.temp_base)

    def save_analysis(self, analysis: str) -> Path:
        """Persist the per-lecture analysis as PDF (txt fallback if PDF fails)."""
        analysis_dir = self.course_output_dir / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = analysis_dir / f"{self.lecture.output_name}_analysis.pdf"
        try:
            write_analysis_pdf(self.lecture.deck_name, analysis, pdf_path)
            return pdf_path
        except Exception as e:
            print(f"  Warning: analysis PDF failed ({e}); writing .txt instead")
            txt_path = analysis_dir / f"{self.lecture.output_name}_analysis.txt"
            txt_path.write_text(analysis, encoding="utf8")
            return txt_path

    def export_ocr_frames(self, candidates: List[OcrCandidate]) -> Optional[Path]:
        """Write per-lecture OCR frames + timestamps.txt (no SSIM / slide detection)."""
        candidates = dedupe_ocr(candidates)
        if not candidates:
            print("  No OCR frames found for this lecture")
            return None

        if self.ocr_dir.exists():
            shutil.rmtree(self.ocr_dir)
        self.ocr_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            f"# OCR frames for: {self.lecture.deck_name}",
            "# Grab these screenshots for OCR / Image Occlusion Enhanced",
            "# One block per frame (blank line between entries):",
            "# index | start | end | filename | source | reason",
            "",
        ]
        saved = 0
        for i, cand in enumerate(candidates, 1):
            start_ts = seconds_to_timestamp(cand.timestamp_s)
            end_s = cand.end_s if cand.end_s is not None else cand.timestamp_s
            end_ts = seconds_to_timestamp(end_s)
            fname = f"{i:02d}_{start_ts.replace(':', '-')}.png"
            out_path = self.ocr_dir / fname
            ok = extract_screenshot(
                self.lecture.video_path, cand.timestamp_s, str(out_path)
            )
            if ok and media_file_valid(str(out_path)):
                saved += 1
                file_col = fname
            else:
                file_col = "(screenshot failed)"

            # Blank line between timestamp entries for readability
            if i > 1:
                lines.append("")
            lines.append(f"{i:02d} | {start_ts} | {end_ts}")
            lines.append(f"     file:   {file_col}")
            lines.append(f"     source: {cand.source}")
            lines.append(f"     reason: {cand.reason}")

        list_path = self.ocr_dir / "timestamps.txt"
        list_path.write_text("\n".join(lines) + "\n", encoding="utf8")
        print(f"  [OK] OCR pack: {self.ocr_dir} ({saved} frames, see timestamps.txt)")
        return self.ocr_dir

    def get_timestamp_media(
        self,
        timestamp_s: float,
        end_s: float,
        media_idx: int,
        media_cache: Dict[int, Tuple[Optional[str], Optional[str]]],
    ) -> Tuple[Optional[str], Optional[str]]:
        """Extract screenshot + audio around a card timestamp (cached by media_idx)."""
        if media_idx in media_cache:
            return media_cache[media_idx]

        screenshot_path = self.media_path(media_idx, "png")
        if extract_screenshot(self.lecture.video_path, timestamp_s, screenshot_path):
            screenshot_path = self.register_staged_media(screenshot_path)
        else:
            screenshot_path = None

        audio_path = None
        if end_s > timestamp_s + 1.0:
            audio_candidate = self.media_path(media_idx, "mp3")
            if extract_audio(
                self.lecture.video_path, timestamp_s, end_s, audio_candidate
            ):
                audio_path = self.register_staged_media(audio_candidate)

        media_cache[media_idx] = (screenshot_path, audio_path)
        return screenshot_path, audio_path

    def process(self) -> Tuple[str, int]:
        """Process lecture and return (output_path, card_count)"""
        print(f"\n{'='*60}")
        print(f"Processing: {self.lecture.deck_name}")
        print(f"Video: {self.lecture.video_path}")
        print(f"{'='*60}")
        
        self.setup_dirs()
        
        try:
            # Step 1: Parse VTT (no SSIM / full-frame extraction)
            vtt = parse_vtt(self.lecture.vtt_path)
            video_duration = get_video_duration(self.lecture.video_path)
            
            # Step 2: Resolve COURSE-LEVEL exam context (shared across all weeks)
            exam_context = resolve_exam_context(self.lecture, self.config)
            if not exam_context:
                raise RuntimeError(
                    "No course-level exam_context.txt available. Add it under "
                    f"{course_root_for(self.lecture) / 'context'} or re-run with "
                    "--auto-context / interactive options."
                )

            additional_summary = resolve_additional_context_summary(
                self.lecture, self.config
            )

            # Step 3: Create deck and model
            deck = genanki.Deck(
                generate_deck_id(self.lecture.deck_name),
                self.lecture.deck_name
            )
            model = create_anki_model(self.lecture.deck_name)
            
            # Step 4: Analyze the ENTIRE lecture (pass 1)
            full_transcript = format_transcript_with_timestamps(vtt)
            print(f"\n  Analyzing full lecture transcript ({len(full_transcript)} chars)...")
            analysis = analyze_lecture(
                full_transcript, self.config, exam_context=exam_context
            )
            if not analysis:
                raise RuntimeError("Lecture analysis failed; cannot generate cards.")

            analysis_path = self.save_analysis(analysis)
            print(f"  [OK] Lecture analysis saved: {analysis_path}")

            # Step 5: Generate cards with exam context + lecture analysis (pass 2)
            with open(self.config.prompt_file, "r", encoding="utf8") as f:
                base_prompt = f.read()

            chunks = chunk_vtt_entries(vtt, self.config.chunk_chars)
            print(f"  Generating cards from {len(chunks)} transcript chunk(s)...")

            all_cards: List[Card] = []
            all_ocr: List[OcrCandidate] = []
            for chunk_idx, chunk in enumerate(chunks, 1):
                chunk_start = seconds_to_timestamp(chunk[0]["start"])
                chunk_end = seconds_to_timestamp(chunk[-1]["start"])
                chunk_info = f"part {chunk_idx}/{len(chunks)}, {chunk_start}-{chunk_end}"
                print(f"\n  Chunk {chunk_idx}/{len(chunks)} ({chunk_start} - {chunk_end})")

                cards_text = generate_cards_for_chunk(
                    format_transcript_with_timestamps(chunk),
                    analysis, base_prompt, chunk_info, self.config,
                    exam_context=exam_context,
                    additional_summary=additional_summary,
                )
                if not cards_text:
                    print(f"    Warning: generation failed for chunk {chunk_idx}")
                    continue

                chunk_cards = parse_cards(cards_text)
                chunk_ocr = parse_ocr_candidates(cards_text)
                print(f"    Generated {len(chunk_cards)} cards"
                      + (f", {len(chunk_ocr)} OCR candidate(s)" if chunk_ocr else ""))
                all_cards.extend(chunk_cards)
                all_ocr.extend(chunk_ocr)

            all_cards = dedupe_cards(all_cards)
            print(f"\n  Total unique cards: {len(all_cards)}")

            # Keyword heuristic: lecturer talking about diagrams → OCR frames
            keyword_ocr = find_diagram_moments_from_transcript(
                vtt,
                window_s=self.config.ocr_keyword_window_s,
                min_score=self.config.ocr_keyword_min_score,
                min_spacing_s=self.config.ocr_keyword_min_spacing_s,
            )
            if keyword_ocr:
                print(f"  Keyword heuristic found {len(keyword_ocr)} diagram moment(s)")
            all_ocr.extend(keyword_ocr)

            # Step 6: Create notes; media from card timestamps (not SSIM slides)
            successful_cards = 0
            media_cache: Dict[int, Tuple[Optional[str], Optional[str]]] = {}
            # Sort timestamps to build audio windows until the next card
            timed = sorted(
                (c.timestamp_s, i)
                for i, c in enumerate(all_cards)
                if c.timestamp_s is not None
            )
            next_ts_by_card: Dict[int, float] = {}
            for rank, (ts, card_i) in enumerate(timed):
                if rank + 1 < len(timed):
                    next_ts_by_card[card_i] = timed[rank + 1][0]
                elif video_duration:
                    next_ts_by_card[card_i] = min(
                        ts + self.config.card_audio_seconds, video_duration
                    )
                else:
                    next_ts_by_card[card_i] = ts + self.config.card_audio_seconds

            media_idx = 0
            for card_i, card in enumerate(all_cards):
                timestamp = None
                slide_num = None
                slide_transcript = None
                screenshot_path = None
                audio_path = None

                if card.timestamp_s is not None:
                    timestamp = seconds_to_timestamp(card.timestamp_s)
                    end_s = next_ts_by_card.get(
                        card_i, card.timestamp_s + self.config.card_audio_seconds
                    )
                    # Keep clips reasonable when cards are far apart
                    end_s = min(end_s, card.timestamp_s + self.config.card_audio_seconds)
                    if video_duration:
                        end_s = min(end_s, video_duration)
                    media_idx += 1
                    slide_num = media_idx
                    slide_transcript = transcript_for_range(
                        vtt, card.timestamp_s, end_s
                    )
                    screenshot_path, audio_path = self.get_timestamp_media(
                        card.timestamp_s, end_s, media_idx, media_cache
                    )

                note = create_note(
                    model=model,
                    question=card.question,
                    answer=card.answer,
                    lecture_name=self.lecture.deck_name,
                    timestamp=timestamp,
                    slide_num=slide_num,
                    slide_transcript=slide_transcript,
                    screenshot_path=screenshot_path,
                    audio_path=audio_path
                )
                deck.add_note(note)
                successful_cards += 1

            print(f"  Extracted media for {len(media_cache)} timestamp(s)")

            # Step 7: Export per-lecture OCR frames + timestamps.txt
            self.export_ocr_frames(all_ocr)
            
            # Step 10: Save deck
            if successful_cards == 0:
                raise RuntimeError(
                    "No cards were generated for this lecture. "
                    "Check API errors in the log and retry."
                )

            output_dir = self.course_output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{self.lecture.output_name}.apkg"
            temp_output_path = output_path.with_suffix(".apkg.tmp")

            img_refs, sound_refs = collect_media_refs_from_deck(deck)
            referenced_media = img_refs | sound_refs
            staged_basenames = {os.path.basename(path) for path in self.staged_media_files}
            missing_media = referenced_media - staged_basenames
            if missing_media:
                sample = ", ".join(sorted(missing_media)[:5])
                raise RuntimeError(
                    f"Media packaging error: {len(missing_media)} referenced file(s) "
                    f"not staged, e.g. {sample}"
                )

            package = genanki.Package(deck)
            package.media_files = [
                path for path in self.staged_media_files
                if os.path.basename(path) in referenced_media
            ]
            package.write_to_file(str(temp_output_path))

            valid, message = validate_apkg_media(str(temp_output_path))
            if not valid:
                if temp_output_path.exists():
                    temp_output_path.unlink()
                raise RuntimeError(f"Generated .apkg failed validation: {message}")

            if output_path.exists():
                output_path.unlink()
            os.replace(str(temp_output_path), str(output_path))

            shutil.rmtree(self.media_staging_dir, ignore_errors=True)

            print(f"\n  [OK] Created deck: {output_path}")
            print(f"  [OK] Total cards: {successful_cards}")
            print(f"  [OK] Media files: {len(self.staged_media_files)}")
            
            return str(output_path), successful_cards
            
        finally:
            # Cleanup temp files
            self.cleanup()


# ========================
# BATCH PROCESSOR
# ========================

def _find_video_and_vtt(folder: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """Find a video and subtitle file inside a lecture folder."""
    videos = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    vtts = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() == ".vtt"
    ]
    if not videos or not vtts:
        return None, None

    # Prefer common scraper names
    preferred_videos = [p for p in videos if "video" in p.name.lower()]
    preferred_vtts = [
        p for p in vtts
        if "subtitle" in p.name.lower() or "caption" in p.name.lower()
    ]
    video = preferred_videos[0] if preferred_videos else sorted(videos)[0]
    vtt = preferred_vtts[0] if preferred_vtts else sorted(vtts)[0]
    return video, vtt


def _slug(name: str) -> str:
    """Filesystem-friendly slug; keep hyphens, collapse other punctuation to underscores."""
    slug = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE)
    return re.sub(r"_+", "_", slug).strip("_") or "lecture"


def discover_lectures(input_dir: str) -> List[LectureConfig]:
    """
    Discover lectures from either:
      A) Nested course layout (preferred):
           Course/
             context/
               exam_context.txt          ← one file for the whole series
               additional_context/
             01 - date - title/
               video_720p.mp4
               subtitles_en-US.vtt
      B) Flat layout (legacy):
           folder/lecture01.mp4 + lecture01.vtt
           folder/context/exam_context.txt
    """
    lectures: List[LectureConfig] = []
    input_path = Path(input_dir)
    if not input_path.is_dir():
        print(f"Error: input directory not found: {input_path}")
        return lectures

    course_name = input_path.name
    # Skip the shared context/ directory when scanning weeks
    subdirs = sorted(
        [
            p for p in input_path.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
            and p.name.lower() != CONTEXT_DIRNAME
        ],
        key=lambda p: p.name.lower(),
    )

    # Nested mode: week/lecture subfolders containing video+vtt
    nested_hits = 0
    for week_dir in subdirs:
        video, vtt = _find_video_and_vtt(week_dir)
        if not video or not vtt:
            continue
        nested_hits += 1
        # "01 - 2026-02-20 - Title" -> deck/output names
        deck_name = week_dir.name
        output_name = _slug(week_dir.name)
        lectures.append(LectureConfig(
            video_path=str(video),
            vtt_path=str(vtt),
            deck_name=deck_name,
            output_name=output_name,
            lecture_dir=str(week_dir),
            course_dir=str(input_path),
            course_name=course_name,
        ))
        print(f"Found: {week_dir.name}/")
        print(f"       {video.name} + {vtt.name}")

    if nested_hits:
        ensure_context_folders(input_path)
        print(f"Mode: nested course folder ({nested_hits} lecture(s))")
        print(f"Course context: {exam_context_path(input_path)}")
        return lectures

    # Flat legacy mode
    for video_file in sorted(input_path.iterdir()):
        if not video_file.is_file() or video_file.suffix.lower() not in VIDEO_EXTENSIONS:
            continue

        base_name = video_file.stem
        vtt_candidates = [
            input_path / f"{base_name}.vtt",
            input_path / f"{base_name}_subtitles.vtt",
            input_path / f"{base_name}_captions.vtt",
        ]
        vtt_file = next((c for c in vtt_candidates if c.exists()), None)
        if not vtt_file:
            # also accept any subtitles*.vtt next to a single video
            loose = list(input_path.glob("subtitles*.vtt")) + list(input_path.glob("*captions*.vtt"))
            vtt_file = loose[0] if loose else None

        if not vtt_file:
            print(f"Warning: No subtitle file found for {video_file.name}")
            continue

        lectures.append(LectureConfig(
            video_path=str(video_file),
            vtt_path=str(vtt_file),
            deck_name=base_name.replace("_", " ").replace("-", " ").title(),
            output_name=base_name,
            lecture_dir=str(input_path),
            course_dir=str(input_path),
            course_name=course_name,
        ))
        print(f"Found: {video_file.name} + {vtt_file.name}")

    if lectures:
        ensure_context_folders(input_path)
        print(f"Mode: flat lecture folder ({len(lectures)} lecture(s))")
        print(f"Course context: {exam_context_path(input_path)}")
    return lectures


def process_batch(
    lectures: List[LectureConfig],
    config: AppConfig,
    skip_existing: bool = True
) -> Dict:
    """Process multiple lectures and generate decks"""
    results = {
        "started_at": datetime.now().isoformat(),
        "lectures": [],
        "total_cards": 0,
        "successful": 0,
        "failed": 0,
        "skipped": 0
    }
    
    print(f"\n{'#'*60}")
    print(f"# BATCH PROCESSING: {len(lectures)} lectures")
    print(f"{'#'*60}")
    
    for i, lecture in enumerate(lectures, 1):
        print(f"\n[{i}/{len(lectures)}] {lecture.deck_name}")

        course_slug = _slug(lecture.course_name) if lecture.course_name else "course"
        output_path = Path(config.output_dir) / course_slug / f"{lecture.output_name}.apkg"
        if skip_existing and output_path.exists():
            valid, message = validate_apkg_media(str(output_path))
            if valid:
                print(f"  [SKIP] Already exists with valid media: {output_path}")
                results["lectures"].append({
                    "name": lecture.deck_name,
                    "status": "skipped",
                    "output": str(output_path)
                })
                results["skipped"] += 1
                continue
            print(f"  [REPROCESS] Existing deck invalid ({message})")

        try:
            processor = LectureProcessor(lecture, config)
            output_path_str, card_count = processor.process()
            
            results["lectures"].append({
                "name": lecture.deck_name,
                "status": "success",
                "output": output_path_str,
                "cards": card_count
            })
            results["total_cards"] += card_count
            results["successful"] += 1
            
        except Exception as e:
            print(f"  [ERROR] Error processing {lecture.deck_name}: {e}")
            results["lectures"].append({
                "name": lecture.deck_name,
                "status": "failed",
                "error": str(e)
            })
            results["failed"] += 1
    
    results["completed_at"] = datetime.now().isoformat()
    
    # Print summary
    print(f"\n{'='*60}")
    print("BATCH COMPLETE")
    print(f"{'='*60}")
    print(f"Successful: {results['successful']}/{len(lectures)}")
    print(f"Skipped (existing): {results['skipped']}/{len(lectures)}")
    print(f"Total cards generated: {results['total_cards']}")
    print(f"Output directory: {config.output_dir}")
    
    # Save results log
    log_path = Path(config.output_dir) / "processing_log.json"
    with open(log_path, "w", encoding="utf8") as f:
        json.dump(results, f, indent=2)
    print(f"Log saved: {log_path}")
    
    return results


# ========================
# CLI
# ========================

def main():
    parser = argparse.ArgumentParser(
        description="Convert lecture videos to Anki flashcard decks"
    )
    parser.add_argument(
        "--input-dir", "-i",
        required=True,
        help="Course folder with week subfolders (video+vtt+context/), or a flat folder of video/vtt pairs"
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="./output",
        help="Directory for output .apkg files (default: ./output)"
    )
    parser.add_argument(
        "--temp-dir", "-t",
        default="./temp",
        help="Directory for temporary files (default: ./temp)"
    )
    parser.add_argument(
        "--api-key", "-k",
        help="Poe API key (or set POE_API_KEY environment variable)"
    )
    parser.add_argument(
        "--prompt-file", "-p",
        default="./prompts/lecture_exam_prompt.txt",
        help="Path to base prompt file for card generation"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess lectures even when a valid .apkg already exists"
    )
    parser.add_argument(
        "--from-deck",
        type=int,
        metavar="N",
        help="Only process lectures numbered N and above (e.g. 3 for lecture03+)"
    )
    parser.add_argument(
        "--to-deck",
        type=int,
        metavar="N",
        help="Only process lectures numbered N and below (e.g. 3 for lecture01-03)"
    )
    parser.add_argument(
        "--only-deck",
        type=int,
        metavar="N",
        help="Only process the lecture numbered N (e.g. 4 for lecture04)"
    )
    parser.add_argument(
        "--model",
        default="Claude-Sonnet-4.6",
        help="Default Poe model for analysis/cards (default: Claude-Sonnet-4.6)"
    )
    parser.add_argument(
        "--model-fast",
        default="GPT-5.2-Instant",
        help="Cheap/fast model for summaries (default: GPT-5.2-Instant)"
    )
    parser.add_argument(
        "--model-powerful",
        default="Claude-Opus-4.7",
        help="Powerful model for creating exam_context.txt (default: Claude-Opus-4.7)"
    )
    parser.add_argument(
        "--use-additional-context",
        action="store_true",
        help="Summarize context/additional_context/ and include it when generating cards"
    )
    parser.add_argument(
        "--auto-context",
        action="store_true",
        help="If exam_context.txt is missing, auto-create it (from additional_context or a minimal prompt) without interactive menu"
    )
    
    args = parser.parse_args()
    
    # Get API key
    api_key = args.api_key or os.environ.get("POE_API_KEY")
    if not api_key:
        print("Error: Poe API key required. Use --api-key or set POE_API_KEY environment variable.")
        return 1
    
    # Resolve prompt path relative to this script when needed
    prompt_path = Path(args.prompt_file)
    if not prompt_path.is_file():
        alt = Path(__file__).resolve().parent / args.prompt_file
        if alt.is_file():
            prompt_path = alt
    if not prompt_path.is_file():
        print(f"Error: prompt file not found: {args.prompt_file}")
        return 1

    # Create config
    config = AppConfig(
        poe_api_key=api_key,
        output_dir=args.output_dir,
        temp_dir=args.temp_dir,
        prompt_file=str(prompt_path),
        model_name=args.model,
        model_fast=args.model_fast,
        model_powerful=args.model_powerful,
        use_additional_context=args.use_additional_context,
        auto_context=args.auto_context,
    )
    
    # Discover lectures
    print(f"Scanning {args.input_dir} for lectures...")
    lectures = discover_lectures(args.input_dir)
    
    if not lectures:
        print("No lecture videos with matching subtitle files found.")
        return 1

    if args.only_deck is not None:
        lectures = [
            lecture for lecture in lectures
            if lecture_number(lecture.output_name) == args.only_deck
        ]
        print(f"Filtered to {len(lectures)} lecture(s) matching deck {args.only_deck}")
        if not lectures:
            print("No lectures matched the --only-deck filter.")
            return 1
    elif args.from_deck is not None or args.to_deck is not None:
        low = args.from_deck if args.from_deck is not None else 0
        high = args.to_deck if args.to_deck is not None else 10**9
        lectures = [
            lecture for lecture in lectures
            if (number := lecture_number(lecture.output_name)) is not None
            and low <= number <= high
        ]
        print(f"Filtered to {len(lectures)} lecture(s) in deck range {low}-{high}")
        if not lectures:
            print("No lectures matched the deck range filter.")
            return 1
    
    # Process all lectures
    results = process_batch(lectures, config, skip_existing=not args.force)
    
    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    exit(main())

