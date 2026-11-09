#!/usr/bin/env python3
"""探活 TVBox 配置里的点播源，输出体检报告。

仅供个人维护自己的订阅源使用。不依赖任何第三方包，GitHub Actions 自带 python3 即可运行。

用法:
    python3 scripts/check.py tvbox.json tvbox-legacy.json
"""
import concurrent.futures
import json
import os
import sys
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36"
KEYWORD = urllib.parse.quote("流浪地球")
TIMEOUT = 9


def probe(site):
    api = site.get("api", "")
    if not api.startswith(("http://", "https://")):
        return (site.get("name", "?"), api, 0, 0, "地址非法", False)
    target = api.rstrip("/") + "?ac=videolist&wd=" + KEYWORD
    started = time.time()
    try:
        req = urllib.request.Request(target, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read(300000).decode("utf-8", "ignore")
            elapsed = time.time() - started
            count = body.count('"vod_id"')
            ok = '"list"' in body and count > 0
            note = "正常" if ok else "返回内容不是影片列表"
            if ok and '"vod_play_url"' not in body:
                note = "能搜索但缺播放地址"
            return (site.get("name", "?"), api, resp.status, round(elapsed, 2), note, ok)
    except Exception as exc:
        return (site.get("name", "?"), api, 0, TIMEOUT, type(exc).__name__, False)


def check_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    sites = data.get("sites", [])
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(probe, sites))
    return rows


def main():
    files = sys.argv[1:] or ["tvbox.json"]
    lines = ["# 点播源体检报告", ""]
    total_bad = 0

    for path in files:
        if not os.path.exists(path):
            lines.append(f"## {path}\n\n文件不存在，跳过。\n")
            continue
        rows = check_file(path)
        good = [r for r in rows if r[5]]
        bad = [r for r in rows if not r[5]]
        total_bad += len(bad)
        lines.append(f"## {path} — 可用 {len(good)} / {len(rows)}")
        lines.append("")
        lines.append("| 状态 | 名称 | 耗时 | 结果数 | 说明 | 地址 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for name, api, code, dt, note, ok in sorted(rows, key=lambda x: (not x[5], x[3])):
            lines.append(
                f"| {'可用' if ok else '失效'} | {name} | {dt}s | "
                f"{code if code else '-'} | {note} | `{api}` |"
            )
        lines.append("")
        if bad:
            lines.append("**建议**：把上表中「失效」的站点从 `sites` 数组里删掉，或换成新的采集接口。")
            lines.append("")

    if total_bad:
        lines.append(f"> 共 {total_bad} 个源不可用。源失效是常态，删掉即可，不影响其余源。")
    else:
        lines.append("> 全部源正常。")

    report = "\n".join(lines)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")


if __name__ == "__main__":
    main()
