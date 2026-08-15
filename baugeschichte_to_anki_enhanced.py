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

DECK_NAME = "Baugeschichte"
MODEL_NAME = "Basic"

SSIM_THRESHOLD = 0.85
STABLE_FRAMES = 3  # Restored from original for accuracy
LOW_TEXT_THRESHOLD = 120

POE_API_KEY = os.environ.get("POE_API_KEY", "")

ANKI_URL = "http://localhost:8765"

# ========================
# UTILITIES
# ========================

def ensure_dirs():
    for d in [FRAME_DIR, SCREENSHOT_DIR, AUDIO_DIR]:
        os.makedirs(d, exist_ok=True)

def time_to_seconds(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

def clean_text(text):
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# ========================
# STEP 1 — FRAMES
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
# STEP 2 — SLIDES (Restored accurate detection)
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
            # Check for stability (restored from original for accuracy)
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
# STEP 3 — VTT (Enhanced parsing)
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
# STEP 4 — MEDIA (Enhanced extraction)
# ========================

def extract_media(start, end, idx):
    screenshot = f"{SCREENSHOT_DIR}/slide_{idx}.png"
    audio = f"{AUDIO_DIR}/slide_{idx}.mp3"

    # Extract screenshot (with 2-second offset for stability)
    subprocess.run([
        "ffmpeg", "-ss", str(start + 2),
        "-i", VIDEO_FILE,
        "-frames:v", "1",
        screenshot, "-y"
    ], capture_output=True)  # Suppress FFmpeg output

    # Extract audio segment
    if end:
        subprocess.run([
            "ffmpeg", "-ss", str(start),
            "-to", str(end),
            "-i", VIDEO_FILE,
            audio, "-y"
        ], capture_output=True)  # Suppress FFmpeg output

    return screenshot, audio if end else None

# ========================
# STEP 5 — POE API (Enhanced for quality)
# ========================

def generate_cards(text, is_visual_slide):
    with open("prompts/baugeschichte_exam_prompt.txt", "r", encoding="utf8") as f:
        base_prompt = f.read()

    if is_visual_slide:
        base_prompt += (
            "\n\nNote: This slide is primarily an image (e.g. plan, facade, building photo). "
            "Base the cards on the spoken explanation of what is shown and how it is interpreted."
        )

    # Enhanced prompt for better quality
    full_prompt = f"{base_prompt}\n\nText to process:\n{text}"
    
    headers = {
        "Authorization": f"Bearer {POE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Using OpenAI-compatible format for Poe API
    payload = {
        "model": "GPT-5.2-Instant",
        "messages": [
            {
                "role": "user",
                "content": full_prompt
            }
        ],
        "max_tokens": 1000,
        "temperature": 0.2
    }
    
    try:
        response = requests.post("https://api.poe.com/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        # Extract text from OpenAI-compatible response format
        return result.get("choices", [{}])[0].get("message", {}).get("content", "Error generating cards")
    except Exception as e:
        print(f"Error calling Poe API: {e}")
        return f"Error: Could not generate cards - {e}"

# ========================
# STEP 6 — ANKI (Enhanced with error handling)
# ========================

def create_deck_if_needed():
    """Ensure the Baugeschichte deck exists"""
    try:
        response = requests.post(ANKI_URL, json={
            "action": "createDeck",
            "version": 6,
            "params": {"deck": DECK_NAME}
        })
        return response.status_code == 200
    except Exception as e:
        print(f"Warning: Could not create/verify deck: {e}")
        return False

def add_note(front, back, image_path, audio_path):
    """Add a note to Anki with enhanced error handling"""
    note = {
        "deckName": DECK_NAME,
        "modelName": MODEL_NAME,
        "fields": {"Front": front, "Back": back},
        "tags": ["baugeschichte", "lecture"],
        "options": {"allowDuplicate": False}
    }

    # Add media files if they exist
    if image_path and os.path.exists(image_path):
        note["picture"] = [{
            "path": os.path.abspath(image_path),
            "filename": os.path.basename(image_path),
            "fields": ["Back"]
        }]

    if audio_path and os.path.exists(audio_path):
        note["audio"] = [{
            "path": os.path.abspath(audio_path),
            "filename": os.path.basename(audio_path),
            "fields": ["Back"]
        }]

    try:
        response = requests.post(ANKI_URL, json={
            "action": "addNote",
            "version": 6,
            "params": {"note": note}
        })
        
        if response.status_code == 200:
            result = response.json()
            if result.get("error"):
                print(f"Anki error: {result['error']}")
                return False
            else:
                print(f"Added card: {front[:50]}...")
                return True
        else:
            print(f"HTTP error: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"Error adding note to Anki: {e}")
        return False

def parse_cards(cards_text):
    """Enhanced card parsing with validation"""
    cards = []
    blocks = cards_text.split("\n\n")
    
    for block in blocks:
        block = block.strip()
        if not block or not block.startswith("Q:"):
            continue
            
        if "\nA:" not in block:
            print(f"Warning: Malformed card block: {block[:100]}...")
            continue
            
        try:
            question, answer = block.split("\nA:", 1)
            question = question.replace("Q:", "").strip()
            answer = answer.strip()
            
            if question and answer:
                cards.append((question, answer))
            else:
                print(f"Warning: Empty question or answer in block")
                
        except Exception as e:
            print(f"Error parsing card block: {e}")
            continue
    
    return cards

# ========================
# MAIN (Enhanced workflow)
# ========================

def main():
    print("Starting Baugeschichte to Anki conversion...")
    
    ensure_dirs()
    
    # Check Anki connection
    create_deck_if_needed()
    
    # Process video if frames don't exist
    if not os.path.exists(FRAME_DIR) or len(os.listdir(FRAME_DIR)) == 0:
        extract_frames()
    else:
        print("Using existing frames...")
    
    slides = detect_slide_changes()
    vtt = parse_vtt(VTT_FILE)

    successful_cards = 0
    total_slides = 0
    
    i = 0
    while i < len(slides):
        start, end = slides[i]
        text = transcript_for_slide(vtt, start, end)

        # Merge slides with insufficient content (restored from original)
        while len(text) < LOW_TEXT_THRESHOLD and i + 1 < len(slides):
            next_start, next_end = slides[i + 1]
            text += " " + transcript_for_slide(vtt, next_start, next_end)
            end = next_end
            i += 1

        text = clean_text(text)
        is_visual_slide = len(text) < 300

        print(f"\n--- Processing slide group {total_slides + 1} (ending at {end}s) ---")
        print(f"Text length: {len(text)} chars, Visual slide: {is_visual_slide}")
        
        # Extract media
        screenshot, audio = extract_media(start, end, total_slides)
        
        # Generate cards
        cards_text = generate_cards(text, is_visual_slide)
        
        if "Error" in cards_text:
            print(f"Skipping slide due to generation error")
            i += 1
            total_slides += 1
            continue
        
        # Parse and add cards
        cards = parse_cards(cards_text)
        print(f"Generated {len(cards)} cards")
        
        for question, answer in cards:
            if add_note(question, answer, screenshot, audio):
                successful_cards += 1
        
        total_slides += 1
        i += 1

    print(f"\n=== SUMMARY ===")
    print(f"Processed {total_slides} slide groups")
    print(f"Successfully added {successful_cards} cards to Anki")
    print(f"Deck: {DECK_NAME}")

if __name__ == "__main__":
    main()
