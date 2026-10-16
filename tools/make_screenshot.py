# -*- coding: utf-8 -*-
"""给 README 生成界面截图: 注入合成数据, 抓整窗 (依赖 DPI 感知)。"""
import ctypes
import os
import subprocess
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import tkinter as tk  # noqa: E402
import portking as pk  # noqa: E402

OUT = os.path.join(REPO, "assets")

FAKE = [
    ("TCP", "0.0.0.0", 135, "LISTENING", 1880, "svchost.exe"),
    ("TCP", "0.0.0.0", 445, "LISTENING", 4, "System"),
    ("TCP", "127.0.0.1", 3000, "LISTENING", 15120, "node.exe"),
    ("TCP", "0.0.0.0", 3306, "LISTENING", 7200, "mysqld.exe"),
    ("TCP", "127.0.0.1", 5173, "LISTENING", 22140, "node.exe"),
    ("TCP", "0.0.0.0", 6379, "LISTENING", 9132, "redis-server.exe"),
    ("TCP", "127.0.0.1", 8080, "LISTENING", 30412, "java.exe"),
    ("TCP", "0.0.0.0", 8787, "LISTENING", 24956, "python.exe"),
    ("TCP", "0.0.0.0", 27017, "LISTENING", 4488, "mongod.exe"),
    ("UDP", "0.0.0.0", 1900, "UDP", 3296, "svchost.exe"),
    ("TCP", "10.0.0.8", 52100, "ESTABLISHED", 8892, "chrome.exe"),
]
PATHS = {
    1880: r"C:\Windows\System32\svchost.exe", 4: "",
    15120: r"C:\Program Files\nodejs\node.exe",
    7200: r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe",
    22140: r"C:\Program Files\nodejs\node.exe",
    9132: r"C:\Redis\redis-server.exe",
    30412: r"C:\Program Files\Java\jdk-17\bin\java.exe",
    24956: r"C:\Python314\python.exe",
    4488: r"C:\Program Files\MongoDB\Server\7.0\bin\mongod.exe",
    3296: r"C:\Windows\System32\svchost.exe",
    8892: r"C:\Program Files\Google\Chrome\Application\chrome.exe",
}


def build():
    pk.enable_dpi_awareness()
    root = tk.Tk()
    root.geometry("1280x760+0+0")
    app = pk.PortPilotApp(root)
    root.update()
    entries = [dict(proto=p, host=h, port=pt, state=s, pid=pid, v6=False,
                    foreign=("0.0.0.0:0" if s == "LISTENING" else "203.0.113.9:443"))
               for p, h, pt, s, pid, _ in FAKE]
    app.entries = entries
    app.rows = pk.build_rows(entries, resolve_names=False)
    for r in app.rows:
        for pid in r.pids:
            r.names[pid] = next(n for p, h, pt, s, pid2, n in FAKE if pid2 == pid)
            if PATHS.get(pid):
                r.paths[pid] = PATHS[pid]
    app.row_by_port = {r.port: r for r in app.rows}
    app.entries_by_port = {}
    for e in entries:
        app.entries_by_port.setdefault(e["port"], []).append(e)
    app.var_listen.set(False)
    app.apply_filter()
    root.update()
    if "8080" and str(8080) in app.tree.get_children():
        app.tree.selection_set(str(8080))
        app.tree.focus(str(8080))
        app.tree.see(str(8080))
    # 悬停提示栏显示完整服务名
    try:
        bbox = app.tree.bbox(str(8080), "service")
        if bbox:
            root.update()
            app.on_tree_motion(SimpleNamespace(y=bbox[1] + bbox[3] // 2))
            root.update()
    except Exception:
        pass
    return root, app


def shoot(root, name, width, height):
    root.geometry("%dx%d+0+0" % (width, height))
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    for _ in range(6):
        root.update()
        time.sleep(0.15)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w, h = root.winfo_width(), root.winfo_height()
    cap_w, cap_h = min(w, sw - x), min(h, sh - y)
    out = os.path.join(OUT, name)
    ps = ("Add-Type -AssemblyName System.Drawing;"
          "$b=New-Object System.Drawing.Bitmap(%d,%d);"
          "$g=[System.Drawing.Graphics]::FromImage($b);"
          "$g.CopyFromScreen(%d,%d,0,0,$b.Size);"
          "$b.Save('%s',[System.Drawing.Imaging.ImageFormat]::Png);"
          "$g.Dispose();$b.Dispose();" % (cap_w, cap_h, x, y, out))
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=40)
        print("  %s  %dx%d  %.0f KB" % (name, cap_w, cap_h,
                                        os.path.getsize(out) / 1024))
    except subprocess.TimeoutExpired:
        print("  %s 抓屏超时" % name)


root, app = build()
shoot(root, "screenshot.png", 1280, 760)
root.destroy()
print("完成, 输出在 assets/")
