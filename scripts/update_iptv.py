#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import aiohttp
import re
import yaml
import time
from pathlib import Path
from collections import defaultdict
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"
SOURCES_FILE = CONFIG_DIR / "sources.txt"
RULES_FILE = CONFIG_DIR / "rules.yaml"

def load_rules():
    with open(RULES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rules = []
    for r in data.get("rules", []):
        rules.append({
            "pattern": re.compile(r["filter"]),
            "rename": r.get("rename"),
            "group": r.get("group", "未分类")
        })
    settings = data.get("settings", {})
    return rules, settings

def parse_m3u(content: str):
    channels = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            name = line.split(",")[-1].strip() if "," in line else "Unknown"
            # 简单提取 group-title
            group_match = re.search(r'group-title="([^"]*)"', line)
            group = group_match.group(1) if group_match else ""
            i += 1
            if i < len(lines):
                url = lines[i].strip()
                if url and not url.startswith("#"):
                    channels.append({"name": name, "url": url, "origin_group": group})
        elif "," in line and not line.startswith("#"):
            # txt 格式: 名称,url
            parts = line.split(",", 1)
            if len(parts) == 2 and parts[1].startswith("http"):
                channels.append({"name": parts[0].strip(), "url": parts[1].strip(), "origin_group": ""})
        i += 1
    return channels

def apply_rules(channels, rules):
    result = []
    for ch in channels:
        name = ch["name"]
        matched = False
        for rule in rules:
            if rule["pattern"].search(name):
                new_name = rule["rename"] if rule["rename"] else name
                group = rule["group"]
                result.append({
                    "name": new_name,
                    "url": ch["url"],
                    "group": group,
                    "origin_name": name
                })
                matched = True
                break
        if not matched:
            result.append({
                "name": name,
                "url": ch["url"],
                "group": ch.get("origin_group") or "未分类",
                "origin_name": name
            })
    return result

async def test_url(session, url, timeout, ua):
    start = time.perf_counter()
    try:
        headers = {"User-Agent": ua}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as resp:
            if resp.status >= 400:
                return None
            # 读一点数据估算速度
            chunk = await resp.content.read(64 * 1024)  # 64KB
            elapsed = time.perf_counter() - start
            if elapsed <= 0:
                return None
            speed_kbps = (len(chunk) * 8) / elapsed / 1000  # kbps
            latency = elapsed * 1000  # ms
            return {"latency": latency, "speed_kbps": speed_kbps, "ok": True}
    except Exception:
        return None

async def speed_test(channels, settings):
    timeout = settings.get("timeout", 8)
    concurrency = settings.get("concurrency", 30)
    ua = settings.get("user_agent", "Mozilla/5.0")
    min_speed = settings.get("min_speed_kbps", 0)

    sem = asyncio.Semaphore(concurrency)
    results = []

    async def worker(ch):
        async with sem:
            res = await test_url(session, ch["url"], timeout, ua)
            if res and res["ok"] and res["speed_kbps"] >= min_speed:
                ch.update(res)
                results.append(ch)

    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [worker(ch) for ch in channels]
        await asyncio.gather(*tasks)

    return results

def dedup_and_select(channels, keep=3):
    # 按标准化名称分组
    groups = defaultdict(list)
    for ch in channels:
        key = re.sub(r'[\s\-_]+', '', ch["name"]).lower()
        groups[key].append(ch)

    final = []
    for key, items in groups.items():
        # 按速度降序，延迟升序
        items.sort(key=lambda x: (-x.get("speed_kbps", 0), x.get("latency", 9999)))
        final.extend(items[:keep])
    return final

def generate_m3u(channels):
    lines = ['#EXTM3U']
    # 按 group 排序，再按名称
    channels.sort(key=lambda x: (x["group"], x["name"]))
    for ch in channels:
        speed = ch.get("speed_kbps", 0)
        latency = ch.get("latency", 0)
        info = f' group-title="{ch["group"]}"'
        lines.append(f'#EXTINF:-1{info},{ch["name"]}')
        lines.append(ch["url"])
    return "\n".join(lines)

def generate_txt(channels):
    lines = []
    channels.sort(key=lambda x: (x["group"], x["name"]))
    current_group = None
    for ch in channels:
        if ch["group"] != current_group:
            lines.append(f'{ch["group"]},#genre#')
            current_group = ch["group"]
        lines.append(f'{ch["name"]},{ch["url"]}')
    return "\n".join(lines)

async def main():
    print("加载规则...")
    rules, settings = load_rules()
    keep = settings.get("keep_per_channel", 3)

    print("读取源列表...")
    urls = [line.strip() for line in SOURCES_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]

    all_channels = []
    async with aiohttp.ClientSession() as session:
        for url in urls:
            print(f"拉取: {url}")
            try:
                async with session.get(url, timeout=30) as resp:
                    content = await resp.text(encoding="utf-8", errors="ignore")
                    chs = parse_m3u(content)
                    print(f"  → {len(chs)} 个频道")
                    all_channels.extend(chs)
            except Exception as e:
                print(f"  失败: {e}")

    print(f"总计原始频道: {len(all_channels)}")
    print("应用重命名/分组规则...")
    all_channels = apply_rules(all_channels, rules)

    print("开始测速...")
    tested = await speed_test(all_channels, settings)
    print(f"测速通过: {len(tested)}")

    print("去重并优选...")
    final = dedup_and_select(tested, keep=keep)
    print(f"最终保留: {len(final)}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    m3u_content = generate_m3u(final)
    txt_content = generate_txt(final)

    (OUTPUT_DIR / "iptv.m3u").write_text(m3u_content, encoding="utf-8")
    (OUTPUT_DIR / "iptv.txt").write_text(txt_content, encoding="utf-8")
    print("已写入 output/iptv.m3u 和 output/iptv.txt")

if __name__ == "__main__":
    asyncio.run(main())
