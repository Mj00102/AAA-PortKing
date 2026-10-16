# -*- coding: utf-8 -*-
"""把 PyInstaller 的文件夹版打包成单文件自解压 exe

用法: python tools\\make_onefile.py
前置: 先跑 PyInstaller --onedir (或直接双击 打包exe.bat)

流程:
  1) 把 dist\\PortKing 压成 zip (deflate)
  2) 用 csc.exe 编译 tools\\launcher.cs
  3) 把 zip 追加到启动器 exe 末尾 -> 真正的单文件
  4) 顺手压出可分发用的 zip
"""
import os
import subprocess
import sys
import zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
SRC_DIR = os.path.join(REPO, "dist", "PortKing")
OUT_EXE = os.path.join(REPO, "dist", "AAA端口大王.exe")
DIST_ZIP = os.path.join(REPO, "dist", "AAA端口大王-单文件版.zip")
ZIP_TMP = os.path.join(TOOLS, "payload.zip")
CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
ICON = os.path.join(REPO, "assets", "app.ico")
MARKER = "PORT_KING_PAYLOAD_V1:"


def zip_dir(src, dst, compresslevel=9):
    n = 0
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as z:
        for root, _dirs, files in os.walk(src):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, src).replace("\\", "/")
                z.write(full, rel)
                n += 1
    return n


def main():
    if not os.path.isdir(SRC_DIR):
        print("找不到文件夹版: %s" % SRC_DIR)
        print("请先跑 PyInstaller --onedir (或直接双击 打包exe.bat)")
        return 1
    if not os.path.exists(CSC):
        print("找不到 C# 编译器: %s" % CSC)
        return 1

    # 1) 压缩
    print("[1/4] 压缩程序文件 ...")
    if os.path.exists(ZIP_TMP):
        os.remove(ZIP_TMP)
    n = zip_dir(SRC_DIR, ZIP_TMP)
    print("      %d 个文件 -> %.2f MB" % (n, os.path.getsize(ZIP_TMP) / 1048576))

    # 2) 编译启动器 (正式版不带日志; 需要调试时给 csc 加 /define:PPLOG)
    print("[2/4] 编译 C# 启动器 ...")
    base_exe = os.path.join(TOOLS, "launcher_base.exe")
    src = os.path.join(TOOLS, "launcher.cs")
    with open(src, "r", encoding="utf-8") as f:
        if MARKER not in f.read():
            print("      launcher.cs 里没有标记 %r" % MARKER)
            return 1
    r = subprocess.run(
        [CSC, "/nologo", "/target:winexe", "/optimize+", "/platform:anycpu",
         "/r:System.Windows.Forms.dll", "/r:System.IO.Compression.dll",
         "/r:System.IO.Compression.FileSystem.dll",
         "/win32icon:" + ICON,
         "/out:" + base_exe, src],
        capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(base_exe):
        print("      编译失败:\n" + (r.stdout or "") + (r.stderr or ""))
        return 1
    print("      启动器 %.0f KB" % (os.path.getsize(base_exe) / 1024))

    # 3) 追加负载 -> 单文件
    print("[3/4] 合成单文件 ...")
    with open(base_exe, "rb") as f:
        head = f.read()
    with open(OUT_EXE, "wb") as out:
        out.write(head)
        with open(ZIP_TMP, "rb") as z:
            while True:
                chunk = z.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
    print("      完成: dist\\AAA端口大王.exe  (%.2f MB)"
          % (os.path.getsize(OUT_EXE) / 1048576))

    # 4) 可分发 zip
    print("[4/4] 生成分发包 ...")
    zip_dir(SRC_DIR, DIST_ZIP)
    print("      dist\\AAA端口大王-单文件版.zip  (%.2f MB)"
          % (os.path.getsize(DIST_ZIP) / 1048576))
    os.remove(ZIP_TMP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
