# -*- coding: utf-8 -*-
"""回归测试: 确保在"控制台编码不是 UTF-8"的环境下测试脚本不会崩。

背景（真实踩过的坑）:
GitHub Actions 的 Windows runner 控制台代码页是 cp1252。
第一版 test_portking.py 直接 print 中文, 于是 CI 一开始就炸:

    UnicodeEncodeError: 'charmap' codec can't encode characters in position 3-6

这和被测功能毫无关系, 纯粹是输出编码问题。这个脚本把那个环境本地复现出来,
防止以后有人把编码兜底删掉。

它会做两件事:
  A. 去掉编码兜底 -> 确认这个环境下确实会崩 (证明模拟是有效的)
  B. 用当前代码   -> 确认能正常跑完

用法: python tools\\test_encoding.py
退出码 0 = 两种情况都符合预期

注意(踩过一次): 本脚本自己打印中文时, 必须先确认 stdout 是 UTF-8。
       否则会重演它要测的那个 bug —— 在 cp1252 下打印报错信息又崩一次。
"""
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
TEST_SRC = os.path.join(REPO, "test_portking.py")

sys.path.insert(0, REPO)
import portking as pk  # noqa: E402

# 先把自己的输出弄成 UTF-8, 并且记下原样以便之后还原
pk.force_utf8_output()
_ORIG_ENCODING = sys.stdout.encoding


def _restore(out):
    """把 stdout 还原成 UTF-8。必须在任何 print 之前调用 —— 否则中文打印会崩。"""
    try:
        out.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def run_in_cp1252(code, name):
    """在 cp1252 + strict 的 stdout 下执行代码。

    返回 (是否崩在编码, main() 的返回值, 说明文本)。
    """
    out = sys.stdout
    try:
        out.reconfigure(encoding="cp1252", errors="strict")
    except Exception as exc:  # noqa: BLE001
        return None, None, "无法切到 cp1252: %s" % exc

    crashed, failed, detail = False, None, ""
    try:
        ns = {"__name__": "sim_" + name, "__file__": TEST_SRC}
        exec(compile(code, name, "exec"), ns)
        failed = ns["main"]()
    except UnicodeEncodeError as exc:
        crashed = True
        detail = str(exc)[:70]
    except Exception as exc:  # noqa: BLE001
        detail = "%s: %s" % (type(exc).__name__, str(exc)[:70])
    finally:
        # 关键顺序: 先恢复 UTF-8, 再做任何输出
        _restore(out)

    if crashed:
        print("  [%s] 崩在编码: %s" % (name, detail))
    elif detail:
        print("  [%s] 其它异常: %s" % (name, detail))
    return crashed, failed, detail


def main():
    print("原 stdout 编码: %s" % _ORIG_ENCODING)

    src = open(TEST_SRC, encoding="utf-8").read()
    broken = src.replace("pk.force_utf8_output()", "pass   # 故意去掉, 用于验证模拟有效")
    if broken == src:
        print("找不到 pk.force_utf8_output() 调用 —— test_portking.py 结构变了, 请更新本脚本")
        return 2

    print("=" * 66)
    print("A. 去掉编码兜底 (应当崩在编码上)")
    print("=" * 66)
    a_crashed, _, _ = run_in_cp1252(broken, "_broken_version")
    print("   -> %s" % ("崩在编码, 与 CI 现场一致" if a_crashed else "没崩, 模拟无效"))

    print()
    print("=" * 66)
    print("B. 当前代码 (应当正常跑完)")
    print("=" * 66)
    b_crashed, b_failed, b_detail = run_in_cp1252(src, "_fixed_version")
    print("   -> %s" % ("崩在编码" if b_crashed else "正常跑完, 失败项 = %s" % (b_failed or "无")))

    print()
    print("=" * 66)
    if a_crashed and not b_crashed and not b_detail:
        print("通过: 编码兜底有效 (A 崩 / B 正常)")
        return 0
    print("未通过: A崩=%s B崩=%s B异常=%s" % (a_crashed, b_crashed, b_detail or "无"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
