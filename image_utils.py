"""
Resizes and compresses an uploaded photo before it's stored or sent to
Groq. A garment photo doesn't need to be huge to be classified or
displayed — shrinking it keeps R2 storage far under the free tier and
makes the Groq vision call faster/cheaper.
"""

from PIL import Image, ImageOps

MAX_DIMENSION = 1000  # pixels, longest side
JPEG_QUALITY = 82


def process_image(input_path: str, output_path: str) -> None:
    """
    Reads the image at input_path, resizes it (if needed) so its longest
    side is MAX_DIMENSION, corrects orientation from EXIF data (phone
    photos are often rotated only in metadata), converts to RGB, and
    saves as a compressed JPEG at output_path.
    """
    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img)  # fix phone photo rotation

        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")

        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
        img.save(output_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
