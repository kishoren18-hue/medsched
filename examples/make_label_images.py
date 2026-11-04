"""Render two fake pharmacy labels (one sharp, one blurry) to test the OCR path.

    python examples/make_label_images.py

The labels are invented; no real pharmacy or patient is depicted.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
LINES = ["DEMO PHARMACY  (sample label)", "", "Amlodipine 5 mg tablets", "Take 1 tablet by mouth once daily",
         "Qty: 30   Refills: 2"]


def font(size):
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(path, blur=0.0):
    img = Image.new("RGB", (1200, 420), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 1189, 409], outline="black", width=4)
    y = 40
    for i, line in enumerate(LINES):
        d.text((50, y), line, fill="black", font=font(46 if i == 2 else 38))
        y += 66
    if blur:
        img = img.resize((300, 105)).filter(ImageFilter.GaussianBlur(blur)).resize((1200, 420))
    img.save(path)


render(HERE / "label_sharp.png")
render(HERE / "label_blurry.png", blur=1.6)
print("wrote label_sharp.png and label_blurry.png")
