"""Optional OCR for label photos (needs `pip install medsched[ocr]` and the tesseract binary).

Returns the text plus a confidence score taken from the lines that matter: the
drug/strength line and the directions. A blurry "10 mg" that might be "40 mg"
pushes the item to "ask", even if the rest of the label read cleanly, while a
smudged refill count does not.
"""

from __future__ import annotations

import re
from collections import defaultdict

_CRITICAL = re.compile(r"\b(?:mg|mcg|ml|units?|take|tablets?|capsules?|daily|hours|times|mouth|puffs?)\b", re.I)
_IGNORE = re.compile(r"\b(?:qty|quantity|refills?|rx|dr\.?|prescriber|exp|date)\b", re.I)


def read_label(image_path: str) -> tuple[str, float]:
    try:
        import pytesseract
        from PIL import Image, ImageOps
    except ImportError as e:  # pragma: no cover
        raise SystemExit("OCR needs: pip install 'medsched[ocr]' and the tesseract program") from e
    img = ImageOps.grayscale(Image.open(image_path))
    if img.width < 1400:  # upscale small photos; tesseract likes ~300 dpi text
        img = img.resize((img.width * 2, img.height * 2))
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    lines = defaultdict(list)
    for i, (w, c) in enumerate(zip(d["text"], d["conf"])):
        if w.strip() and float(c) >= 0:
            lines[(d["block_num"][i], d["par_num"][i], d["line_num"][i])].append((w.strip(), float(c) / 100))
    text_lines, critical = [], []
    for key in sorted(lines):
        words = lines[key]
        line = " ".join(w for w, _ in words)
        text_lines.append(line)
        if _CRITICAL.search(line) and not _IGNORE.search(line):
            critical += [c for _, c in words]
    return "\n".join(text_lines), (min(critical) if critical else 0.0)
