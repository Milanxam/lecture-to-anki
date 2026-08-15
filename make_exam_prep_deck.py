#!/usr/bin/env python3
"""
Build a summary / exam-preparation Anki deck for Städtebau I.

Sources:
  1. Exam expectations txt + "Tools and thematic clusters" PDF
     (C:/Users/mIlan/Desktop/Informatio)
  2. Q/A knowledge already generated in output/Städtebau_I/*.apkg

The deck explicitly targets the exam structure: glossary/process ordering,
case-study short questions, comparisons, and tool-transfer analyses with
model answers and drawing checklists.

Usage:
    python make_exam_prep_deck.py
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

import genanki

from lecture_to_anki import (
    AppConfig,
    call_poe_api,
    create_anki_model,
    create_note,
    extract_text_from_pdf,
    generate_deck_id,
    load_supermemo_rules,
    parse_cards,
    validate_apkg_media,
)

INFO_DIR = Path(r"C:\Users\mIlan\Desktop\Informatio")
COURSE_OUTPUT = Path("output") / "Städtebau_I"
DECK_NAME = "Städtebau I - Exam Prep Summary"
OUTPUT_APKG = COURSE_OUTPUT / "00_-_Exam_Prep_Summary.apkg"


def strip_html(text: str) -> str:
    """Reduce a note field to plain text."""
    text = re.sub(r"<details.*?</details>", " ", text, flags=re.DOTALL)
    text = re.sub(r"\[sound:[^\]]+\]", " ", text)
    text = re.sub(r"<img[^>]*>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"\s+", " ", text).strip()


def extract_qa_from_apkg(apkg_path: Path):
    """Yield (question, answer) plain-text pairs from an .apkg file."""
    with zipfile.ZipFile(apkg_path) as archive:
        db_data = archive.read("collection.anki2")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".anki2")
    try:
        tmp.write(db_data)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        try:
            cursor = conn.execute("SELECT flds FROM notes")
            for (flds,) in cursor.fetchall():
                fields = flds.split("\x1f")
                if len(fields) < 2:
                    continue
                question = strip_html(fields[0])
                # Drop the lecture-name/timestamp meta suffix commonly on the front
                question = re.sub(
                    r"(Lecture|Folie|Slide)\b.*$", "", question
                ).strip(" -|")
                answer = strip_html(fields[1])
                if question and answer:
                    yield question, answer
        finally:
            conn.close()
    finally:
        os.unlink(tmp.name)


def build_knowledge_digest(max_chars: int = 70000) -> str:
    """Condense all existing Städtebau I cards into a Q/A digest."""
    parts = []
    used = 0
    apkgs = sorted(p for p in COURSE_OUTPUT.glob("*.apkg") if p != OUTPUT_APKG)
    for apkg in apkgs:
        lecture = apkg.stem.replace("_", " ")
        header = f"\n### {lecture}\n"
        block_lines = []
        try:
            for q, a in extract_qa_from_apkg(apkg):
                if len(a) > 300:
                    a = a[:300] + "…"
                block_lines.append(f"Q: {q}\nA: {a}")
        except Exception as e:
            print(f"  Warning: could not read {apkg.name}: {e}")
            continue
        block = header + "\n".join(block_lines)
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining > 500:
                parts.append(block[:remaining])
                used = max_chars
            break
        parts.append(block)
        used += len(block)
    print(f"  Knowledge digest: {used} chars from {len(apkgs)} decks")
    return "".join(parts)


COMMON_RULES = """
Output format (STRICT, one blank line between cards):

Q: <question>
A: <answer>

Formulation (SUPERMEMO — binding):
- ONE fact per card. Short question; answer preferably a few words to one short line.
- Prefer cloze deletions for names/years/fill-ins.
- Never pack city + cluster + ESG + why + how into one answer — SPLIT into separate cards.
- Avoid unordered "list all …" sets. For a city's three tools, either three cards
  (Environment / Social / Governance tool of <city>?) or one ordered enumeration of ≤3.
- For a city's challenges, prefer one challenge per card or ≤3 ordered items.
- Combat interference: put city / cluster / ESG in the question when tools are similar.
- Do NOT invent tools, clusters, or city facts absent from the material.
- English is fine; keep official tool and cluster names exact.
- No commentary outside Q/A blocks.

Practice / exam-rehearsal cards (Comparisons & Analyses only):
- Still prefer atomic scaffolds over one giant essay answer.
- Split a practice item into several cards, e.g.:
  (1) which second city to pick, (2) one shared challenge, (3) one difference,
  (4) which tool to transfer, (5) which cluster to name, (6) one drawing must-show.
- At most one short "skeleton outline" card (≤5 ordered steps) per practice scenario.
"""


SECTION_PROMPTS = {
    "A_framework": """You create Anki cards for the Städtebau I exam (ETH). Below you get
(1) EXAM EXPECTATIONS from a student who took the parallel exam, (2) the official
tools/clusters overview, (3) a digest of lecture-derived Q/A knowledge.

Create 25-40 ATOMIC cards for PART 1: glossary, framework and process understanding.
Cover explicitly (split aggressively — SuperMemo minimum information):
- Tool-development process: prefer overlapping cloze / "what comes after X?" cards
  for the order: choose city → analyse SDGs and thematic clusters → ESG → choose a tool
  → adapt the tool to the city. Optionally ONE ordered 5-step enumeration card.
