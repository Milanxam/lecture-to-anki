def transcript_for_slide(vtt_entries, slide_start, slide_end):
    texts = []

    for entry in vtt_entries:
        if entry["end"] >= slide_start and entry["start"] <= (slide_end or 1e9):
            texts.append(entry["text"])

    return " ".join(texts)

def transcript_for_slide(vtt_entries, slide_start, slide_end):
    texts = []

    for entry in vtt_entries:
        if entry["end"] >= slide_start and entry["start"] <= (slide_end or 1e9):
            texts.append(entry["text"])

    return " ".join(texts)

def clean_text(text):
    text = re.sub(r"\s+", " ", text)

    return text.strip()


slides = [(45, 112), (112, 189)]  # example
vtt = parse_vtt("lecture.vtt")

for i, (s, e) in enumerate(slides):
    print(f"\n--- SLIDE {i} ---")
    print(transcript_for_slide(vtt, s, e)[:500])