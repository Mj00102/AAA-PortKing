# -*- coding: utf-8 -*-
"""把当前版本同步到 GitHub（走 gh api，不需要 git 能联网）

什么时候用它:
  正常情况你直接 `git push` 就行。但如果遇到
      fatal: unable to access 'https://github.com/...': Failed to connect
  而 `gh api repos/Mj00102/AAA-PortKing` 又是通的（说明只有 git 的网络被拦），
  就用这个脚本把工作区内容推上去。

它会:
  1. 遍历仓库文件（排除构建中间物和运行时解包目录）
  2. 对每个文件调 Contents API（已存在则带 sha 覆盖）
  3. 每个文件产生一次提交

用法: python tools\\sync_github.py
"""
import base64
import json
import os
import subprocess
import sys
import tempfile

OWNER_REPO = "Mj00102/AAA-PortKing"
BRANCH = "main"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {".git", "__pycache__", "pyi-work", "PortKing-data", "PortKing"}
SKIP_FILES = {"launcher_base.exe", "payload.zip", "PortKing.spec"}


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout or "").strip(), (r.stderr or "").strip()


def api_json(path):
    ok, out, err = gh(["api", path])
    if not ok:
        raise RuntimeError("gh api %s 失败: %s %s" % (path, out, err))
    return json.loads(out) if out else {}


def existing_sha(rel):
    ok, out, _ = gh(["api", "repos/%s/contents/%s" % (OWNER_REPO, rel), "--jq", ".sha"])
    return out.strip() if (ok and out and not out.startswith("{")) else None


def put_file(rel, full):
    with open(full, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    sha = existing_sha(rel)
    body = {
        "message": ("chore: 同步 " + rel) if sha else ("chore: 添加 " + rel),
        "content": b64,
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    # 必须写文件再 --input: 大文件 base64 后超过 Windows 命令行长度上限
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        ok, out, err = gh(["api", "--method", "PUT",
                           "repos/%s/contents/%s" % (OWNER_REPO, rel),
                           "--input", path])
        return ok, ("已更新" if sha else "已创建"), (out + err)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def list_files():
    out = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES or f.endswith((".pyc", ".log")):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, REPO_ROOT).replace("\\", "/")
            if rel.startswith("dist/PortKing/"):
                continue
            out.append((rel, full))
    return sorted(out)


def main():
    files = list_files()
    print("仓库根: %s" % REPO_ROOT)
    print("共 %d 个文件, 开始同步到 %s ...\n" % (len(files), OWNER_REPO))
    ok_n = 0
    for i, (rel, full) in enumerate(files, 1):
        try:
            ok, action, msg = put_file(rel, full)
        except Exception as exc:  # noqa: BLE001
            ok, action, msg = False, "异常", str(exc)
        if ok:
            ok_n += 1
        print("  [%2d/%d] %-4s %-44s %s" % (i, len(files),
                                            action if ok else "失败", rel,
                                            "" if ok else msg[:120]))
    print("\n成功 %d / %d" % (ok_n, len(files)))
    print("仓库: https://github.com/%s" % OWNER_REPO)
    return 0 if ok_n == len(files) else 1


if __name__ == "__main__":
    sys.exit(main())
