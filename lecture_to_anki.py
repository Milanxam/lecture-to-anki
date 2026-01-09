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
import cv2
import subprocess
import requests
import argparse
import json
import hashlib
import random
import shutil
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict
from skimage.metrics import structural_similarity as ssim

import genanki

# ========================
# CONFIGURATION
# ========================

@dataclass
class LectureConfig:
    """Configuration for a single lecture"""
    video_path: str
    vtt_path: str
    deck_name: str
    output_name: str  # Output .apkg filename (without extension)


@dataclass 
class AppConfig:
    """Global application configuration"""
    poe_api_key: str
    output_dir: str = "./output"
    temp_dir: str = "./temp"
    prompt_file: str = "./prompts/baugeschichte_exam_prompt.txt"
    
    # Processing parameters
    ssim_threshold: float = 0.85
    stable_frames: int = 3
    low_text_threshold: int = 120
    
    # API settings
    model_name: str = "Claude-3.5-Sonnet"
    max_tokens: int = 1000
    temperature: float = 0.2


# ========================
# UTILITIES
# ========================

def seconds_to_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS format"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def time_to_seconds(t: str) -> float:
    """Convert HH:MM:SS.mmm to seconds"""
    parts = t.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(parts[0])


def clean_text(text: str) -> str:
    """Normalize whitespace in text"""
    return re.sub(r"\s+", " ", text).strip()


def generate_deck_id(name: str) -> int:
    """Generate a consistent deck ID from name"""
    hash_val = hashlib.md5(name.encode()).hexdigest()
    return int(hash_val[:8], 16) + 1000000000


def generate_model_id(name: str) -> int:
    """Generate a consistent model ID from name"""
    hash_val = hashlib.md5(f"model_{name}".encode()).hexdigest()
    return int(hash_val[:8], 16) + 2000000000


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


def transcript_for_slide(vtt_entries: List[Dict], start: float, end: Optional[float]) -> str:
    """Get transcript text for a slide time range"""
    texts = []
    for e in vtt_entries:
        if e["end"] >= start and e["start"] <= (end or 1e9):
            texts.append(e["text"])
    return clean_text(" ".join(texts))


# ========================
# VIDEO PROCESSING
# ========================

def extract_frames(video_path: str, frame_dir: str) -> None:
    """Extract frames from video at 1 fps"""
    print(f"  Extracting frames from {video_path}...")
    os.makedirs(frame_dir, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vf", "fps=1",
        f"{frame_dir}/frame_%06d.jpg",
        "-y"
    ], check=True, capture_output=True)


