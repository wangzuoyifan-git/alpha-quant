#!/usr/bin/env python3
"""
WSL 磁盘空间 + VHDX 大小看板（只读）

展示：
  - WSL 根文件系统（/）与各 Windows 盘（/mnt/*）的容量 / 已用 / 可用 / 使用率
  - WSL 发行版 ext4.vhdx 的真实路径、文件大小、所在宿主盘及该盘可用空间
    （这才是"真实可增长上限"——WSL 内 df 显示的虚拟盘上限会严重高估）
  - 项目 data/ 各域占用

只做 statvfs / stat / du / 注册表查询，**不写入、不压缩、不修改任何数据**。

用法：
  venv/bin/python3 tools/wsl_space.py
  venv/bin/python3 tools/wsl_space.py --json
  venv/bin/python3 tools/wsl_space.py --no-data          # 跳过 data/ 扫描（更快）
  WSL_VHDX_PATH=/mnt/e/wsl/Ubuntu-24.04/ext4.vhdx venv/bin/python3 tools/wsl_space.py
"""
import argparse
import contextlib
import glob
import json
import os
import re
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOW_FREE = 10 * 1024 ** 3          # 可用 < 10G 视为告警
GIB = 1024 ** 3


def _human(n) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}T"


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _entry(path: str) -> dict:
    st = os.statvfs(path)
    fr = st.f_frsize or st.f_bsize
    size = st.f_blocks * fr
    avail = st.f_bavail * fr
    used = size - st.f_bfree * fr
    pct = (used / size * 100) if size else 0.0
    return {"path": path, "size": size, "used": used, "avail": avail, "pct": pct}


def collect_mounts() -> list:
    mounts = []
    for p in ["/"]:
        with contextlib.suppress(OSError):
            mounts.append(_entry(p))
    for p in sorted(glob.glob("/mnt/[a-z]")):
        if os.path.ismount(p) or os.path.isdir(p):
            with contextlib.suppress(OSError):
                mounts.append(_entry(p))
    return mounts


def _reg_base_paths() -> list:
    exe = shutil.which("reg.exe") or "/mnt/c/Windows/System32/reg.exe"
    if not os.path.exists(exe):
        return []
    try:
        out = subprocess.run(
            [exe, "query",
             r"HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss",
             "/s", "/v", "BasePath"],
            capture_output=True, timeout=15).stdout
    except Exception:
        return []
    text = out.decode("utf-8", "replace")
    return [m.strip() for m in re.findall(r"BasePath\s+REG_SZ\s+(.+)", text)]


def _win_to_mnt(win_path: str):
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", win_path.strip())
    if not m:
        return None
    return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/").rstrip("/")


def find_vhdxes() -> list:
    cands = []
    env = os.environ.get("WSL_VHDX_PATH")
    if env:
        cands.append(env)
    for bp in _reg_base_paths():
        mnt = _win_to_mnt(bp)
        if mnt:
            cands.append(os.path.join(mnt, "ext4.vhdx"))
    for pat in ("/mnt/*/wsl/*/ext4.vhdx", "/mnt/*/WSL/*/ext4.vhdx",
                "/mnt/c/Users/*/AppData/Local/Packages/*/LocalState/ext4.vhdx",
                "/mnt/c/Users/*/AppData/Local/wsl/*/*.vhdx"):
        cands += glob.glob(pat)
    seen, out = set(), []
    for c in cands:
        c = os.path.normpath(c)
        # /mnt/* 为 drvfs，大小写不敏感；按小写去重，避免 wsl/WSL 重复命中
        key = c.lower() if c.startswith("/mnt/") else c
        if key not in seen and os.path.isfile(c):
            seen.add(key)
            out.append(c)
    return out


def vhdx_info(path: str) -> dict:
    st = os.stat(path)
    host = "/mnt/" + path.split("/")[2] if path.startswith("/mnt/") else None
    host_e = None
    if host and os.path.isdir(host):
        try:
            host_e = _entry(host)
        except OSError:
            host_e = None
    return {
        "path": path,
        "size": st.st_size,
        "allocated": st.st_blocks * 512,
        "sparse": st.st_blocks * 512 < st.st_size,
        "host_mount": host,
        "host_free": host_e["avail"] if host_e else None,
        "host_size": host_e["size"] if host_e else None,
    }


def _du(path: str) -> int:
    try:
        out = subprocess.run(["du", "-sb", path], capture_output=True,
                             text=True, timeout=60).stdout
        return int(out.split()[0])
    except Exception:
        return 0


