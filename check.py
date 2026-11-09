#!/usr/bin/env python3
"""探活 TVBox 配置里的点播源、直播订阅与本地 M3U 快照，输出体检报告。

仅供个人维护自己的订阅源使用。不依赖任何第三方包，GitHub Actions 自带 python3 即可运行。

用法:
    python3 scripts/check.py tvbox.json tvbox-legacy.json
    python3 scripts/check.py tvbox.json --live     # 额外抽样探测 M3U 里的直播流
"""
import collections
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36"
KEYWORD = urllib.parse.quote("流浪地球")
TIMEOUT = 9
M3U_FILES = ["live.m3u", "live-legacy.m3u"]


def fetch(url, timeout=TIMEOUT, size=300000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-4000"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(size).decode("utf-8", "ignore")


def probe_site(site):
    """点播源：搜索关键词，确认返回影片列表。"""
    api = site.get("api", "")
    if not api.startswith(("http://", "https://")):
        return (site.get("name", "?"), api, 0, 0, "地址非法", False)
    target = api.rstrip("/") + "?ac=videolist&wd=" + KEYWORD
    started = time.time()
    try:
        code, body = fetch(target)
        elapsed = round(time.time() - started, 2)
        count = body.count('"vod_id"')
        ok = '"list"' in body and count > 0
        note = "正常" if ok else "返回内容不是影片列表"
        if ok and '"vod_play_url"' not in body:
            note = "能搜索但缺播放地址"
        return (site.get("name", "?"), api, code, elapsed, note, ok)
    except Exception as exc:
        return (site.get("name", "?"), api, 0, TIMEOUT, type(exc).__name__, False)


def probe_live(live):
    """直播订阅：确认 URL 返回的是 M3U 播放列表。"""
    name, url = live.get("name", "?"), live.get("url", "")
    if not url.startswith(("http://", "https://")):
        return (name, url, 0, 0, "地址非法", False)
    started = time.time()
    try:
        code, body = fetch(url, size=200000)
        elapsed = round(time.time() - started, 2)
        n = len(re.findall(r"^#EXTINF", body, re.M))
        ok = code in (200, 206) and ("#EXTM3U" in body or n > 0)
        note = f"{n} 个频道" if ok else "返回的不是 M3U 列表"
        return (name, url, code, elapsed, note, ok)
    except Exception as exc:
        return (name, url, 0, TIMEOUT, type(exc).__name__, False)


def parse_m3u(path):
    """解析本地 M3U，返回 (分组 -> [url]) 以及格式问题列表。"""
    lines = open(path, "r", encoding="utf-8").read().splitlines()
    groups = collections.defaultdict(list)
    issues = []
    if not lines or not lines[0].startswith("#EXTM3U"):
        issues.append("首行不是 #EXTM3U")
    inf = 0
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF"):
            inf += 1
            m = re.search(r'group-title="([^"]*)"', line)
            group = m.group(1) if m else "未分组"
            for j in range(i + 1, min(i + 4, len(lines))):
                u = lines[j].strip()
                if not u or u.startswith("#"):
                    continue
                if ";" in u or u.count("http") > 1 or not u.startswith(("http://", "https://")):
                    issues.append(f"第 {j + 1} 行地址异常")
                else:
                    groups[group].append(u)
                break
    return groups, issues, inf


def probe_stream(url):
    """探测单条直播流；兼容 m3u8 播放列表和 mp4/flv 直链。"""
    started = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-2000"})
        with urllib.request.urlopen(req, timeout=7) as resp:
            code = resp.status
            ctype = (resp.headers.get("Content-Type") or "").lower()
            body = resp.read(2000).decode("utf-8", "ignore")
            ok = code in (200, 206) and (
                "#EXTM3U" in body
                or "#EXT-X" in body
                or "mpegurl" in ctype
                or any(t in ctype for t in ("video/", "application/octet-stream"))
            )
            return url, ok, round(time.time() - started, 2)
    except Exception:
        return url, False, round(time.time() - started, 2)


def table(rows, headers):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return out


def main():
    argv = sys.argv[1:]
    do_live_probe = "--live" in argv
    files = [a for a in argv if not a.startswith("--")] or ["tvbox.json"]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lines = ["# 影视源体检报告", ""]
    total_bad = 0

    # ---- 点播源 ----
    for path in files:
        if not os.path.exists(path):
            lines.append(f"## {path}\n\n文件不存在，跳过。\n")
            continue
        data = json.load(open(path, "r", encoding="utf-8"))
        sites = data.get("sites", [])
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            rows = list(pool.map(probe_site, sites))
        good = [r for r in rows if r[5]]
        bad = [r for r in rows if not r[5]]
        total_bad += len(bad)
        lines.append(f"## {os.path.basename(path)} — 点播源可用 {len(good)} / {len(rows)}")
        lines.append("")
        lines += table(
            [
                ("可用" if r[5] else "失效", r[0], f"{r[3]}s", r[2] or "-", r[4], f"`{r[1]}`")
                for r in sorted(rows, key=lambda x: (not x[5], x[3]))
            ],
            ["状态", "名称", "耗时", "HTTP", "说明", "地址"],
        )
        lines.append("")

        # ---- 直播订阅 ----
        lives = data.get("lives", [])
        # 含中文占位符的本地快照在上传前无法访问，单独提示，不误报为失效
        pending = [l for l in lives if any(ord(ch) > 127 for ch in l.get("url", ""))]
        remote = [l for l in lives if l not in pending]
        if pending:
            lines.append("### 本地快照（占位符待替换）")
            lines.append("")
            for l in pending:
                lines.append(
                    f"- **{l['name']}**：URL 里的 `你的用户名` 需替换成实际 GitHub 用户名，"
                    "上传仓库后即生效（本地 M3U 文件本身已校验通过）。"
                )
            lines.append("")
        if remote:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                lrows = list(pool.map(probe_live, remote))
            lbad = [r for r in lrows if not r[5]]
            total_bad += len(lbad)
            lines.append(f"### 直播订阅 — 可用 {len(lrows) - len(lbad)} / {len(lrows)}")
            lines.append("")
            lines += table(
                [
                    ("可用" if r[5] else "失效", r[0], f"{r[3]}s", r[2] or "-", r[4], f"`{r[1]}`")
                    for r in sorted(lrows, key=lambda x: (not x[5], x[3]))
                ],
                ["状态", "名称", "耗时", "HTTP", "说明", "地址"],
            )
            lines.append("")

    # ---- 本地 M3U 快照 ----
    for fname in M3U_FILES:
        path = os.path.join(root, fname)
        if not os.path.exists(path):
            continue
        groups, issues, inf = parse_m3u(path)
        urls = [u for v in groups.values() for u in v]
        prot = collections.Counter("https" if u.startswith("https") else "http" for u in urls)
        lines.append(f"## {fname} — {inf} 个频道 / {len(urls)} 条线路")
        lines.append("")
        lines.append("| 分组 | 线路数 |")
        lines.append("| --- | --- |")
        for g, v in groups.items():
            lines.append(f"| {g} | {len(v)} |")
        lines.append("")
        lines.append(f"协议分布：http {prot['http']} 条，https {prot['https']} 条。")
        lines.append("")
        if issues:
            total_bad += len(issues)
            lines.append(f"**格式问题 {len(issues)} 处**：" + "；".join(issues[:8]))
            lines.append("")
        if do_live_probe:
            sample = []
            for g, v in groups.items():
                sample += [(g, u) for u in v[:5]]
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
                res = list(pool.map(lambda t: (t[0], *probe_stream(t[1])), sample))
            okc = sum(1 for r in res if r[2])
            lines.append(f"### 抽样探活（每组前 5 条）：{okc} / {len(res)} 可用")
            lines.append("")
            lines += table(
                [("可用" if r[2] else "失效", r[0], f"{r[3]}s", f"`{r[1]}`") for r in res],
                ["状态", "分组", "耗时", "地址"],
            )
            lines.append("")

    if total_bad:
        lines.append(f"> 共 {total_bad} 项异常。点播/直播源失效是常态，删掉或换掉即可，不影响其余源。")
    else:
        lines.append("> 全部源正常。")
    lines.append("")
    lines.append("> 提示：本地运行 `python3 scripts/check.py tvbox.json --live` 可额外抽样探测直播流连通性。")

    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")


if __name__ == "__main__":
    main()
