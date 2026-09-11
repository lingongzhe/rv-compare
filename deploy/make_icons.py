# -*- coding: utf-8 -*-
"""生成房车比价通 App 图标（favicon.ico / apple-touch-icon / PWA icons）。

用法: python deploy/make_icons.py
输出: docs/favicon.ico, docs/apple-touch-icon.png, docs/icons/icon-{192,512}.png
"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")
S = 1024          # 超采样画布，最终缩到 512
R = 230           # 圆角半径（512 尺度下约 115）

# 站点配色
BLUE_L = (44, 86, 136)    # #2c5688 渐变亮端
BLUE_D = (30, 58, 95)     # #1e3a5f 渐变暗端
WHITE = (255, 255, 255)
ORANGE = (230, 126, 34)   # #e67e22
GREEN = (127, 209, 160)   # #7fd1a0
WHEEL = (20, 39, 61)      # 深色轮胎


def gradient_square(size):
    """对角线渐变方块（左上亮、右下暗）。"""
    px = bytearray()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            px += bytes(round(a + (b - a) * t) for a, b in zip(BLUE_L, BLUE_D))
    return Image.frombytes("RGB", (size, size), bytes(px))


def draw_rv(d, s):
    """在画布上画房车侧影（坐标按 1024 画布给出，s 为缩放系数=1）。"""
    # 车身（白色圆角矩形）
    d.rounded_rectangle([152, 316, 872, 672], radius=52, fill=WHITE)
    # 车窗 × 3（蓝）
    for x0 in (216, 432, 660):
        x1 = 392 if x0 == 216 else (604 if x0 == 432 else 828)
        d.rounded_rectangle([x0, 372, x1, 492], radius=28, fill=BLUE_D)
    # 橙色饰条
    d.rounded_rectangle([172, 576, 852, 632], radius=28, fill=ORANGE)
    # 车轮（深色胎 + 绿色轮毂）
    for cx in (344, 696):
        d.ellipse([cx - 76, 612 - 0, cx + 76, 612 + 152], fill=WHEEL)
        d.ellipse([cx - 30, 612 + 46, cx + 30, 612 + 106], fill=GREEN)


def build(rounded):
    """rounded=True 透明圆角（favicon 用），False 方形满幅（手机主屏用）。"""
    img = gradient_square(S).convert("RGBA")
    d = ImageDraw.Draw(img)
    draw_rv(d, 1)
    if rounded:
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=R, fill=255)
        img.putalpha(mask)
    else:
        img.putalpha(Image.new("L", (S, S), 255))
    return img


def save_png(img, size, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.resize((size, size), Image.LANCZOS).save(path, "PNG")
    print("OK", path)


def main():
    square = build(False)   # 方形：主屏图标（系统自己加圆角遮罩）
    rounded = build(True)   # 圆角：浏览器 favicon

    save_png(square, 512, os.path.join(DOCS, "icons", "icon-512.png"))
    save_png(square, 192, os.path.join(DOCS, "icons", "icon-192.png"))
    save_png(square, 180, os.path.join(DOCS, "apple-touch-icon.png"))

    # favicon.ico：16/32/48 多尺寸，圆角透明
    ico_path = os.path.join(DOCS, "favicon.ico")
    rounded.resize((48, 48), Image.LANCZOS).save(
        ico_path, format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=[rounded.resize(s, Image.LANCZOS) for s in [(16, 16), (32, 32)]],
    )
    print("OK", ico_path)


if __name__ == "__main__":
    main()