def project_data() -> dict:
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return {"total": 0, "items": []}
    items = []
    total = 0
    for d in os.scandir(data_dir):
        if not d.is_dir() or d.name == "__pycache__":
            continue
        sz = _du(d.path)
        total += sz
        items.append({"name": d.name, "size": sz})
    items.sort(key=lambda x: -x["size"])
    return {"total": total, "items": items}


def collect(skip_data: bool = False) -> dict:
    info = {
        "is_wsl": is_wsl(),
        "distro": os.environ.get("WSL_DISTRO_NAME", ""),
        "mounts": collect_mounts(),
        "vhdxes": [],
        "project_data": None,
    }
    if info["is_wsl"]:
        info["vhdxes"] = [vhdx_info(p) for p in find_vhdxes()]
    if not skip_data:
        info["project_data"] = project_data()
    return info


def render_text(info: dict):
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
    except ImportError:
        return _render_plain(info)

    console = Console()

    title = (f"WSL 磁盘空间看板  ·  发行版 {info['distro'] or '(非 WSL)'}"
             if info["is_wsl"] else "磁盘空间看板（非 WSL 环境）")
    console.print(Panel.fit(title, border_style="cyan"))

    t = Table(title="文件系统", border_style="blue", expand=False)
    for c in ("挂载点", "容量", "已用", "可用", "使用率"):
        t.add_column(c, justify="right" if c != "挂载点" else "left")
    for m in info["mounts"]:
        low = m["avail"] < LOW_FREE
        style = "red" if low else ("yellow" if m["pct"] >= 85 else "green")
        t.add_row(m["path"], _human(m["size"]), _human(m["used"]),
                  f"[{style}]{_human(m['avail'])}[/{style}]",
                  f"[{style}]{m['pct']:.0f}%[/{style}]")
    console.print(t)

    if info["is_wsl"]:
        if info["vhdxes"]:
            vt = Table(title="WSL VHDX（宿主真实占用与可增长上限）",
                       border_style="magenta", expand=False)
            for c in ("VHDX 路径", "文件大小", "宿主盘", "宿主可用", "稀疏"):
                vt.add_column(c)
            for v in info["vhdxes"]:
                free = v["host_free"]
                style = "red" if (free is not None and free < LOW_FREE) else "green"
                vt.add_row(v["path"].replace("/mnt/", ""), _human(v["size"]),
                           v["host_mount"] or "-",
                           f"[{style}]{_human(free)}[/{style}]",
                           "是" if v["sparse"] else "否")
            console.print(vt)
            console.print("[dim]注：WSL 内 df 的可用量是 ext4 虚拟盘上限；真实可增长受『宿主可用』约束，"
                          "VHDX 只增不减，需 fstrim+压缩才会回收。[/dim]")
        else:
            console.print("[yellow]未找到 ext4.vhdx（可设 WSL_VHDX_PATH 指定）[/yellow]")

    pd = info["project_data"]
    if pd is not None:
        dt = Table(title=f"项目 data/ 占用（合计 {_human(pd['total'])}）",
                   border_style="green", expand=False)
        for c in ("域", "大小", "占比"):
            dt.add_column(c, justify="right" if c != "域" else "left")
        for it in pd["items"]:
            pct = (it["size"] / pd["total"] * 100) if pd["total"] else 0
            dt.add_row(it["name"], _human(it["size"]), f"{pct:.1f}%")
        console.print(dt)


def _render_plain(info: dict):
    print(f"WSL={info['is_wsl']} distro={info['distro']}")
    print("文件系统:")
    for m in info["mounts"]:
        print(f"  {m['path']:8s} size={_human(m['size'])} used={_human(m['used'])} "
              f"avail={_human(m['avail'])} ({m['pct']:.0f}%)")
    for v in info["vhdxes"]:
        print(f"VHDX {v['path']} size={_human(v['size'])} host={v['host_mount']} "
              f"host_free={_human(v['host_free'])} sparse={v['sparse']}")
    if info["project_data"]:
        print(f"data/ 合计 {_human(info['project_data']['total'])}")
        for it in info["project_data"]["items"]:
            print(f"  {it['name']:16s} {_human(it['size'])}")


def main():
    ap = argparse.ArgumentParser(description="WSL 磁盘空间 + VHDX 大小看板（只读）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--no-data", action="store_true", help="跳过 data/ 目录扫描")
    args = ap.parse_args()

    info = collect(skip_data=args.no_data)
    if args.json:
        json.dump(info, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        render_text(info)


if __name__ == "__main__":
    main()
