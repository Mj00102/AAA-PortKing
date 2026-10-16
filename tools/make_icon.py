# -*- coding: utf-8 -*-
"""生成应用图标 assets/app.ico (纯代码生成, 不依赖 Pillow)

图标含义: 深色圆角底 + 一个网络端口插口 (两个针脚 + 指示灯)
输出: 16/32/48/64/128/256 六种尺寸的 ICO (BMP 格式, 兼容 PyInstaller)
"""
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "assets", "app.ico")

BG = (30, 41, 59, 255)        # #1e293b 深色底
EDGE = (56, 189, 248, 255)    # #38bdf8 插口外框
PIN = (34, 197, 94, 255)      # #22c55e 针脚
LAMP = (250, 204, 21, 255)    # #facc15 指示灯


def render(size):
    """画一张 size x size 的 RGBA 图, 返回逐行像素 (自下而上, 符合 BMP)。"""
    S = float(size)
    cx = cy = S / 2.0
    r_out = S * 0.46                  # 圆角矩形半径 (用圆形近似即可)
    body_w, body_h = S * 0.62, S * 0.44
    bx0, by0 = cx - body_w / 2, cy - body_h / 2 + S * 0.06
    bx1, by1 = cx + body_w / 2, cy + body_h / 2 + S * 0.06
    thick = max(1.0, S * 0.075)
    pin_w = max(1.0, S * 0.085)
    pin_top = cy - S * 0.34
    lamp_r = max(1.0, S * 0.075)

    rows = []
    for y in range(size - 1, -1, -1):          # BMP: 自下而上
        row = []
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            c = (0, 0, 0, 0)
            # 深色圆角底
            if (px - cx) ** 2 + (py - cy) ** 2 <= r_out ** 2:
                c = BG
            # 插口外框 (矩形描边)
            if bx0 <= px <= bx1 and by0 <= py <= by1:
                inside = (bx0 + thick <= px <= bx1 - thick) and (by0 + thick <= py <= by1 - thick)
                if not inside:
                    c = EDGE
            # 两个针脚
            for px_center in (cx - body_w * 0.26, cx + body_w * 0.26):
                if abs(px - px_center) <= pin_w / 2 and pin_top <= py <= by0 + thick * 0.5:
                    c = PIN
            # 指示灯
            if (px - cx) ** 2 + (py - (by0 + body_h / 2)) ** 2 <= lamp_r ** 2:
                c = LAMP
            row.append(c)
        rows.append(row)
    return rows


def bmp_entry(rows, size):
    """把像素转成 ICO 里的 BMP (BITMAPINFOHEADER + BGRA + AND 掩码)。"""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         size * size * 4, 0, 0, 0, 0)
    body = bytearray()
    for row in rows:
        for (r, g, b, a) in row:
            body += bytes((b, g, r, a))
    # AND 掩码: 每行按 4 字节对齐, 全 0 表示全部使用 alpha
    mask_row = ((size + 31) // 32) * 4
    body += b"\x00" * (mask_row * size)
    return header + bytes(body)


def main():
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [(s, bmp_entry(render(s), s)) for s in sizes]

    entries, offset = [], 6 + 16 * len(images)
    for s, data in images:
        entries.append(struct.pack("<BBBBHHII", s if s < 256 else 0, s if s < 256 else 0,
                                   0, 0, 1, 32, len(data), offset))
        offset += len(data)

    with open(OUT, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(images)))
        for e in entries:
            f.write(e)
        for _s, data in images:
            f.write(data)

    print("已生成 %s  (%.1f KB, %d 个尺寸)"
          % (OUT, os.path.getsize(OUT) / 1024, len(images)))


if __name__ == "__main__":
    main()
