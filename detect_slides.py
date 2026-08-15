python

import cv2
import os
from skimage.metrics import structural_similarity as ssim

FRAME_DIR = "data/frames"
SSIM_THRESHOLD = 0.85
STABLE_FRAMES = 3  # seconds

def load_gray(path):
    img = cv2.imread(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

def detect_slide_changes():
    frames = sorted(os.listdir(FRAME_DIR))
    prev = load_gray(os.path.join(FRAME_DIR, frames[0]))
    changes = []

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
                changes.append(i)
                prev = curr

    return changes
