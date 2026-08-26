"""生成 medkit.ico —— 与网站 med-review-site.pages.dev 图标同构:
圆角方块(rx=7/32) + 「MW」字标;仅配色(蓝→青绿渐变)与字体(等宽→Segoe UI Bold)不同。
用法:python pack/make_icon.py  (输出 medkit.ico + pack/icon_preview.png)
"""
import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = r"C:\Windows\Fonts\segoeuib.ttf"
SIZES = [16, 24, 32, 48, 64, 128, 256]
# 135° 渐变三档(对齐网站 .brand .logo 的渐变结构,色系换成青绿)
STOPS = [(0.0, (45, 212, 191)), (0.6, (13, 148, 136)), (1.0, (19, 78, 74))]
TEXT = "MW"
TEXT_FILL = (245, 250, 248, 255)


def _interp(t: float) -> tuple[int, int, int]:
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:], strict=False):
        if t <= t1:
            k = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(round(a + (b - a) * k) for a, b in zip(c0, c1, strict=True))  # type: ignore[return-value]
    return STOPS[-1][1]


def draw_icon(size: int) -> Image.Image:
    denom = max(size - 1, 1)
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            px[x, y] = (*_interp((x + y) / (2 * denom)), 255)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=round(size * 7 / 32), fill=255)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    draw = ImageDraw.Draw(img)
    target_w = size * 0.60
    fs = max(6, round(size * 0.55))
    font = ImageFont.truetype(FONT, fs)
    while fs > 6:
        bbox = draw.textbbox((0, 0), TEXT, font=font)
        if bbox[2] - bbox[0] <= target_w:
            break
        fs -= 1
        font = ImageFont.truetype(FONT, fs)
    bbox = draw.textbbox((0, 0), TEXT, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - w) / 2 - bbox[0]
    y = (size - h) / 2 - bbox[1] + size * 0.015
    draw.text((x, y), TEXT, font=font, fill=TEXT_FILL)
    return img


def save_ico(images: list[Image.Image], path: Path) -> None:
    """多尺寸 PNG 帧写入 ICO(Vista+ 支持 PNG 条目,每档原生绘制保清晰)。"""
    blobs = []
    for im in images:
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        blobs.append(buf.getvalue())
    n = len(blobs)
    offset = 6 + 16 * n
    out = struct.pack("<HHH", 0, 1, n)
    for im, blob in zip(images, blobs, strict=True):
        s = im.size[0]
        out += struct.pack("<BBBBHHII", 0 if s >= 256 else s, 0 if s >= 256 else s,
                           0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(out + b"".join(blobs))


if __name__ == "__main__":
    images = [draw_icon(s) for s in SIZES]
    save_ico(images, ROOT / "medkit.ico")
    images[-1].resize((256, 256)).save(ROOT / "pack" / "icon_preview.png")
    print("medkit.ico 已生成:", SIZES, "尺寸")
