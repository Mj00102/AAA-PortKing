# -*- coding: utf-8 -*-
"""AAA端口大王 自测脚本

验证: 地址解析 / 服务识别 / 内核 API 与 netstat 一致性 / 真实服务全链路
      (扫描 -> 识别进程 -> 浏览器地址 -> 停止服务) / 危险进程保护

用法: python test_portking.py
不需要任何第三方依赖; 需要能访问本机端口 (普通用户权限即可)
"""
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portking as pk  # noqa: E402

# 必须尽早做: CI 的 Windows runner 控制台是 cp1252, 打印中文会直接抛
# UnicodeEncodeError 让脚本挂掉 (和被测功能无关)。实现在主程序里。
pk.force_utf8_output()

PORT = 8765
FAIL = []


def check(name, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(name)


def port_free(p):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        return True
    except OSError:
        return False
    finally:
        s.close()




def main():
    """跑完全部检查, 返回失败项列表。"""
    print("=" * 68)
    print("AAA端口大王 自测")
    print("=" * 68)

    # ---------- 1. 纯函数 ----------
    print("\n[1] 地址解析 / 服务识别")
    check("split_addr IPv4", pk.split_addr("0.0.0.0:8080") == ("0.0.0.0", "8080"),
          pk.split_addr("0.0.0.0:8080"))
    check("split_addr IPv6", pk.split_addr("[::]:443") == ("::", "443"),
          pk.split_addr("[::]:443"))
    check("netstat 行解析", pk.NETSTAT_RE.match(
        "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1880") is not None)
    check("netstat UDP 行解析", pk.NETSTAT_RE.match(
        "  UDP    0.0.0.0:500            *:*                                    6212") is not None)
    check("端口识别 3306 -> MySQL", pk.service_hint(3306)[0] == "MySQL")
    check("端口识别 8080 是网页服务", pk.service_hint(8080)[1] is True)
    check("端口识别 5173 是网页服务", pk.service_hint(5173)[1] is True)
    check("未知端口返回空", pk.service_hint(54321) == (None, False))

    # ---------- 2. 内核 API vs netstat ----------
    print("\n[2] 内核 API 与 netstat 一致性 / 性能")
    api = pk._list_ports_api()
    ns = pk._list_ports_netstat()
    check("API 有数据", len(api) > 5, "%d 条" % len(api))
    check("netstat 有数据", len(ns) > 5, "%d 条" % len(ns))

    cols = lambda rows: {(r["proto"], r["host"], r["port"], r["state"]) for r in rows}
    # 两次快照之间瞬时连接必然在变 (TIME_WAIT 等), 所以只比对稳定子集
    STABLE = ("LISTENING", "UDP")
    stable = lambda rows: {k for k in cols(rows) if k[3] in STABLE}
    sa, sn = stable(api), stable(ns)
    check("监听端口/UDP 完全一致", sa == sn, "API %d 项 / netstat %d 项" % (len(sa), len(sn)))
    if sa != sn:
        print("        仅 API:", sorted(sa - sn, key=str)[:5])
        print("        仅 netstat:", sorted(sn - sa, key=str)[:5])


    def bench(f, n=7):
        xs = []
        for _ in range(n):
            t0 = time.perf_counter()
            f()
            xs.append((time.perf_counter() - t0) * 1000)
        xs.sort()
        return xs[len(xs) // 2]


    t_api, t_ns = bench(pk._list_ports_api), bench(pk._list_ports_netstat)
    print("        API %.2f ms  vs  netstat %.1f ms  (快 %.0f 倍)"
          % (t_api, t_ns, t_ns / max(t_api, .01)))
    check("API 比 netstat 快 5 倍以上", t_ns / max(t_api, .01) > 5,
          "%.2f ms vs %.1f ms" % (t_api, t_ns))
    check("IPv6 行也被解析", any(r["v6"] for r in api), sum(1 for r in api if r["v6"]))

    # ---------- 3. 真实服务全链路 ----------
    print("\n[3] 真实服务全链路 (起一个 http 服务在 %d)" % PORT)
    proc = None
    if port_free(PORT):
        proc = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT),
                                 "--bind", "127.0.0.1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
    else:
        print("  端口 %d 已被占用, 跳过" % PORT)

    if proc:
        entries = pk.list_ports()
        mine = [e for e in entries if e["port"] == PORT and e["state"] == "LISTENING"]
        check("扫到监听中的 %d" % PORT, len(mine) == 1, mine)
        if mine:
            check("PID 与子进程一致", mine[0]["pid"] == proc.pid,
                  "netstat=%s 实际=%s" % (mine[0]["pid"], proc.pid))
            rows = pk.build_rows(entries, resolve_names=False)
            r = [x for x in rows if x.port == PORT]
            check("聚合出端口行", len(r) == 1 and r[0].listen, r)
            wp = pk.WinProc()
            path = wp.path_of(proc.pid)
            check("能解析到 python 进程路径", bool(path) and "python" in (path or "").lower(), path)
            check("浏览器地址正确", pk.open_target(PORT, entries) == "http://127.0.0.1:%d" % PORT,
                  pk.open_target(PORT, entries))

    # ---------- 4. GUI ----------
    print("\n[4] GUI 层 (窗口隐藏运行)")
    import tkinter as tk  # noqa: E402
    root = tk.Tk()
    root.withdraw()
    app = pk.PortPilotApp(root)
    root.update()
    check("GUI 构建成功", app.tree is not None)
    check("表格有数据", len(app.tree.get_children()) > 0, "%d 行" % len(app.tree.get_children()))

    app.var_listen.set(True)
    app.apply_filter()
    check("只看监听 生效", all(r.listen for r in app.filtered))
    app.var_listen.set(False)
    app.apply_filter()

    if proc:
        app.var_search.set(str(PORT))
        app.apply_filter()
        check("搜索端口 命中 1 行", len(app.filtered) == 1, [r.port for r in app.filtered])
        app.var_search.set("")
        app.apply_filter()

    app.sort_by("port")
    app.sort_by("port")
    check("排序不报错", True)

    # 拦截 webbrowser, 验证打开地址而不真的弹窗口
    opened = []
    real_open = pk.webbrowser.open
    pk.webbrowser.open = lambda u, *a, **k: (opened.append(u), True)[1]
    try:
        app.open_port(PORT)
        check("open_port 调用浏览器", opened == ["http://127.0.0.1:%d" % PORT], opened)

        if proc:
            # 无头 CI 下窗口被 withdraw, tree.bbox() 返回空、identify_row 不可靠,
            # 所以不模拟鼠标事件 (那会导致 Ctrl+点击 打开到错误的端口)。
            # 改测等价的「选中行 + 打开」路径 (Ctrl+点击/双击 最终也是调 open_port)。
            app.tree.selection_set(str(PORT))
            app.tree.focus(str(PORT))
            app.open_selected()
            check("选中行 + 打开 触发浏览器",
                  opened[-1] == "http://127.0.0.1:%d" % PORT, opened)
    finally:
        pk.webbrowser.open = real_open

    # ---------- 5. 危险进程保护 ----------
    print("\n[5] 停止服务前的保护判断")
    check("保护本进程", app._kill_block_reason(os.getpid(), "python.exe") is not None)
    check("保护 PID 0/4", app._kill_block_reason(4, "System") is not None)
    check("保护 lsass", app._kill_block_reason(1234, "lsass.exe") is not None)
    check("保护 svchost", app._kill_block_reason(1234, "svchost.exe") is not None)
    check("读不到进程名时拦截", app._kill_block_reason(9999, "<无法读取, 可能是系统进程>") is not None)
    check("进程名缺失时拦截", app._kill_block_reason(9999, "?") is not None)
    check("普通进程放行", app._kill_block_reason(1234, "node.exe") is None)
    check("普通进程放行2", app._kill_block_reason(1234, "mysqld.exe") is None)

    # ---------- 6. 停止服务 ----------
    print("\n[6] 停止服务")
    if proc:
        app.refresh(silent=True)
        ok, msg = app.winproc.kill(proc.pid)
        print("       kill -> %s | %s" % (ok, str(msg).replace("\n", " ")))
        time.sleep(1.0)
        alive = proc.poll() is None
        if ok:
            check("进程已被结束", not alive)
            entries2 = pk.list_ports()
            check("端口已释放", not [e for e in entries2
                                   if e["port"] == PORT and e["state"] == "LISTENING"])
        else:
            print("  [SKIP] 结束进程 (当前环境权限受限: %s)" % str(msg).splitlines()[0])
        if alive:
            proc.kill()
        proc.wait()

    root.destroy()
    print("\n" + "=" * 68)
    print("结果: %s" % ("全部通过 [OK]" if not FAIL else "失败 %d 项: %s" % (len(FAIL), FAIL)))
    print("=" * 68)
    return FAIL


if __name__ == "__main__":
    _failed = main()
    sys.exit(1 if _failed else 0)
