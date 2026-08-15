#!/usr/bin/env python3
"""
Build a readable PDF of all lecture subtitle transcripts.

Usage:
    python make_course_transcript_pdf.py -i ./lectures -o ./output/course_transcript.pdf
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fpdf import FPDF

# Reuse the project's VTT helpers when available
try:
    from lecture_to_anki import parse_vtt, clean_text, seconds_to_timestamp, time_to_seconds
except ImportError:
    def clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def time_to_seconds(t: str) -> float:
        parts = t.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(parts[0])

    def seconds_to_timestamp(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def parse_vtt(path: str) -> List[Dict]:
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
                entries.append({"start": start_s, "end": end_s, "text": " ".join(text_lines)})
            i += 1
        return entries


def lecture_sort_key(path: Path) -> Tuple:
    match = re.search(r"(\d+)", path.stem)
    return (int(match.group(1)) if match else 10**9, path.stem.lower())


def discover_vtt_files(input_dir: Path) -> List[Path]:
    files = list(input_dir.glob("*.vtt"))
    return sorted(files, key=lecture_sort_key)


def find_system_font() -> Optional[str]:
    """Pick a Unicode TTF available on Windows / common systems."""
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


def entries_to_paragraphs(entries: List[Dict], gap_seconds: float = 8.0) -> List[Tuple[str, str]]:
    """
    Merge consecutive VTT cues into readable paragraphs.
    Returns list of (timestamp, paragraph_text).
    """
    if not entries:
        return []

    paragraphs: List[Tuple[str, str]] = []
    buf: List[str] = []
    para_start = entries[0]["start"]
    prev_end = entries[0]["end"]

    for e in entries:
        text = clean_text(e["text"])
        if not text:
            continue

        # Start a new paragraph after a longer pause
        if buf and (e["start"] - prev_end) > gap_seconds:
            paragraphs.append((seconds_to_timestamp(para_start), " ".join(buf)))
            buf = []
            para_start = e["start"]

        if not buf:
            para_start = e["start"]
        buf.append(text)
        prev_end = e["end"]

    if buf:
        paragraphs.append((seconds_to_timestamp(para_start), " ".join(buf)))
    return paragraphs


class TranscriptPDF(FPDF):
    def __init__(self, course_title: str, font_path: str):
        super().__init__(format="A4")
        self.course_title = course_title
        self.set_auto_page_break(auto=True, margin=18)
        self.add_font("Body", "", font_path)
        # Bold variant if available (Arial Bold)
        bold_path = font_path.replace("arial.ttf", "arialbd.ttf").replace(
            "Arial.ttf", "Arial Bold.ttf"
        )
        if Path(bold_path).exists():
            self.add_font("Body", "B", bold_path)
            self.has_bold = True
        else:
            self.has_bold = False

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Body", "B" if self.has_bold else "", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, self.course_title, align="L")
        self.ln(4)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(6)

    def footer(self):
        self.set_y(-14)
        self.set_font("Body", "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")


def build_pdf(
    vtt_files: List[Path],
    output_path: Path,
    course_title: str = "Course Transcript",
) -> Path:
    font_path = find_system_font()
    if not font_path:
        raise RuntimeError(
            "No Unicode TTF font found. Install Arial or DejaVu Sans, "
            "or edit find_system_font()."
        )

    pdf = TranscriptPDF(course_title, font_path)
    pdf.add_page()

    # Title page
    pdf.set_font("Body", "B" if pdf.has_bold else "", 22)
    pdf.set_text_color(20, 20, 20)
    pdf.ln(40)
    pdf.multi_cell(0, 12, course_title, align="C")
    pdf.ln(8)
    pdf.set_font("Body", "", 12)
    pdf.set_text_color(80, 80, 80)
    pdf.multi_cell(
        0,
        8,
        f"{len(vtt_files)} lectures — full subtitle transcripts for reading and exam prep",
        align="C",
    )
    pdf.ln(16)
    pdf.set_font("Body", "", 11)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(0, 7, "Contents:", align="L")
    pdf.ln(2)
    for i, vtt in enumerate(vtt_files, 1):
        title = vtt.stem.replace("_", " ").replace("-", " ").title()
        pdf.cell(0, 7, f"  {i}. {title}", new_x="LMARGIN", new_y="NEXT")

    # One chapter per lecture
    for lecture_idx, vtt in enumerate(vtt_files, 1):
        title = vtt.stem.replace("_", " ").replace("-", " ").title()
        print(f"  Adding {vtt.name}...")
        entries = parse_vtt(str(vtt))
        paragraphs = entries_to_paragraphs(entries)

        pdf.add_page()
        pdf.set_font("Body", "B" if pdf.has_bold else "", 16)
        pdf.set_text_color(20, 20, 20)
        pdf.multi_cell(0, 10, f"{lecture_idx}. {title}")
        pdf.ln(2)
        pdf.set_font("Body", "", 9)
        pdf.set_text_color(100, 100, 100)
        duration = entries[-1]["end"] if entries else 0
        pdf.cell(
            0,
            6,
            f"{len(entries)} subtitle cues · ~{seconds_to_timestamp(duration)} · {len(paragraphs)} paragraphs",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(4)
        pdf.set_draw_color(180, 180, 180)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(6)

        for ts, para in paragraphs:
            # Timestamp in margin style
            pdf.set_font("Body", "B" if pdf.has_bold else "", 8)
            pdf.set_text_color(130, 130, 130)
            pdf.cell(0, 5, ts, new_x="LMARGIN", new_y="NEXT")
            # Paragraph body
            pdf.set_font("Body", "", 10)
            pdf.set_text_color(25, 25, 25)
            pdf.multi_cell(0, 5.5, para)
            pdf.ln(3)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a PDF of all lecture subtitle transcripts"
    )
    parser.add_argument(
        "--input-dir", "-i",
        required=True,
        help="Directory containing .vtt subtitle files",
    )
    parser.add_argument(
        "--output", "-o",
        default="./output/course_transcript.pdf",
        help="Output PDF path (default: ./output/course_transcript.pdf)",
    )
    parser.add_argument(
        "--title",
        default="Baugeschichte — Course Transcript",
        help="Course title shown on the PDF cover",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"Error: input directory not found: {input_dir}")
        return 1

    vtt_files = discover_vtt_files(input_dir)
    if not vtt_files:
        print(f"Error: no .vtt files found in {input_dir}")
        return 1

    print(f"Found {len(vtt_files)} subtitle file(s)")
    out = build_pdf(vtt_files, Path(args.output), course_title=args.title)
    print(f"[OK] Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
