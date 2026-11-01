"""Build windows/spindle.ico from Spindle's PNG icons (needs Pillow)."""
from pathlib import Path

from PIL import Image

root = Path(__file__).resolve().parent.parent
assets = root / "spindle" / "gui" / "assets"
sizes = [16, 24, 32, 48, 64, 128, 256]
big = Image.open(assets / "spindle-256.png").convert("RGBA")
big.save(Path(__file__).with_name("spindle.ico"), sizes=[(s, s) for s in sizes])
print("wrote", Path(__file__).with_name("spindle.ico"))