def load_gray(path: str):
    """Load image as grayscale"""
    img = cv2.imread(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def detect_slide_changes(frame_dir: str, config: AppConfig) -> List[Tuple[int, Optional[int]]]:
    """Detect slide changes using SSIM"""
    frames = sorted(os.listdir(frame_dir))
    if not frames:
        return []
        
    prev = load_gray(os.path.join(frame_dir, frames[0]))
    slide_starts = [0]

    for i in range(1, len(frames)):
        curr = load_gray(os.path.join(frame_dir, frames[i]))
        score = ssim(prev, curr)

        if score < config.ssim_threshold:
            stable = True
            base = curr
            for j in range(1, config.stable_frames):
                if i + j >= len(frames):
                    break
                test = load_gray(os.path.join(frame_dir, frames[i + j]))
                if ssim(base, test) < 0.95:
                    stable = False
                    break

            if stable:
                slide_starts.append(i)
                prev = curr

    slides = []
    for i, start in enumerate(slide_starts):
        end = slide_starts[i + 1] if i + 1 < len(slide_starts) else None
        slides.append((start, end))

    print(f"  Detected {len(slides)} slides")
    return slides


def extract_screenshot(video_path: str, timestamp: float, output_path: str) -> bool:
    """Extract a single screenshot from video"""
    result = subprocess.run([
        "ffmpeg", "-ss", str(timestamp),
        "-i", video_path,
        "-frames:v", "1",
        output_path, "-y"
    ], capture_output=True)
    return result.returncode == 0


def extract_audio(video_path: str, start: float, end: float, output_path: str) -> bool:
    """Extract audio segment from video"""
    result = subprocess.run([
        "ffmpeg", "-ss", str(start),
        "-to", str(end),
        "-i", video_path,
        "-q:a", "0", "-map", "a",
        output_path, "-y"
    ], capture_output=True)
    return result.returncode == 0


# ========================
# AI CARD GENERATION
# ========================

def call_poe_api(prompt: str, config: AppConfig, max_retries: int = 3) -> Optional[str]:
    """Call Poe API for card generation with retry logic"""
    import time
    
    headers = {
        "Authorization": f"Bearer {config.poe_api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": config.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": config.max_tokens,
        "temperature": config.temperature
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://api.poe.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=90
            )
            response.raise_for_status()
            result = response.json()
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 5  # 5, 10, 15 seconds
                print(f"    API error (attempt {attempt + 1}/{max_retries}): {e}")
                print(f"    Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"    Error calling Poe API after {max_retries} attempts: {e}")
                return None


def generate_cards_from_text(text: str, is_visual_slide: bool, config: AppConfig) -> Optional[str]:
    """Generate flashcards from transcript text"""
    with open(config.prompt_file, "r", encoding="utf8") as f:
        base_prompt = f.read()

    if is_visual_slide:
        base_prompt += (
            "\n\nNote: This slide is primarily an image (e.g. plan, facade, building photo). "
            "Base the cards on the spoken explanation of what is shown and how it is interpreted."
        )

    full_prompt = f"{base_prompt}\n\nText to process:\n{text}"
    return call_poe_api(full_prompt, config)


def parse_cards(cards_text: str) -> List[Tuple[str, str]]:
    """Parse Q&A cards from AI response"""
    cards = []
    blocks = cards_text.split("\n\n")
    
    for block in blocks:
        block = block.strip()
        if not block or not block.startswith("Q:"):
            continue
            
        if "\nA:" not in block:
            continue
            
        try:
            question, answer = block.split("\nA:", 1)
            question = question.replace("Q:", "").strip()
            answer = answer.strip()
            
            if question and answer:
                cards.append((question, answer))
        except Exception:
            continue
    
    return cards


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


class MediaNote(genanki.Note):
    """Custom Note class that tracks media files"""
    def __init__(self, model, fields, tags=None, media_files=None):
        super().__init__(model=model, fields=fields, tags=tags)
        self.media_files = media_files or []


def create_note(
    model: genanki.Model,
    question: str,
    answer: str,
    slide_num: int,
    timestamp: str,
    full_transcript: str,
    lecture_name: str,
    lecture_id: str,
    screenshot_path: Optional[str] = None,
    audio_path: Optional[str] = None
) -> Tuple[MediaNote, List[str]]:
    """Create an Anki note with all content"""
    
    media_files = []
    media_html = ""
    
    # Use lecture_id prefix to ensure unique filenames across lectures
    # Add screenshot reference
    if screenshot_path and os.path.exists(screenshot_path):
        filename = f"{lecture_id}_slide_{slide_num}.png"
        media_files.append((screenshot_path, filename))
        media_html += f'<br><img src="{filename}" style="max-width: 100%; margin-top: 12px;">'
    
    # Add audio reference
    if audio_path and os.path.exists(audio_path):
        filename = f"{lecture_id}_slide_{slide_num}.mp3"
        media_files.append((audio_path, filename))
        media_html += f'<br>[sound:{filename}]'
    
    # Highlight keywords
    highlighted_transcript = highlight_keywords_in_text(answer, full_transcript)
    
    # Build front with slide number and timestamp
    front_html = f'''<div class="slide-info">[Folie {slide_num}] <span class="timestamp">{timestamp}</span></div>
<div class="question">{question}</div>'''
    
    # Build back with answer, media, and transcript
    back_html = f'''<div class="answer"><b>{answer}</b></div>
{media_html}
<details>
<summary>Sprechtext (Folie {slide_num})</summary>
<div class="transcript">{highlighted_transcript}</div>
</details>'''
    
    tags = [
        lecture_name.lower().replace(" ", "_"),
        f"folie_{slide_num}"
    ]
    
    note = MediaNote(
        model=model,
        fields=[front_html, back_html],
        tags=tags,
        media_files=media_files
    )
    
    return note, media_files


# ========================
# LECTURE PROCESSOR
# ========================

class LectureProcessor:
    """Processes a single lecture video into an Anki deck"""
    
    def __init__(self, lecture: LectureConfig, config: AppConfig):
        self.lecture = lecture
        self.config = config
        self.lecture_id = hashlib.md5(lecture.video_path.encode()).hexdigest()[:8]
        
        # Create temp directories for this lecture
        self.temp_base = Path(config.temp_dir) / self.lecture_id
        self.frame_dir = self.temp_base / "frames"
        self.screenshot_dir = self.temp_base / "screenshots"
        self.audio_dir = self.temp_base / "audio"
        
    def setup_dirs(self):
        """Create necessary directories"""
        for d in [self.frame_dir, self.screenshot_dir, self.audio_dir]:
            d.mkdir(parents=True, exist_ok=True)
    
    def cleanup(self):
        """Remove temporary files"""
        if self.temp_base.exists():
            shutil.rmtree(self.temp_base)
    
    def process(self) -> Tuple[str, int]:
        """Process lecture and return (output_path, card_count)"""
        print(f"\n{'='*60}")
        print(f"Processing: {self.lecture.deck_name}")
        print(f"Video: {self.lecture.video_path}")
        print(f"{'='*60}")
        
        self.setup_dirs()
        
        try:
            # Step 1: Extract frames
            extract_frames(self.lecture.video_path, str(self.frame_dir))
            
            # Step 2: Detect slides
            slides = detect_slide_changes(str(self.frame_dir), self.config)
            
            # Step 3: Parse VTT
            vtt = parse_vtt(self.lecture.vtt_path)
            
            # Step 4: Create deck and model
            deck = genanki.Deck(
                generate_deck_id(self.lecture.deck_name),
                self.lecture.deck_name
            )
            model = create_anki_model(self.lecture.deck_name)
            
            all_media_files = []
            successful_cards = 0
            
            # Step 5: Process each slide
            i = 0
            slide_num = 0
            while i < len(slides):
                start, end = slides[i]
                text = transcript_for_slide(vtt, start, end)
                
                # Merge slides with insufficient content
                while len(text) < self.config.low_text_threshold and i + 1 < len(slides):
                    next_start, next_end = slides[i + 1]
                    text += " " + transcript_for_slide(vtt, next_start, next_end)
                    end = next_end
                    i += 1
                
                text = clean_text(text)
                if not text:
                    i += 1
                    continue
                    
                slide_num += 1
                is_visual_slide = len(text) < 300
                timestamp = seconds_to_timestamp(start)
                
                print(f"\n  Slide {slide_num} @ {timestamp} ({len(text)} chars)")
                
                # Extract media
                screenshot_path = str(self.screenshot_dir / f"slide_{slide_num}.png")
                audio_path = str(self.audio_dir / f"slide_{slide_num}.mp3")
                
                extract_screenshot(self.lecture.video_path, start + 2, screenshot_path)
                if end:
                    extract_audio(self.lecture.video_path, start, end, audio_path)
                else:
                    audio_path = None
                
                # Generate cards
                cards_text = generate_cards_from_text(text, is_visual_slide, self.config)
                
                if not cards_text or "Error" in cards_text:
                    print(f"    Skipping slide due to generation error")
                    i += 1
                    continue
                
                cards = parse_cards(cards_text)
                print(f"    Generated {len(cards)} cards")
                
                # Create notes
                for question, answer in cards:
                    note, media = create_note(
                        model=model,
                        question=question,
                        answer=answer,
                        slide_num=slide_num,
                        timestamp=timestamp,
                        full_transcript=text,
                        lecture_name=self.lecture.deck_name,
                        lecture_id=self.lecture_id,
                        screenshot_path=screenshot_path,
                        audio_path=audio_path
                    )
                    deck.add_note(note)
                    all_media_files.extend(media)
                    successful_cards += 1
                
                i += 1
            
            # Step 6: Save deck
            output_dir = Path(self.config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{self.lecture.output_name}.apkg"
            
            # Copy media files to a staging area and prepare for packaging
            # Media files are tuples of (source_path, target_filename)
            media_staging_dir = output_dir / f".media_{self.lecture_id}"
            media_staging_dir.mkdir(parents=True, exist_ok=True)
            
            staged_media = []
            seen_files = set()
            for source_path, target_filename in all_media_files:
                if target_filename in seen_files:
                    continue
                seen_files.add(target_filename)
                
                if os.path.exists(source_path):
                    staged_path = media_staging_dir / target_filename
                    shutil.copy2(source_path, staged_path)
                    staged_media.append(str(staged_path))
            
            # Create package with staged media
            package = genanki.Package(deck)
            package.media_files = staged_media
            package.write_to_file(str(output_path))
            
            # Clean up staging directory
            shutil.rmtree(media_staging_dir)
            
            print(f"\n  [OK] Created deck: {output_path}")
            print(f"  [OK] Total cards: {successful_cards}")
            
            return str(output_path), successful_cards
            
        finally:
            # Cleanup temp files
            self.cleanup()


# ========================
# BATCH PROCESSOR
# ========================

def discover_lectures(input_dir: str) -> List[LectureConfig]:
    """
    Discover lecture videos and their subtitle files.
    Expects pairs of: video.mp4 + video.vtt (or video_subtitles.vtt)
    """
    lectures = []
    input_path = Path(input_dir)
    
    video_extensions = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}
    
    for video_file in input_path.iterdir():
        if video_file.suffix.lower() not in video_extensions:
            continue
        
        base_name = video_file.stem
        
        # Look for matching VTT file
        vtt_candidates = [
            input_path / f"{base_name}.vtt",
            input_path / f"{base_name}_subtitles.vtt",
            input_path / f"{base_name}_captions.vtt",
        ]
        
        vtt_file = None
        for candidate in vtt_candidates:
            if candidate.exists():
                vtt_file = candidate
                break
        
        if vtt_file:
            # Create deck name from filename
            deck_name = base_name.replace("_", " ").replace("-", " ").title()
            
            lectures.append(LectureConfig(
                video_path=str(video_file),
                vtt_path=str(vtt_file),
                deck_name=deck_name,
                output_name=base_name
            ))
            print(f"Found: {video_file.name} + {vtt_file.name}")
        else:
            print(f"Warning: No subtitle file found for {video_file.name}")
    
    return lectures


def process_batch(lectures: List[LectureConfig], config: AppConfig) -> Dict:
    """Process multiple lectures and generate decks"""
    results = {
        "started_at": datetime.now().isoformat(),
        "lectures": [],
        "total_cards": 0,
        "successful": 0,
        "failed": 0
    }
    
    print(f"\n{'#'*60}")
    print(f"# BATCH PROCESSING: {len(lectures)} lectures")
    print(f"{'#'*60}")
    
    for i, lecture in enumerate(lectures, 1):
        print(f"\n[{i}/{len(lectures)}] {lecture.deck_name}")
        
        try:
            processor = LectureProcessor(lecture, config)
            output_path, card_count = processor.process()
            
            results["lectures"].append({
                "name": lecture.deck_name,
                "status": "success",
                "output": output_path,
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
        help="Directory containing lecture videos and VTT files"
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
        default="./prompts/baugeschichte_exam_prompt.txt",
        help="Path to prompt file for card generation"
    )
    parser.add_argument(
        "--ssim-threshold",
        type=float,
        default=0.85,
        help="SSIM threshold for slide detection (default: 0.85)"
    )
    
    args = parser.parse_args()
    
    # Get API key
    api_key = args.api_key or os.environ.get("POE_API_KEY")
    if not api_key:
        print("Error: Poe API key required. Use --api-key or set POE_API_KEY environment variable.")
        return 1
    
    # Create config
    config = AppConfig(
        poe_api_key=api_key,
        output_dir=args.output_dir,
        temp_dir=args.temp_dir,
        prompt_file=args.prompt_file,
        ssim_threshold=args.ssim_threshold
    )
    
    # Discover lectures
    print(f"Scanning {args.input_dir} for lectures...")
    lectures = discover_lectures(args.input_dir)
    
    if not lectures:
        print("No lecture videos with matching subtitle files found.")
        return 1
    
    # Process all lectures
    results = process_batch(lectures, config)
    
    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    exit(main())

