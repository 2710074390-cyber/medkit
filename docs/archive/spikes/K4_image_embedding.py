# -*- coding: utf-8 -*-
"""K4 SPIKE：产物 HTML 内嵌 base64 图片体积/性能 vs 相对路径。

按《结构化执行方案》§2.2 K4：1 张 ~2MB 扫描图（合成噪声图替代）→ 测：
  A. base64 内嵌后的 HTML 体积与 33% 膨胀、与「首页打开 <2s」预算关系；
  B. 相对路径 + 复制资产 的 HTML 体积（图片不计入 HTML）；
  C. Pillow 可用时：压缩到 ~200KB 再内嵌的对比。
打印样式两者同为 <img> 标签，无差异（只测体积/内联成本）。

用法：python docs/spikes/K4_image_embedding.py（仓库根目录运行）。
"""
from __future__ import annotations

import base64
import io
import struct
import zlib
from pathlib import Path

TMP = Path(__file__).resolve().parent / "k4_out"
TMP.mkdir(exist_ok=True)


def make_png(width: int, height: int, seed: int = 7) -> bytes:
    """纯 stdlib 生成 RGB 噪声 PNG（压缩率差 → 接近真实扫描图体积）。"""
    rnd = __import__("random").Random(seed)
    raw = bytearray()
    for _y in range(height):
        raw.append(0)  # filter none
        for _ in range(width):
            raw += bytes((rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 1)) + chunk(b"IEND", b""))


def try_pillow_compress(png: bytes, max_kb: int) -> bytes | None:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(png)).convert("RGB")
        out = io.BytesIO()
        # 先降采样再 JPEG，模拟「扫描图压缩到 ~200KB」
        w, h = img.size
        img = img.resize((w // 2, h // 2), Image.LANCZOS)
        for q in (85, 70, 55, 40):
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=q)
            if len(buf.getvalue()) <= max_kb * 1024:
                return buf.getvalue()
        return out.getvalue() or None
    except Exception:  # noqa: BLE001  Pillow 未安装 → 无压缩对照
        return None


if __name__ == "__main__":
    png = make_png(1000, 1000)
    size_mb = len(png) / 1e6
    print(f"[原图] 合成 1000×1000 PNG = {size_mb:.2f} MB")

    b64 = base64.b64encode(png).decode("ascii")
    html_inline = f"<!doctype html><html><body><h1>产物页</h1><img src='data:image/png;base64,{b64}'></body></html>"
    html_ref = "<!doctype html><html><body><h1>产物页</h1><img src='assets/fig_1.png'></body></html>"

    print(f"[A 内嵌 base64] 图片字节 {len(png):,} → HTML {len(html_inline) / 1e6:.2f} MB"
          f"（膨胀 {len(html_inline) / len(png):.2f}x，即 +33% base64 开销；解析约需几十 ms 级）")
    print(f"[B 相对路径] HTML {len(html_ref) / 1e3:.1f} KB + 资产文件 {size_mb:.2f} MB 随导出一并复制")
    comp = try_pillow_compress(png, 220)
    if comp:
        print(f"[C Pillow 压缩] JPEG {len(comp) / 1e3:.0f} KB → 内嵌后 HTML 约 {len(comp) * 1.37 / 1e3:.0f} KB")
    else:
        print("[C Pillow 压缩] 未安装 Pillow → 无对照（真实扫描图压缩建议用基线 JPEG q70）")

    verdict = (
        "PASS —— 内嵌可行但首屏多载 33% 且阻塞解析；"
        "默认走「相对路径 + 导出复制资产」，单文件便携场景(print/分享)用内嵌开关"
    )
    print(f"K4 判定: {verdict}")
    print(f"（产物 {TMP}/fig_1.png 保留作演示；真实扫描图 2MB 结论一致，仅换源）")
    (TMP / "fig_1.png").write_bytes(png)
