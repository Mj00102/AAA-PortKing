# -*- coding: utf-8 -*-
"""
AAA端口大王 - Windows 端口进程一键查看工具
=========================================
功能:
  * 一键刷新: 扫描本机所有监听/连接端口, 自动关联占用它的进程
  * Ctrl + 左键 / 双击: 用默认浏览器打开该端口的本地服务
  * 一键停止服务: 结束占用该端口的进程 (结束前二次确认 + 自保护)
  * 搜索过滤 / 只看监听 / 只看 TCP / 自动刷新 / 一键提权重启

依赖: Python 3.8+ 自带 tkinter + ctypes (无第三方库)
"""

import ctypes
import ctypes.wintypes as wt
import os
import re
import socket
import subprocess
import sys
import time
import webbrowser
from tkinter import (BOTH, END, HORIZONTAL, LEFT, RIGHT, X, Y, BooleanVar,
                     Menu, StringVar, Text, Tk, messagebox)
from tkinter import ttk

APP_NAME = "端口大王"          # 窗口标题 (短名)
APP_FULL = "AAA端口大王"       # 弹窗/关于 里用的全名
APP_VER = "1.0"
CREATE_NO_WINDOW = 0x08000000
IS_WINDOWS = os.name == "nt"


def force_utf8_output():
    """把标准输出/错误切成 UTF-8。

    为什么需要: 程序和服务脚本里有大量中文输出, 而 Windows 上某些环境
    (典型是 CI 的 runner, 控制台代码页 cp1252) 默认编码不是 UTF-8,
    一 print 中文就抛 UnicodeEncodeError 直接终止。
    做成可重复调用、且失败无所谓 (pythonw 下 stdout 本来就是 None)。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ----------------------------------------------------------------------------
# 常量表
# ----------------------------------------------------------------------------

# 常见端口 -> (服务名, 是否通常是网页服务)
COMMON_PORTS = {
    80: ("HTTP 网页", True), 443: ("HTTPS 网页", True), 8080: ("HTTP 备用", True),
    8000: ("HTTP 开发服务", True), 8001: ("HTTP 开发服务", True),
    8888: ("Jupyter / 代理", True), 3000: ("Node / 前端开发", True),
    3001: ("前端开发", True), 4000: ("前端开发", True), 5000: ("Flask / 开发", True),
    5173: ("Vite 前端", True), 5500: ("Live Server", True), 4200: ("Angular", True),
    7860: ("Gradio", True), 8501: ("Streamlit", True), 9000: ("PHP-FPM / 面板", True),
    9090: ("监控面板", True), 9200: ("Elasticsearch", True), 5601: ("Kibana", True),
    15672: ("RabbitMQ 管理", True), 8848: ("Nacos", True), 2375: ("Docker API", True),
    1433: ("SQL Server", False), 1521: ("Oracle", False), 3306: ("MySQL", False),
    33060: ("MySQL X", False), 5432: ("PostgreSQL", False), 6379: ("Redis", False),
    27017: ("MongoDB", False), 11211: ("Memcached", False), 2181: ("ZooKeeper", False),
    9092: ("Kafka", False), 1883: ("MQTT", False), 21: ("FTP", False),
    22: ("SSH / SFTP", False), 23: ("Telnet", False), 25: ("SMTP", False),
    53: ("DNS", False), 110: ("POP3", False), 135: ("RPC", False), 139: ("NetBIOS", False),
    445: ("SMB 文件共享", False), 993: ("IMAPS", False), 995: ("POP3S", False),
    1434: ("SQL Server 浏览", False), 3389: ("远程桌面", False), 5040: ("Windows 服务", False),
    5357: ("WSDAPI", False), 5985: ("WinRM", False), 7680: ("交付优化", False),
}

# 绝对不允许结束的系统进程 (结束会导致蓝屏 / 系统不可用)
PROTECTED_NAMES = {
    "system", "system idle process", "registry", "memory compression",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "lsaiso.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe",
    "ntoskrnl.exe", "secure system", "wudfhost.exe", "sihost.exe",
}
# 这些虽然可杀, 但必须给出额外警告
CAUTION_NAMES = {"explorer.exe", "wininit.exe", "audiodg.exe", "searchindexer.exe"}

NETSTAT_RE = re.compile(
    r"^\s*(TCP|TCPv6|UDP|UDPv6)\s+(\S+)\s+(\S+)\s+(\S+)?\s*(\d+)\s*$", re.I)


# ----------------------------------------------------------------------------
# Windows 原生进程信息 (不依赖 tasklist / WMI, 受限环境也能用)
# ----------------------------------------------------------------------------

class WinProc:
    """用 kernel32 直接取进程路径 / 命令行, 比 tasklist 更可靠更快。"""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_TERMINATE = 0x0001
    PROCESS_VM_READ = 0x0010

    def __init__(self):
        self.ok = IS_WINDOWS
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True) if IS_WINDOWS else None
        self._ntdll = ctypes.WinDLL("ntdll", use_last_error=True) if IS_WINDOWS else None
        self._cache = {}

    # -- 句柄 ---------------------------------------------------------------
    def _open(self, pid, access):
        if not self.ok:
            return None
        h = self._k32.OpenProcess(access, False, int(pid))
        return h or None

    def _close(self, h):
        try:
            self._k32.CloseHandle(h)
        except Exception:
            pass

    # -- 信息 ---------------------------------------------------------------
    def path_of(self, pid, ttl=1.5):
        """返回进程可执行文件完整路径, 失败返回 None (带短缓存, 便于频繁刷新)。"""
        now = time.time()
        hit = self._cache.get(pid)
        if hit and now - hit[1] < ttl:
            return hit[0]
        path = self._path_of_raw(pid)
        self._cache[pid] = (path, now)
        return path

    def _path_of_raw(self, pid):
        h = self._open(pid, self.PROCESS_QUERY_LIMITED_INFORMATION)
        if not h:
            return None
        try:
            size = wt.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if self._k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value or None
            return None
        finally:
            self._close(h)

    def name_of(self, pid):
        p = self.path_of(pid)
        return os.path.basename(p) if p else None

    def cmdline_of(self, pid):
        """通过 NtQueryInformationProcess + PEB 读取命令行, 失败返回 None。"""
        if not self.ok:
            return None
        h = self._open(pid, self.PROCESS_QUERY_LIMITED_INFORMATION | self.PROCESS_VM_READ)
        if not h:
            return None
        try:
            # PROCESS_BASIC_INFORMATION
            class PBI(ctypes.Structure):
                _fields_ = [("Reserved1", ctypes.c_void_p),
                            ("PebBaseAddress", ctypes.c_void_p),
                            ("Reserved2", ctypes.c_void_p * 2),
                            ("UniqueProcessId", ctypes.c_void_p),
                            ("Reserved3", ctypes.c_void_p)]
            pbi = PBI()
            rlen = wt.ULONG(0)
            status = self._ntdll.NtQueryInformationProcess(
                h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(rlen))
            if status != 0 or not pbi.PebBaseAddress:
                return None
            # PEB->ProcessParameters 偏移: x64 = 0x20, x86 = 0x10
            ptr_size = ctypes.sizeof(ctypes.c_void_p)
            pp_off = 0x20 if ptr_size == 8 else 0x10
            pp = ctypes.c_void_p()
            if not self._k32.ReadProcessMemory(h, ctypes.c_void_p(pbi.PebBaseAddress + pp_off),
                                               ctypes.byref(pp), ptr_size, None):
                return None
            if not pp:
                return None
            # RTL_USER_PROCESS_PARAMETERS->CommandLine (UNICODE_STRING): x64 = 0x70, x86 = 0x40
            cl_off = 0x70 if ptr_size == 8 else 0x40
            raw = ctypes.create_string_buffer(ptr_size * 2)
            if not self._k32.ReadProcessMemory(h, ctypes.c_void_p(pp.value + cl_off),
                                               raw, ptr_size * 2, None):
                return None
            length = ctypes.c_ushort.from_buffer(raw, 0).value
            maxlen = ctypes.c_ushort.from_buffer(raw, 2).value
            bufptr = ctypes.c_void_p.from_buffer(raw, ptr_size).value
            if not bufptr or length <= 0 or length > 32768:
                return None
            text = ctypes.create_unicode_buffer(length // 2 + 1)
            if not self._k32.ReadProcessMemory(h, ctypes.c_void_p(bufptr), text,
                                               min(length, maxlen), None):
                return None
            return text.value or None
        except Exception:
            return None
        finally:
            self._close(h)

    def kill(self, pid):
        """返回 (成功?, 提示)。优先原生 API, 失败再退回 taskkill。"""
        pid = int(pid)
        h = self._open(pid, self.PROCESS_TERMINATE)
        if h:
            try:
                if self._k32.TerminateProcess(h, 1):
                    return True, "已结束进程 (PID %d)" % pid
                err = ctypes.get_last_error()
                msg = "TerminateProcess 失败 (错误码 %d)" % err
            finally:
                self._close(h)
        else:
            msg = "无法打开进程 (错误码 %d), 通常是权限不足" % ctypes.get_last_error()

        ok, out = run_hidden(["taskkill", "/F", "/T", "/PID", str(pid)])
        if ok:
            return True, "已结束进程 (PID %d)" % pid
        low = (out or "").lower()
        if "not found" in low or "没有找到" in low or "找不到" in low:
            return True, "进程已不存在 (PID %d)" % pid
        return False, msg + ("\ntaskkill 返回: " + (out or "").strip()[:200])


# ----------------------------------------------------------------------------
# 命令行执行 / 输出解码
# ----------------------------------------------------------------------------

def _decode(raw):
    if not raw:
        return ""
    for enc in ("utf-8", "gbk", "mbcs", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def run_hidden(args, timeout=25):
    """无窗口执行命令, 返回 (returncode == 0, 输出文本)。"""
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           creationflags=CREATE_NO_WINDOW, timeout=timeout)
        return p.returncode == 0, _decode(p.stdout)
    except FileNotFoundError:
        return False, "找不到命令: %s" % args[0]
    except subprocess.TimeoutExpired:
        return False, "命令超时: %s" % " ".join(args)
    except Exception as exc:  # noqa: BLE001
        return False, "%s" % exc


# ----------------------------------------------------------------------------
# 端口扫描
# ----------------------------------------------------------------------------

def split_addr(addr):
    """'0.0.0.0:8080' / '[::]:80' -> (host, port)"""
    addr = addr.strip()
    if addr.startswith("["):
        host, _, port = addr.rpartition("]:")
        return host.lstrip("["), port
    host, _, port = addr.rpartition(":")
    return host, port


def list_ports():
    """获取端口占用记录。优先用 Windows API 直查内核 (约快 10 倍), 失败退回 netstat。"""
    rows = _list_ports_api()
    return rows if rows else _list_ports_netstat()


# TCP 状态值 -> 名称
_TCP_STATES = {1: "CLOSED", 2: "LISTENING", 3: "SYN_SENT", 4: "SYN_RECEIVED",
               5: "ESTABLISHED", 6: "FIN_WAIT_1", 7: "FIN_WAIT_2", 8: "CLOSE_WAIT",
               9: "CLOSING", 10: "LAST_ACK", 11: "TIME_WAIT", 12: "DELETE_TCB"}

_AF_INET, _AF_INET6 = 2, 23
_TCP_TABLE_OWNER_PID_ALL = 5
_UDP_TABLE_OWNER_PID = 1
_NO_ERROR, _ERROR_INSUFFICIENT_BUFFER = 0, 122


def _fmt_ipv4(dw):
    return "%d.%d.%d.%d" % (dw & 0xFF, (dw >> 8) & 0xFF, (dw >> 16) & 0xFF, (dw >> 24) & 0xFF)


def _fmt_ipv6(slots, scope=0):
    """把 MIB 表里的 16 字节 IPv6 地址 (4 个 DWORD 槽) 转成 netstat 同款写法 (不带括号)。"""
    try:
        b = b"".join(int(s).to_bytes(4, "little") for s in slots)
        text = socket.inet_ntop(socket.AF_INET6, b)
    except Exception:
        return "::"
    if text.lower().startswith("::ffff:") and "." not in text:      # IPv4 映射地址
        text = text[7:]
    return "%s%%%d" % (text, scope) if scope else text


def _http(host, port):
    """按 netstat 的写法拼出地址列: IPv6 带方括号 -> [::]:80, IPv4 -> a.b.c.d:80"""
    return "[%s]:%d" % (host, port) if ":" in host else "%s:%d" % (host, port)


def _table(which, family):
    """调用 GetExtendedTcpTable / GetExtendedUdpTable, 返回 (内存, 行数, 行结构, 行大小)。
    端口表随时在变, 两次调用之间可能变大, 所以要重试并做边界钳制。"""
    func = (ctypes.windll.iphlpapi.GetExtendedTcpTable if which == "tcp"
            else ctypes.windll.iphlpapi.GetExtendedUdpTable)
    cls = 5 if which == "tcp" else _UDP_TABLE_OWNER_PID
    for _ in range(4):
        size = wt.DWORD(0)
        ret = func(None, ctypes.byref(size), False, family, cls, 0)
        if size.value == 0:
            return None
        buf = ctypes.create_string_buffer(size.value)
        ret = func(buf, ctypes.byref(size), False, family, cls, 0)
        if ret == _NO_ERROR:
            count = ctypes.c_ulong.from_buffer(buf, 0).value
            stride = _row_stride(which, family)
            max_fit = (len(buf) - ctypes.sizeof(ctypes.c_ulong)) // stride
            count = min(count, max(0, max_fit))     # 防止越界读
            if count <= 0:
                return None
            return buf, count, _row_spec(which, family), stride
        if ret != _ERROR_INSUFFICIENT_BUFFER:
            return None          # 其它错误交给 netstat 兜底
    return None


def _row_spec(which, family):
    """每项 = (类型, 名字, 占几个 DWORD 槽); IPv6 地址占 4 槽, 必须按槽累加偏移。"""
    D = ctypes.c_uint32   # 注意: Windows DWORD 是 4 字节, 不能用 c_ulong (64 位下是 8)
    if which == "tcp":
        if family == _AF_INET:
            return ((D, "state", 1), (D, "local_addr", 1), (D, "local_port", 1),
                    (D, "remote_addr", 1), (D, "remote_port", 1), (D, "pid", 1))
        return ((D, "local_addr", 4), (D, "local_scope", 1), (D, "local_port", 1),
                (D, "remote_addr", 4), (D, "remote_scope", 1), (D, "remote_port", 1),
                (D, "state", 1), (D, "pid", 1))
    if family == _AF_INET:
        return ((D, "local_addr", 1), (D, "local_port", 1), (D, "pid", 1))
    return ((D, "local_addr", 4), (D, "local_scope", 1), (D, "local_port", 1),
            (D, "pid", 1))


def _row_stride(which, family):
    """行字节数 (按实测布局; 缓冲尾部可能多几字节填充, 由 _table 做边界钳制)。"""
    if which == "tcp":
        return 24 if family == _AF_INET else 56     # MIB_TCPROW / MIB_TCP6ROW
    return 12 if family == _AF_INET else 28         # MIB_UDPROW / MIB_UDP6ROW


def _list_ports_api():
    """用 GetExtendedTcpTable/UdpTable 直接读内核表, 省掉一次子进程启动。"""
    if not IS_WINDOWS:
        return []
    rows = []
    try:
        for which in ("tcp", "udp"):
            for fam in (_AF_INET, _AF_INET6):
                got = _table(which, fam)
                if not got:
                    continue
                buf, count, spec, row_size = got
                base = ctypes.sizeof(ctypes.c_ulong)
                for i in range(count):
                    off = base + i * row_size
                    slot = 0
                    vals = {}
                    for ctype, name, nslot in spec:
                        if nslot == 1:
                            vals[name] = ctype.from_buffer(buf, off + slot * 4).value
                        else:   # 多槽: 地址类字段, 按 DWORD 顺序存成整数
                            vals[name] = tuple(
                                ctype.from_buffer(buf, off + (slot + k) * 4).value
                                for k in range(nslot))
                        slot += nslot
                    lp = ((vals["local_port"] & 0xFF) << 8) | ((vals["local_port"] >> 8) & 0xFF)
                    if fam == _AF_INET:
                        lh = _fmt_ipv4(vals["local_addr"])
                    else:
                        lh = _fmt_ipv6(vals["local_addr"], vals["local_scope"])
                    if which == "tcp":
                        rp = ((vals["remote_port"] & 0xFF) << 8) | ((vals["remote_port"] >> 8) & 0xFF)
                        if fam == _AF_INET:
                            rh = _fmt_ipv4(vals["remote_addr"])
                        else:
                            rh = _fmt_ipv6(vals["remote_addr"], vals["remote_scope"])
                        state = _TCP_STATES.get(vals["state"], "?")
                        foreign = _http(rh, rp)
                    else:
                        state = "UDP"
                        foreign = "*:*"     # netstat 对 UDP 就是这个写法
                    rows.append({
                        "proto": "TCP" if which == "tcp" else "UDP",
                        "v6": fam == _AF_INET6,
                        "host": lh,
                        "port": lp,
                        "state": state,
                        "pid": vals["pid"],
                        "foreign": foreign,
                    })
    except Exception:
        return []          # 任何异常都退回 netstat 实现, 保证功能不丢
    return rows


def _list_ports_netstat():
    """netstat -ano 兜底实现。"""
    ok, out = run_hidden(["netstat", "-ano"])
    rows = []
    if not ok or not out:
        return rows
    for line in out.splitlines():
        m = NETSTAT_RE.match(line)
        if not m:
            continue
        proto, local, foreign, state, pid = m.groups()
        proto = proto.upper()
        host, port = split_addr(local)
        if not port.isdigit():
            continue
        if proto.startswith("UDP"):
            state = "UDP"
        else:
            state = (state or "?").upper()
        rows.append({
            "proto": "TCP" if proto.startswith("TCP") else "UDP",
            "v6": proto.endswith("v6"),
            "host": host,
            "port": int(port),
            "state": state,
            "pid": int(pid),
            "foreign": foreign,
        })
    return rows


# 以本机为目标的连接 (访客), 其余视为出站连接
LOCAL_HOSTS = {"0.0.0.0", "127.0.0.1", "::", "::1", "[::]", "[::1]", "*", "[*]"}


def foreign_is_remote(addr):
    host = split_addr(addr)[0]
    return host not in LOCAL_HOSTS and host not in ("", "0.0.0.0", "::")


class PortRow:
    """表格中的一行 = 一个本地端口。"""

    __slots__ = ("port", "protos", "pids", "names", "paths",
                 "listen", "inbound", "outbound", "udp")

    def __init__(self, port):
        self.port = port
        self.protos = set()
        self.pids = []          # 去重后的 pid 列表
        self.names = {}
        self.paths = {}
        self.listen = False
        self.udp = False
        self.inbound = 0
        self.outbound = 0

    @property
    def state(self):
        if self.listen:
            return "LISTENING"
        if self.udp:
            return "UDP"
        return "ESTABLISHED"

    def add_pid(self, pid):
        if pid not in self.pids:
            self.pids.append(pid)


def build_rows(entries, resolve_names=True, resolver=None):
    by_port = {}
    for e in entries:
        row = by_port.get(e["port"])
        if row is None:
            row = by_port[e["port"]] = PortRow(e["port"])
        row.protos.add(e["proto"])
        row.add_pid(e["pid"])
        if e["state"] == "LISTENING":
            row.listen = True
        elif e["proto"] == "UDP":
            row.udp = True
        elif foreign_is_remote(e["foreign"]):
            row.outbound += 1
        else:
            row.inbound += 1

    rows = sorted(by_port.values(), key=lambda r: r.port)
    if resolve_names and resolver:
        pids = sorted({p for r in rows for p in r.pids})
        info = resolver(pids)
        for r in rows:
            for pid in r.pids:
                nm, pth = info.get(pid, (None, None))
                if nm:
                    r.names[pid] = nm
                if pth:
                    r.paths[pid] = pth
    return rows


def service_hint(port):
    return COMMON_PORTS.get(port, (None, False))


def open_target(port, port_entries):
    """决定用浏览器打开哪个地址。"""
    hosts = {e["host"] for e in port_entries if e["port"] == port and e["state"] == "LISTENING"}
    if not hosts:
        hosts = {e["host"] for e in port_entries if e["port"] == port}
    local = {"127.0.0.1", "::1", "[::1]"}
    if hosts & local:
        host = "127.0.0.1"
    else:
        ipv4 = [h for h in hosts if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", h) and h != "0.0.0.0"]
        host = ipv4[0] if ipv4 else "127.0.0.1"
    scheme = "https" if port in (443, 8443) else "http"
    return "%s://%s:%d" % (scheme, host, port)


# ----------------------------------------------------------------------------
# 主界面
# ----------------------------------------------------------------------------

class PortPilotApp:
    def __init__(self, root):
        self.root = root
        self.winproc = WinProc()
        self.entries = []
        self.rows = []
        self.row_by_port = {}
        self.entries_by_port = {}
        self.filtered = []
        self.sort_key = "port"
        self.sort_desc = False
        self.scanning = False
        self._auto_job = None
        self._tip_text = ""

        root.title("%s %s - 端口进程查看器" % (APP_NAME, APP_VER))
        self._fit_window(root)
        self._set_icon()
        self._setup_style()
        self._build_ui()
        self._bind_keys()
        self.refresh(silent=True)

    # -- 外观 ---------------------------------------------------------------
    # 各列: (基础宽度, 最小宽度, 完整显示所需宽度)
    # "所需宽度" 按实测字体量: Consolas 10 下 "LISTENING"=135px、"ESTABLISHED"=165px。
    # 状态列是硬需求 —— 列表里没有它就没意义, 所以它有自己的保底宽度 (见 _reflow_cols)。
    BASE_COLS = {
        "port": (92, 62, 92),
        "count": (100, 60, 90),
        "listen": (190, 118, 192),
        "proto": (84, 54, 84),
        "conns": (130, 76, 120),
        "pid": (100, 62, 94),
        "proc": (350, 140, 300),
        "service": (36, 36, 42),          # 数据列 1.5 汉字, 表头「服务」需 ~40px
        "path": (520, 110, 340),          # 路径允许截断, 完整值在「详情」里看
    }
    # 状态列保底: 必须能完整显示 "ESTABLISHED" (实测 165px + 内边距)
    LISTEN_FLOOR = 180

    def _fit_window(self, root):
        """按屏幕工作区自适应大小并居中; 尽量占满屏幕 (上下都要铺开)。"""
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = max(900, sw - 60)
        h = max(520, sh - 80)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        root.minsize(860, 460)

    @staticmethod
    def _char_width(chars=1.0):
        """1 个汉字在当前 DPI 下的实际像素宽度 × chars (用于锁定列宽)。"""
        try:
            from tkinter import font as tkfont
            f = tkfont.Font(family="Microsoft YaHei UI", size=9, weight="bold")
            return int(round(f.measure("服") * chars))
        except Exception:
            return int(round(150 * chars))

    def _set_icon(self):
        """设置窗口图标。打包成 exe 后资源在 sys._MEIPASS 里, 所以两个路径都找。"""
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            for ico in (os.path.join(here, "assets", "app.ico"),
                        os.path.join(getattr(sys, "_MEIPASS", here), "app.ico"),
                        os.path.join(here, "app.ico")):
                if os.path.exists(ico):
                    self.root.iconbitmap(ico)
                    return
        except Exception:
            pass

    def _setup_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        base = ("Microsoft YaHei UI", 9)
        st.configure(".", font=base)
        st.configure("TButton", padding=(10, 5))
        st.configure("Accent.TButton", padding=(12, 5))
        # 行高/内边距按 1.5 倍放大, 让行与行之间松快些 (字体大小保持不变)
        st.configure("Treeview", font=("Consolas", 10), rowheight=30, borderwidth=1,
                     relief="solid")
        st.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"),
                     padding=(8, 14))
        st.configure("Status.TLabel", padding=(8, 4))
        self._cell_pad = (6, 10)      # 单元格左右/上下内边距 (原为 0)
        self._head_pad = (8, 14)
        self._rowheight = 30

    # -- 界面 ---------------------------------------------------------------
    def _build_ui(self):
        self._pwid = self._char_width(1.0)   # 1 个汉字在当前 DPI 下的像素宽度
        # 第一行: 主要动作 (右对齐的按钮先 pack, 先占住空间, 免得被挤出窗口)
        bar = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        bar.pack(fill=X)

        self.btn_refresh = ttk.Button(bar, text="⟳ 刷新 (F5)", command=self.refresh,
                                      style="Accent.TButton")
        self.btn_open = ttk.Button(bar, text="🌐 浏览器打开", command=self.open_selected)
        self.btn_stop = ttk.Button(bar, text="⛔ 停止服务", command=self.stop_selected)
        self.btn_detail = ttk.Button(bar, text="🔍 详情", command=self.show_details)
        self.btn_admin = ttk.Button(bar, text="🛡 以管理员身份重启", command=self.restart_as_admin)

        self.btn_admin.pack(side=RIGHT)
        self.btn_refresh.pack(side=LEFT)
        self.btn_open.pack(side=LEFT, padx=(6, 0))
        self.btn_stop.pack(side=LEFT, padx=(6, 0))
        self.btn_detail.pack(side=LEFT, padx=(6, 0))

        # 窗口变窄时自动缩短按钮文案, 保证"管理员"按钮不被挤出可视区
        self._narrow = None
        bar.bind("<Configure>", self._on_bar_resize)

        # 第二行: 筛选与搜索
        bar2 = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        bar2.pack(fill=X)

        ttk.Label(bar2, text="搜索:").pack(side=LEFT)
        self.var_search = StringVar()
        ent = ttk.Entry(bar2, textvariable=self.var_search, width=18)
        ent.pack(side=LEFT, padx=(4, 0))
        ent.bind("<KeyRelease>", lambda e: self.apply_filter())
        ent.bind("<Escape>", lambda e: (self.var_search.set(""), self.apply_filter()))

        self.var_listen = BooleanVar(value=True)
        ttk.Checkbutton(bar2, text="只看监听中", variable=self.var_listen,
                        command=self.apply_filter).pack(side=LEFT, padx=(12, 0))
        self.var_tcp = BooleanVar(value=False)
        ttk.Checkbutton(bar2, text="只看 TCP", variable=self.var_tcp,
                        command=self.apply_filter).pack(side=LEFT, padx=(8, 0))
        self.var_auto = BooleanVar(value=False)
        ttk.Checkbutton(bar2, text="自动刷新", variable=self.var_auto,
                        command=self.toggle_auto).pack(side=LEFT, padx=(8, 0))
        ttk.Button(bar2, text="重置", width=6, command=self.reset_auto).pack(side=LEFT, padx=(8, 0))

        # 表格
        wrap = ttk.Frame(self.root)
        wrap.pack(fill=BOTH, expand=True, padx=8, pady=(4, 0))

        cols = ("port", "count", "listen", "proto", "conns", "pid", "proc", "service", "path")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        labels = {"port": "端口", "count": "连接数", "listen": "状态", "proto": "协议",
                  "conns": "方向/连接", "pid": "PID", "proc": "进程名",
                  "service": "服务", "path": "进程路径 / 命令行"}
        for c in cols:
            self.tree.heading(c, text=labels[c], command=lambda cc=c: self.sort_by(cc))
            anchor = "e" if c in ("port", "pid", "count", "conns") else "w"
            self.tree.column(c, width=self.BASE_COLS[c][0], minwidth=self.BASE_COLS[c][1],
                             anchor=anchor, stretch=False)
        # 服务列 = 1.5 个汉字宽度 (按当前 DPI 实测)
        self._service_w = max(36, int(round(self._pwid * 1.5)))

        self.tree.tag_configure("listen", background="#eef7ee")
        self.tree.tag_configure("conn", background="#f7f7f7")
        self.tree.tag_configure("udp", background="#f2f2fb")
        self.tree.tag_configure("warn", background="#fff6e5")
        self.tree.tag_configure("danger", background="#fdecec")

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(wrap, orient=HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        # 响应式列宽: 以"窗口宽度"为唯一依据 (窗口宽度是权威值, 不受表格内容影响,
        # 因此不会形成"列变宽->表格变宽->再重算"的反馈死循环)
        self._avail = 0
        self.root.bind("<Configure>", self._on_win_resize)
        self.root.after(60, lambda: self._on_win_resize(None))

        self.tree.bind("<Control-Button-1>", self.on_ctrl_click)
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Return>", lambda e: self.open_selected())
        self.tree.bind("<Delete>", lambda e: self.stop_selected())
        self.tree.bind("<Button-3>", self.on_right_click)
        # "服务"列被锁成 1.5 个汉字宽度, 鼠标悬停时在提示栏显示完整服务名
        self.tree.bind("<Motion>", self.on_tree_motion)

        # 右键菜单
        self.menu = None
        self._build_menu()

        # 状态栏
        status = ttk.Frame(self.root)
        status.pack(fill=X, side="bottom")
        self.var_status = StringVar(value="就绪")
        ttk.Label(status, textvariable=self.var_status, style="Status.TLabel",
                  anchor="w").pack(side=LEFT, fill=X, expand=True)
        self.var_tip = StringVar(value="提示: Ctrl + 左键 / 双击 = 浏览器打开   |   Delete = 停止服务")
        ttk.Label(status, textvariable=self.var_tip, style="Status.TLabel",
                  foreground="#666").pack(side=RIGHT)

    # -- 响应式列宽 ---------------------------------------------------------
    def _reflow_cols(self, avail):
        """按可用宽度重算列宽。

        宽裕时: 按基础宽度比例放大铺满, 不留空白。
        紧张时: 每一列按"自己完整显示所需宽度"等比缩放, 而不是所有列用同一个
                缩放系数 —— 否则会出现 "LISTENING" 被截成 "LISTENIN" 这种
                明明有空间却显示不全的情况。缩不下的部分交给横向滚动条。
        """
        keys = ("port", "count", "listen", "proto", "conns", "pid",
                "proc", "service", "path")
        base, floor, need = {}, {}, {}
        for c in keys:
            b, f, n = self.BASE_COLS[c]
            base[c], floor[c], need[c] = b, f, n
        # 「服务」列: 数据按 1.5 汉字宽, 但列宽至少 42px 否则表头「服务」显示不全
        svc = max(self._service_w, self.BASE_COLS["service"][2])
        base["service"] = floor["service"] = need["service"] = svc
        base["proc"] = 350
        base["path"] = 520
        total = sum(base.values())

        if avail >= total:
            extra = avail - total
            out = dict((c, base[c] + extra * base[c] // total) for c in keys)
        else:
            nsum = sum(need.values())
            scale = avail / float(nsum)
            out = {}
            for c in keys:
                # 按所需宽度缩放, 但不低于最小宽度、也不超过基础宽度
                w = int(round(need[c] * scale))
                out[c] = max(floor[c], min(base[c], w))
            # 状态列是硬需求: 保底能完整显示 ESTABLISHED
            out["listen"] = max(out["listen"], min(self.LISTEN_FLOOR, base["listen"]))
        for c in keys:
            if abs(self.tree.column(c, "width") - out[c]) >= 2:
                self.tree.column(c, width=out[c])

    def _on_win_resize(self, event):
        """窗口尺寸变化 -> 重算列宽。

        注意: 子控件的 <Configure> 事件会冒泡到 root, 所以必须确认事件来自 root 本身,
        否则 event.width 会是某个小控件 (如滚动条 16px) 的宽度, 把列压扁。
        """
        if event is not None and event.widget is not self.root:
            return
        win_w = self.root.winfo_width() if event is None else event.width
        if win_w <= 1:      # 还没真正布局出来
            return
        avail = win_w - 48       # 减掉左右边距 + 纵向滚动条 + 表格边框
        if abs(avail - self._avail) < 8:
            return
        self._avail = avail
        self._reflow_cols(max(360, avail))

    def _build_menu(self):
        m = Menu(self.root, tearoff=0)
        m.add_command(label="🌐  在浏览器打开", command=self.open_selected)
        m.add_command(label="⛔  停止该服务", command=self.stop_selected)
        m.add_separator()
        m.add_command(label="📋  复制端口", command=lambda: self.copy_field("port"))
        m.add_command(label="📋  复制 PID", command=lambda: self.copy_field("pid"))
        m.add_command(label="📋  复制进程路径", command=lambda: self.copy_field("path"))
        m.add_separator()
        m.add_command(label="🔍  查看详情 / 命令行", command=self.show_details)
        m.add_command(label="♻  刷新", command=self.refresh)
        self.menu = m

    def _on_bar_resize(self, event):
        """窄窗口时把按钮文案缩短, 避免右对齐按钮被挤掉。"""
        narrow = event.width < 1080
        if narrow == self._narrow:
            return
        self._narrow = narrow
        self.btn_refresh.configure(text="⟳ 刷新" if narrow else "⟳ 刷新 (F5)")
        self.btn_open.configure(text="🌐 打开" if narrow else "🌐 浏览器打开")
        self.btn_stop.configure(text="⛔ 停止" if narrow else "⛔ 停止服务")
        self.btn_detail.configure(text="🔍" if narrow else "🔍 详情")
        if not is_admin():
            self.btn_admin.configure(text="🛡 管理员" if narrow else "🛡 以管理员身份重启")

    def _bind_keys(self):
        self.root.bind("<F5>", lambda e: self.refresh())
        self.root.bind("<Control-r>", lambda e: self.refresh())
        self.root.bind("<Control-f>", lambda e: self.root.focus_force())
        self.root.bind("<Control-o>", lambda e: self.open_selected())
        self.root.bind("<Control-k>", lambda e: self.stop_selected())
        self.root.bind("<Escape>", lambda e: self.tree.selection_remove(*self.tree.selection()))

    # -- 扫描 ---------------------------------------------------------------
    def refresh(self, silent=False):
        if self.scanning:
            return
        self.scanning = True
        if not silent:
            self.var_status.set("正在扫描端口占用情况 ...")
            self.root.update_idletasks()
        self._start_ts = time.time()
        try:
            entries = list_ports()
            pids = sorted({e["pid"] for e in entries})
            info = self.resolve_pids(pids)
            rows = build_rows(entries, resolve_names=False)
            for r in rows:
                for pid in r.pids:
                    nm, pth = info.get(pid, (None, None))
                    if nm:
                        r.names[pid] = nm
                    if pth:
                        r.paths[pid] = pth
            self.entries = entries
            self.rows = rows
            self.row_by_port = {r.port: r for r in rows}
            self.entries_by_port = {}
            for e in entries:
                self.entries_by_port.setdefault(e["port"], []).append(e)
            self.apply_filter()
            if not silent:
                self.var_status.set("扫描完成: 共 %d 个端口, %d 条连接, 扫描耗时 %.2f 秒"
                                    % (len(rows), len(entries), time.time() - self._start_ts))
        except Exception as exc:  # noqa: BLE001
            self.var_status.set("扫描失败: %s" % exc)
        finally:
            self.scanning = False

    def resolve_pids(self, pids):
        """解析 pid -> (名称, 路径)。原生 API 优先, tasklist 兜底。"""
        info = {}
        if not pids:
            return info
        unresolved = []
        if self.winproc.ok:
            for pid in pids:
                p = self.winproc.path_of(pid)
                if p:
                    info[pid] = (os.path.basename(p), p)
                else:
                    unresolved.append(pid)
        else:
            unresolved = list(pids)

        if unresolved:
            for pid, val in self.tasklist_map(unresolved).items():
                info.setdefault(pid, val)

        for pid in pids:
            info.setdefault(pid, ("<无法读取, 可能是系统进程>", ""))
        return info

    def tasklist_map(self, pids):
        """tasklist /fo csv 解析 pid -> (名称, 路径); 失败返回空。"""
        result = {}
        ok, out = run_hidden(["tasklist", "/fo", "csv", "/nh"])
        if not ok or not out.strip():
            return result
        want = set(pids)
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith('"'):
                continue
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) < 2:
                continue
            name = parts[0].strip('"')
            pidtxt = parts[1].strip('"').replace(",", "").replace(" ", "")
            if not pidtxt.isdigit():
                continue
            pid = int(pidtxt)
            if pid in want:
                result[pid] = (name, "")
        return result

    # -- 过滤 / 排序 --------------------------------------------------------
    def apply_filter(self):
        only_listen = self.var_listen.get()
        only_tcp = self.var_tcp.get()
        q = self.var_search.get().strip().lower()
        tokens = q.split()

        out = []
        for r in self.rows:
            if only_listen and not r.listen:
                continue
            if only_tcp and r.protos == {"UDP"}:
                continue
            if tokens:
                blob = self._row_blob(r)
                if not all(t in blob for t in tokens):
                    continue
            out.append(r)

        self.filtered = out
        self.render()

    def _row_blob(self, r):
        parts = [str(r.port), r.state]
        parts.extend(sorted(r.protos))
        for pid in r.pids:
            parts.append(str(pid))
            parts.append(r.names.get(pid, ""))
            parts.append(r.paths.get(pid, ""))
        hint = service_hint(r.port)[0]
        if hint:
            parts.append(hint)
        return " ".join(parts).lower()

    def sort_by(self, key):
        if self.sort_key == key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key, self.sort_desc = key, False
        self.render()

    def _sort_val(self, r):
        k = self.sort_key
        if k == "port":
            return r.port
        if k == "count":
            return len(r.pids)
        if k == "listen":
            return r.state
        if k == "proto":
            return ",".join(sorted(r.protos))
        if k == "conns":
            return r.inbound + r.outbound
        if k == "pid":
            return min(r.pids) if r.pids else 0
        if k == "service":
            return (service_hint(r.port)[0] or "") + "zzz"
        return self._row_blob(r)

    @staticmethod
    def pretty_name(pid, name):
        """读不到进程名时给一个更容易理解的说明。"""
        if name and not name.startswith("<无法读取"):
            return name
        if pid == 0:
            return "<系统空闲进程 (PID 0)>"
        if pid == 4:
            return "<Windows 内核 (PID 4), HTTP/SMB 等由它托管>"
        if pid < 1000:
            return "<系统进程 PID %d, 需管理员权限查看>" % pid
        return "<需管理员权限 (PID %d)>" % pid

    def render(self):
        self.tree.delete(*self.tree.get_children())
        data = sorted(self.filtered, key=self._sort_val, reverse=self.sort_desc)
        for r in data:
            names = ", ".join(dict.fromkeys(
                self.pretty_name(p, r.names.get(p, "?")) for p in r.pids)) or "?"
            pids = ",".join(str(p) for p in r.pids)
            conns = []
            if r.listen:
                conns.append("监听")
            if r.inbound:
                conns.append("入站 %d" % r.inbound)
            if r.outbound:
                conns.append("出站 %d" % r.outbound)
            if r.udp and not r.listen:
                conns.insert(0, "UDP")
            hint = service_hint(r.port)[0] or ""
            path = ""
            if r.paths:
                path = next(iter(r.paths.values()))
            elif names not in ("?", ""):
                path = "(路径不可读, 可能权限不足)"

            raw = ", ".join(r.names.get(p, "") for p in r.pids).lower()
            tag = "listen" if r.listen else ("udp" if r.udp else "conn")
            if any(n in PROTECTED_NAMES for n in raw.split(", ")):
                tag = "warn"
            if r.port in (135, 139, 445, 3389) and r.pids == [4]:
                tag = "danger"

            self.tree.insert("", END, iid=str(r.port), tags=(tag,), values=(
                r.port, len(r.pids), r.state, ",".join(sorted(r.protos)),
                " ".join(conns), pids, names, hint, path))

        total = len(self.rows)
        shown = len(data)
        listen = sum(1 for r in self.rows if r.listen)
        status = ("端口 %d 个 (监听中 %d) | 当前显示 %d 行 | 最后刷新 %s"
                  % (total, listen, shown, time.strftime("%H:%M:%S")))
        blocked = sum(1 for r in self.rows
                      if any(str(n).startswith("<无法读取") or str(n).startswith("<需")
                             or str(n).startswith("<系统") for n in r.names.values()))
        if self.rows and blocked > len(self.rows) * 0.5:
            status += "  |  ⚠ %d 个端口读不到进程信息, 建议点右上角以管理员身份重启" % blocked
        self.var_status.set(status)

    # -- 选择 ---------------------------------------------------------------
    def selected_ports(self):
        return [int(i) for i in self.tree.selection()]

    def selected_rows(self):
        return [self.row_by_port[p] for p in self.selected_ports() if p in self.row_by_port]

    def on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.tree.focus(iid)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def on_tree_motion(self, event):
        """悬停时在提示栏显示完整信息 (服务列很窄, 靠这个看全名)。"""
        iid = self.tree.identify_row(event.y)
        text = "提示: Ctrl + 左键 / 双击 = 浏览器打开   |   Delete = 停止服务"
        if iid:
            r = self.row_by_port.get(int(iid)) if iid.isdigit() else None
            if r is not None:
                hint = service_hint(r.port)[0]
                names = ", ".join(dict.fromkeys(
                    self.pretty_name(p, r.names.get(p, "?")) for p in r.pids))
                text = "端口 %d | %s | 服务: %s | 进程: %s" % (
                    r.port, r.state, hint or "未识别", names)
        if text != self._tip_text:
            self._tip_text = text
            self.var_tip.set(text)

    def on_ctrl_click(self, event):
        """Ctrl + 左键: 浏览器打开。"""
        iid = self.tree.identify_row(event.y)
        if not iid:
            return "break"
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.open_port(int(iid))
        return "break"

    def on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        # 双击列标题分隔线不做处理
        if self.tree.identify_region(event.x, event.y) == "separator":
            return
        self.open_port(int(iid))

    def open_selected(self):
        ports = self.selected_ports()
        if not ports:
            messagebox.showinfo(APP_NAME, "请先在列表里选中一行 (Ctrl+左键或双击可直接打开)")
            return
        self.open_port(ports[0])

    def open_port(self, port):
        url = open_target(port, self.entries)
        try:
            ok = webbrowser.open(url, new=2)
        except Exception as exc:  # noqa: BLE001
            ok = False
            messagebox.showerror(APP_NAME, "打开浏览器失败: %s" % exc)
            return
        hint = service_hint(port)[0]
        self.var_status.set("已用默认浏览器打开 %s%s"
                            % (url, ("  (%s)" % hint) if hint else ""))
        if not ok:
            self.var_status.set("已尝试打开 %s (如果没反应, 该端口可能不是网页服务)" % url)

    def copy_field(self, field):
        rows = self.selected_rows()
        if not rows:
            return
        if field == "port":
            text = "\n".join(str(r.port) for r in rows)
        elif field == "pid":
            text = "\n".join(",".join(str(p) for p in r.pids) for r in rows)
        else:
            text = "\n".join(next(iter(r.paths.values()), "") for r in rows)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.var_status.set("已复制到剪贴板")

    # -- 停止服务 -----------------------------------------------------------
    def stop_selected(self):
        rows = self.selected_rows()
        if not rows:
            messagebox.showinfo(APP_NAME, "请先选中要停止的服务 (那一行端口)")
            return
        # 收集要结束的进程
        targets = []   # (pid, name, path, ports)
        seen = {}
        for r in rows:
            for pid in r.pids:
                if pid in seen:
                    seen[pid][3].append(r.port)
                else:
                    item = [pid, r.names.get(pid, "?"), r.paths.get(pid, ""), [r.port]]
                    seen[pid] = item
                    targets.append(item)

        blockers = []
        for pid, name, path, ports in targets:
            reason = self._kill_block_reason(pid, name)
            if reason:
                blockers.append((pid, name, reason))
        if blockers:
            msg = "以下进程受保护, 不允许结束:\n\n" + "\n".join(
                "  PID %d  %s\n      -> %s" % (p, n, why) for p, n, why in blockers)
            messagebox.showwarning(APP_NAME + " - 已阻止", msg)
            targets = [t for t in targets
                       if not self._kill_block_reason(t[0], t[1])]
            if not targets:
                return

        lines = []
        for pid, name, path, ports in targets:
            lines.append("PID %-7d %-28s 端口 %s" % (pid, name, ",".join(map(str, ports))))
            if path:
                lines.append("        " + path)
        warn = ""
        if any(n.lower() in CAUTION_NAMES for _, n, _, _ in targets):
            warn = ("\n\n⚠ 注意: 其中包含系统外壳进程 (explorer 等), "
                    "结束会导致桌面/任务栏重启。")
        text = ("确定要强制结束以下进程吗?\n\n%s%s\n\n"
                "进程会被立即终止, 未保存的数据可能丢失。" % ("\n".join(lines), warn))
        if not messagebox.askyesno(APP_NAME + " - 停止服务", text, icon="warning"):
            return

        results = []
        for pid, name, path, ports in targets:
            ok, msg = self.winproc.kill(pid)
            if not ok and "权限" in msg:
                ok2, out2 = run_hidden(["taskkill", "/F", "/T", "/PID", str(pid)])
                if ok2:
                    ok, msg = True, "已结束进程 (PID %d, taskkill)" % pid
                else:
                    msg += "\n可能需要以管理员身份运行 (点右上角『以管理员身份重启』)"
            results.append((pid, name, ok, msg))

        failed = [r for r in results if not r[2]]
        detail = "\n".join("  %s PID %d %s: %s" % ("[成功]" if ok else "[失败]",
                                                  pid, name, msg)
                           for pid, name, ok, msg in results)
        self.var_status.set("已结束 %d 个进程, %d 个失败"
                            % (len(results) - len(failed), len(failed)))
        if failed:
            messagebox.showerror(APP_NAME + " - 部分失败", detail)
        time.sleep(0.4)
        self.refresh(silent=True)

    def _kill_block_reason(self, pid, name):
        """返回阻止结束该进程的原因; None 表示允许结束。"""
        if pid in (0, 4):
            return "系统内核进程 (PID %d), 结束会蓝屏" % pid
        if pid == os.getpid():
            return "这是本工具自身的进程"
        low = (name or "").lower()
        if low in PROTECTED_NAMES:
            return "Windows 关键系统进程, 结束后系统会崩溃或注销"
        if not name or name == "?" or re.fullmatch(r"pid\s*\d+", low):
            return "无法确认进程身份, 为安全起见已阻止"
        if name.startswith("<"):
            return "读不到进程名 (权限不足), 请用『以管理员身份重启』后再试"
        return None

    def show_details(self):
        rows = self.selected_rows()
        if not rows:
            messagebox.showinfo(APP_NAME, "请先选中一行")
            return
        r = rows[0]
        parts = ["端口: %d" % r.port,
                 "状态: %s" % r.state,
                 "协议: %s" % ",".join(sorted(r.protos)),
                 "连接: 入站 %d / 出站 %d" % (r.inbound, r.outbound)]
        hint = service_hint(r.port)
        if hint[0]:
            parts.append("识别: %s" % hint[0])
        parts.append("浏览器地址: %s" % open_target(r.port, self.entries))
        for pid in r.pids:
            nm = self.pretty_name(pid, r.names.get(pid, "?"))
            parts.append("")
            parts.append("PID %d  %s" % (pid, nm))
            parts.append("  路径: %s" % (r.paths.get(pid) or "(不可读, 试试管理员身份)"))
            cmd = self.winproc.cmdline_of(pid)
            parts.append("  命令行: %s" % (cmd or "(不可读)"))
        conns = [e for e in self.entries_by_port.get(r.port, [])][:20]
        parts.append("")
        parts.append("连接明细 (最多 20 条):")
        for e in conns:
            parts.append("  %-4s %s:%d -> %s  %s  pid=%d"
                         % (e["proto"], e["host"], e["port"], e["foreign"],
                            e["state"], e["pid"]))
        text = "\n".join(parts)
        self._show_text_window("端口 %d 详情" % r.port, text)

    def _show_text_window(self, title, text):
        win = Tk.Toplevel(self.root)
        win.title(title)
        win.geometry("860x520")
        frame = ttk.Frame(win, padding=8)
        frame.pack(fill=BOTH, expand=True)
        txt = Text(frame, wrap="none", font=("Consolas", 10))
        vs = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vs.set)
        txt.pack(side=LEFT, fill=BOTH, expand=True)
        vs.pack(side=RIGHT, fill=Y)
        txt.insert("1.0", text)
        txt.configure(state="disabled")
        btns = ttk.Frame(win, padding=(8, 0, 8, 8))
        btns.pack(fill=X)
        ttk.Button(btns, text="复制全部",
                   command=lambda: (self.root.clipboard_clear(),
                                    self.root.clipboard_append(text),
                                    self.var_status.set("详情已复制"))).pack(side=LEFT)
        ttk.Button(btns, text="关闭", command=win.destroy).pack(side=RIGHT)

    # -- 自动刷新 -----------------------------------------------------------
    def toggle_auto(self):
        if self.var_auto.get():
            self.schedule_auto()
        else:
            self.cancel_auto()

    def schedule_auto(self):
        self.cancel_auto()
        self._auto_job = self.root.after(5000, self._auto_tick)

    def _auto_tick(self):
        self._auto_job = None
        if not self.var_auto.get():
            return
        self.refresh(silent=True)
        self.schedule_auto()

    def cancel_auto(self):
        if self._auto_job:
            try:
                self.root.after_cancel(self._auto_job)
            except Exception:
                pass
            self._auto_job = None

    def reset_auto(self):
        self.cancel_auto()
        self.var_auto.set(False)
        self.var_listen.set(True)
        self.var_tcp.set(False)
        self.var_search.set("")
        self.apply_filter()
        self.var_status.set("已重置筛选与自动刷新")

    # -- 提权 ---------------------------------------------------------------
    def restart_as_admin(self):
        if is_admin():
            messagebox.showinfo(APP_NAME, "当前已经是管理员权限运行")
            return
        try:
            params = '"%s"' % os.path.abspath(__file__)
            exe = sys.executable
            if exe.lower().endswith("python.exe"):
                cand = exe[:-len("python.exe")] + "pythonw.exe"
                if os.path.exists(cand):
                    exe = cand
            ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params,
                                                os.path.dirname(os.path.abspath(__file__)), 1)
            self.root.after(300, self.root.destroy)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, "提权失败: %s" % exc)


def enable_dpi_awareness():
    """开启 DPI 感知, 否则高分屏下窗口尺寸/坐标会被系统缩放, 导致排版错位。"""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def is_admin():
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ----------------------------------------------------------------------------
def main():
    if not IS_WINDOWS:
        print("本工具仅支持 Windows")
        return 1
    enable_dpi_awareness()
    root = Tk()
    app = PortPilotApp(root)
    if is_admin():
        app.btn_admin.configure(text="🛡 已是管理员权限", state="disabled")
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
