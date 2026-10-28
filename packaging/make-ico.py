"""Encode multiple icon sizes from the project's vector-rendered PNG."""
from pathlib import Path
from PIL import Image
assets = Path(__file__).resolve().parents[1] / 'assets'
with Image.open(assets / 'app.png') as image:
    image.convert('RGBA').save(assets / 'app.ico', sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