- Separate cards: What does ESG stand for? What does SDG stand for? How do they differ?
  How does the toolbox relate to SDG 11?
- One card per ESG dimension (Environment / Social / Governance) meaning.
- One card: each city has how many tools / one per ESG dimension?
- For each of the 9 thematic clusters: ONE card (name → one-line meaning) and optionally
  ONE card (cluster → example tool+city). Never dump all 9 clusters into one answer.
- Key glossary terms from the digest as cloze or definition cards (one term each).
""",
    "B_mapping": """You create Anki cards for the Städtebau I exam (ETH). Below you get
the official tools/clusters overview and a digest of lecture knowledge.

Create 50-80 ATOMIC mapping cards for PART 2 (case studies / toolbox). SuperMemo split:
- For EACH of the 18 tools, create SEPARATE cards (do not combine into one):
  (a) tool → city
  (b) tool → thematic cluster
  (c) tool → ESG dimension
  (d) optional: tool → main challenge/opportunity it responds to (one line)
  (e) optional: tool → what it does (one short line)
- For EACH of the 6 cities:
  - three cards: Environment / Social / Governance tool of <city>?
  - challenges: one challenge per card (or ≤3 ordered), never a long unordered set
- Use context cues in questions when names could interfere.
""",
    "C_casestudy": """You create Anki cards for the Städtebau I exam (ETH). Below you get
the official tools/clusters overview, the exam expectations, and a digest of lecture knowledge.

Create 20-30 case-study short-question cards for PART 2 in the exam's own style
(multiple-choice and true-or-false), still SuperMemo-atomic:
- True/false: answer is "True" or "False — <≤10 word correction>" (not a paragraph).
- MCQ: options A/B/C/D in the question; answer = letter only, or letter + ≤8 word reason.
- One tested fact per card. Spread across all six cities.
- Base every statement strictly on the digest/overview.
""",
    "D_practice": """You create Anki cards for the Städtebau I exam (ETH). Below you get
the exam expectations, the official tools/clusters overview, and a lecture digest.

Create ATOMIC practice scaffolds for Comparisons and Analyses (not essay dumps).
Target ~25-40 small cards that rehearse the exam moves:
- Comparison 1: for 2–3 given cities, cards like "good second city to compare with X?",
  "one similarity between X and Y?", "one difference …?", naming one tool/cluster each.
- Comparison 2: for 3–4 tool pairs, separate cards: purpose of A, purpose of B,
  shared cluster/theme, one transferability difference — not one mega-comparison answer.
- Analysis transfer: use 'City challenges and applyable tools'. For several
  (city, challenge) pairs create separate cards:
  which foreign tool?, origin city?, thematic cluster to name?, one adaptation step?,
  one drawing must-show (temporality / multi-scale / legend)?
- Drawing checklist: one requirement per card (not one long checklist dump).
""",
}


def main() -> int:
    api_key = os.environ.get("POE_API_KEY")
    if not api_key:
        print("Error: set POE_API_KEY")
        return 1
    config = AppConfig(poe_api_key=api_key)

    expectations = (INFO_DIR / "exam expextations.txt").read_text(
        encoding="utf8", errors="replace"
    )
    tools_pdf = extract_text_from_pdf(INFO_DIR / "Tools and thematic clusters.pdf")
    print(f"  Expectations: {len(expectations)} chars, tools PDF: {len(tools_pdf)} chars")

    digest = build_knowledge_digest()
    supermemo = load_supermemo_rules()

    shared_context = (
        "=== SUPERMEMO FORMULATION RULES (binding) ===\n"
        f"{supermemo}\n\n"
        "=== EXAM EXPECTATIONS (student report, authoritative for exam style) ===\n"
        f"{expectations}\n\n"
        "=== OFFICIAL TOOLS AND THEMATIC CLUSTERS OVERVIEW ===\n"
        f"{tools_pdf}\n\n"
        "=== LECTURE KNOWLEDGE DIGEST (existing Anki cards) ===\n"
        f"{digest}\n"
    )

    deck = genanki.Deck(generate_deck_id(DECK_NAME), DECK_NAME)
    model = create_anki_model(DECK_NAME)

    total = 0
    for section, prompt in SECTION_PROMPTS.items():
        print(f"\n  Generating section {section}...")
        full_prompt = prompt + COMMON_RULES + "\n" + shared_context
        response = call_poe_api(full_prompt, config, model=config.model_name)
        if not response:
            print(f"  Warning: section {section} failed, skipping")
            continue
        cards = parse_cards(response)
        print(f"    {len(cards)} cards")
        for card in cards:
            note = create_note(
                model=model,
                question=card.question,
                answer=card.answer,
                lecture_name=DECK_NAME,
            )
            deck.add_note(note)
            total += 1

    if total == 0:
        print("Error: no cards generated")
        return 1

    COURSE_OUTPUT.mkdir(parents=True, exist_ok=True)
    tmp_path = OUTPUT_APKG.with_suffix(".apkg.tmp")
    genanki.Package(deck).write_to_file(str(tmp_path))
    valid, message = validate_apkg_media(str(tmp_path))
    if not valid:
        tmp_path.unlink(missing_ok=True)
        print(f"Error: validation failed: {message}")
        return 1
    if OUTPUT_APKG.exists():
        OUTPUT_APKG.unlink()
    os.replace(tmp_path, OUTPUT_APKG)
    print(f"\n[OK] Exam prep deck: {OUTPUT_APKG} ({total} cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
