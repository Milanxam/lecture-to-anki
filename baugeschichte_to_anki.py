import os
import re
import cv2
import subprocess
import requests
from skimage.metrics import structural_similarity as ssim

# ========================
# CONFIG
# ========================

VIDEO_FILE = "lecture.mp4"
VTT_FILE = "data/lecture.vtt"

FRAME_DIR = "data/frames"
SCREENSHOT_DIR = "data/screenshots"
AUDIO_DIR = "data/audio"

SSIM_THRESHOLD = 0.85
STABLE_FRAMES = 3

LOW_TEXT_THRESHOLD = 120  # characters

POE_API_KEY = os.environ.get("POE_API_KEY", "")

# ========================
# UTILITIES
# ========================

def ensure_dirs():
    os.makedirs(FRAME_DIR, exist_ok=True)
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    os.makedirs(AUDIO_DIR, exist_ok=True)

def time_to_seconds(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

def clean_text(text):
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# ========================
# STEP 1 — FRAME EXTRACTION
# ========================

def extract_frames():
    print("Extracting frames...")
    subprocess.run([
        "ffmpeg", "-i", VIDEO_FILE,
        "-vf", "fps=1",
        f"{FRAME_DIR}/frame_%06d.jpg",
        "-y"
    ], check=True)

# ========================
# STEP 2 — SLIDE DETECTION
# ========================

def load_gray(path):
    img = cv2.imread(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

def detect_slide_changes():
    frames = sorted(os.listdir(FRAME_DIR))
    prev = load_gray(os.path.join(FRAME_DIR, frames[0]))
    slide_starts = [0]

    for i in range(1, len(frames)):
        curr = load_gray(os.path.join(FRAME_DIR, frames[i]))
        score = ssim(prev, curr)

        if score < SSIM_THRESHOLD:
            stable = True
            base = curr
            for j in range(1, STABLE_FRAMES):
                if i + j >= len(frames):
                    break
                test = load_gray(os.path.join(FRAME_DIR, frames[i + j]))
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

    print(f"Detected {len(slides)} slides")
    return slides

# ========================
# STEP 3 — VTT PARSING
# ========================

def parse_vtt(path):
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

def transcript_for_slide(vtt_entries, start, end):
    texts = []
    for e in vtt_entries:
        if e["end"] >= start and e["start"] <= (end or 1e9):
            texts.append(e["text"])
    return clean_text(" ".join(texts))

# ========================
# STEP 4 — MEDIA EXTRACTION
# ========================

def extract_media(start, end, idx):
    screenshot = f"{SCREENSHOT_DIR}/slide_{idx}.png"
    audio = f"{AUDIO_DIR}/slide_{idx}.mp3"

    subprocess.run([
        "ffmpeg", "-ss", str(start + 2),
        "-i", VIDEO_FILE,
        "-frames:v", "1",
        screenshot, "-y"
    ])

    if end:
        subprocess.run([
            "ffmpeg", "-ss", str(start),
            "-to", str(end),
            "-i", VIDEO_FILE,
            audio, "-y"
        ])

    return screenshot, audio if end else None

# ========================
# STEP 5 — CARD GENERATION
# ========================

def generate_cards(text, is_visual_slide):
    with open("prompts/baugeschichte_exam_prompt.txt", "r", encoding="utf8") as f:
        base_prompt = f.read()

    if is_visual_slide:
        base_prompt += (
            "\n\nNote: This slide is primarily an image (e.g. plan, facade, building photo). "
            "Base the cards on the spoken explanation of what is shown and how it is interpreted."
        )

    # Combine system prompt and user text
    full_prompt = f"{base_prompt}\n\nText to process:\n{text}"
    
    # Use Poe API directly with requests
    headers = {
        "Authorization": f"Bearer {POE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "version": "1.0",
        "type": "query",
        "query": [
            {
                "role": "user",
                "content": full_prompt
            }
        ],
        "bot": "Claude-3-Sonnet"
    }
    
    try:
        response = requests.post("https://api.poe.com/bot/", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        return result.get("text", "Error generating cards")
    except Exception as e:
        print(f"Error calling Poe API: {e}")
        return f"Error: Could not generate cards - {e}"

# ========================
# MAIN
# ========================

def main():
    ensure_dirs()

    extract_frames()
    slides = detect_slide_changes()
    vtt = parse_vtt(VTT_FILE)

    i = 0
    while i < len(slides):
        start, end = slides[i]
        text = transcript_for_slide(vtt, start, end)

        # merge with next slide if current slide has very little speech
        while len(text) < LOW_TEXT_THRESHOLD and i + 1 < len(slides):
            next_start, next_end = slides[i + 1]
            text += " " + transcript_for_slide(vtt, next_start, next_end)
            end = next_end
            i += 1

        text = clean_text(text)
        is_visual_slide = len(text) < 300

        print(f"\n--- SLIDE GROUP ending at {end}s ---")
        screenshot, audio = extract_media(start, end, i)
        cards = generate_cards(text, is_visual_slide)

        print(cards)
        i += 1

if __name__ == "__main__":
    main()