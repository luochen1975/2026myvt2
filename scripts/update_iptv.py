#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import aiohttp
import re
import yaml
import time
import subprocess
import shutil
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"

SOURCES_FILE = CONFIG_DIR / "sources.txt"
RULES_FILE = CONFIG_DIR / "rules.yaml"
KEYWORDS_FILE = CONFIG_DIR / "keywords.txt"
BLACKLIST_FILE = CONFIG_DIR / "blacklist.txt"


def load_list(path: Path):
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def load_rules():
    with open(RULES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rules = []
    for r in data.get("rules", []):
        rules.append({
            "pattern": re.compile(r["filter"]),
            "rename": r.get("rename"),
            "group": r.get("group", "未分类")
        })
    settings = data.get("settings", {})
    return rules, settings


def parse_content(content: str):
    channels = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            name = line.split(",")[-1].strip() if "," in line else "Unknown"
            group_match = re.search(r'group-title="([^"]*)"', line)
            group = group_match.group(1) if group_match else ""
            i += 1
            if i < len(lines):
                url = lines[i].strip()
                if url and not url.startswith("#"):
                    channels.append({"name": name, "url": url, "origin_group": group})
        elif "," in line and not line.startswith("#"):
            parts = line.split(",", 1)
            if len(parts) == 2 and parts[1].strip().startswith("http"):
                channels.append({
                    "name": parts[0].strip(),
                    "url": parts[1].strip(),
                    "origin_group": ""
                })
        i += 1
    return channels


def filter_by_keywords(channels, keywords):
    if not keywords:
        return channels
    return [ch for ch in channels if any(kw.lower() in ch["name"].lower() for kw in keywords)]


def filter_by_blacklist(channels, blacklist):
    if not blacklist:
        return channels
    result = []
    for ch in channels:
        text = (ch["name"] + " " + ch["url"]).lower()
        if not any(bl.lower() in text for bl in blacklist):
            result.append(ch)
    return result


def apply_rules(channels, rules):
    result = []
    for ch in channels:
        name = ch["name"]
        matched = False
        for rule in rules:
            if rule["pattern"].search(name):
                result.append({
                    "name": rule["rename"] if rule["rename"] else name,
                    "url": ch["url"],
                    "group": rule["group"],
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


async def light_check(session, url, timeout, ua):
    start = time.perf_counter()
    try:
        headers = {"User-Agent": ua}
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
            ssl=False
        ) as resp:
            if resp.status >= 400:
                return None
            chunk = await resp.content.read(32 * 1024)
            elapsed = time.perf_counter() - start
            if elapsed <= 0 or len(chunk) < 1024:
                return None
            speed_kbps = (len(chunk) * 8) / elapsed / 1000
            return {
                "latency_ms": round(elapsed * 1000, 1),
                "speed_kbps": round(speed_kbps, 1),
                "ok": True
            }
    except Exception:
        return None


def ffprobe_check(url, timeout=12):
    if not shutil.which("ffprobe"):
        return {
            "ok": True, "has_video": True, "has_audio": True,
            "resolution": "unknown", "codec": "unknown", "width": 0, "height": 0
        }

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=codec_type,width,height,codec_name",
        "-of", "csv=p=0",
        "-timeout", str(timeout * 1000000),
        "-user_agent", "Mozilla/5.0",
        url
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        if result.returncode != 0:
            return None

        has_video = False
        has_audio = False
        width, height = 0, 0
        video_codec = "unknown"
        audio_codec = "unknown"

        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if not parts:
                continue
            codec_type = parts[0].lower()

            if codec_type == "video":
                has_video = True
                if len(parts) >= 4 and parts[1].isdigit() and parts[2].isdigit():
                    width = int(parts[1])
                    height = int(parts[2])
                    video_codec = parts[3] if len(parts) > 3 else "unknown"
                elif len(parts) >= 2:
                    video_codec = parts[-1]
            elif codec_type == "audio":
                has_audio = True
                if len(parts) >= 2:
                    audio_codec = parts[-1]

        if not has_video and not has_audio:
            return None

        resolution = f"{width}x{height}" if width and height else "unknown"
        return {
            "ok": True,
            "has_video": has_video,
            "has_audio": has_audio,
            "resolution": resolution,
            "codec": video_codec if has_video else audio_codec,
            "width": width,
            "height": height
        }
    except Exception:
        return None


async def two_stage_test(channels, settings):
    timeout = settings.get("timeout", 8)
    concurrency = settings.get("concurrency", 40)
    ua = settings.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    min_speed = settings.get("min_speed_kbps", 30)
    enable_ffprobe = settings.get("enable_ffprobe", True)
    ffprobe_timeout = settings.get("ffprobe_timeout", 12)
    audio_only_group = settings.get("audio_only_group", "").strip()

    print(f"初筛并发: {concurrency}, 超时: {timeout}s")
    sem = asyncio.Semaphore(concurrency)
    light_passed = []

    async def light_worker(ch):
        async with sem:
            res = await light_check(session, ch["url"], timeout, ua)
            if res and res["ok"] and res["speed_kbps"] >= min_speed:
                ch.update(res)
                light_passed.append(ch)

    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.gather(*(light_worker(ch) for ch in channels))

    print(f"初筛通过: {len(light_passed)} / {len(channels)}")

    if not enable_ffprobe or not light_passed:
        return light_passed

    print("开始 FFmpeg 深度检测...")
    deep_passed = []
    deep_sem = asyncio.Semaphore(min(15, max(5, concurrency // 3)))

    async def deep_worker(ch):
        async with deep_sem:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, ffprobe_check, ch["url"], ffprobe_timeout)
            if res and res.get("ok"):
                ch.update(res)
                # 纯音频 → 自动归到广播
                if not res.get("has_video") and res.get("has_audio") and audio_only_group:
                    ch["group"] = audio_only_group
                deep_passed.append(ch)

    await asyncio.gather(*(deep_worker(ch) for ch in light_passed))
    print(f"深度检测通过: {len(deep_passed)} / {len(light_passed)}")
    return deep_passed


def dedup_and_select(channels, keep=3):
    groups = defaultdict(list)
    for ch in channels:
        key = re.sub(r'[\s\-_]+', '', ch["name"]).lower()
        groups[key].append(ch)

    final = []
    for items in groups.values():
        items.sort(key=lambda x: (
            -x.get("height", 0),
            -x.get("speed_kbps", 0),
            x.get("latency_ms", 9999)
        ))
        final.extend(items[:keep])
    return final


def generate_m3u(channels):
    lines = ['#EXTM3U']
    channels.sort(key=lambda x: (x["group"], x["name"]))
    for ch in channels:
        group = ch["group"]
        lines.append(f'#EXTINF:-1 group-title="{group}",{ch["name"]}')
        lines.append(ch["url"])
    return "\n".join(lines)


def generate_txt(channels):
    lines = []
    channels.sort(key=lambda x: (x["group"], x["name"]))
    current = None
    for ch in channels:
        if ch["group"] != current:
            lines.append(f'{ch["group"]},#genre#')
            current = ch["group"]
        lines.append(f'{ch["name"]},{ch["url"]}')
    return "\n".join(lines)


async def main():
    print("=== 加载配置 ===")
    rules, settings = load_rules()
    keywords = load_list(KEYWORDS_FILE)
    blacklist = load_list(BLACKLIST_FILE)
    keep = settings.get("keep_per_channel", 3)

    print(f"白名单关键字: {len(keywords)} 个")
    print(f"黑名单: {len(blacklist)} 个")

    print("\n=== 拉取源 ===")
    urls = load_list(SOURCES_FILE)
    all_channels = []

    async with aiohttp.ClientSession() as session:
        for url in urls:
            print(f"拉取: {url}")
            try:
                async with session.get(url, timeout=30) as resp:
                    content = await resp.text(encoding="utf-8", errors="ignore")
                    chs = parse_content(content)
                    print(f"  → {len(chs)} 个")
                    all_channels.extend(chs)
            except Exception as e:
                print(f"  失败: {e}")

    print(f"原始总计: {len(all_channels)}")

    print("\n=== 白名单筛选 ===")
    all_channels = filter_by_keywords(all_channels, keywords)
    print(f"白名单后: {len(all_channels)}")

    print("\n=== 黑名单剔除 ===")
    all_channels = filter_by_blacklist(all_channels, blacklist)
    print(f"黑名单后: {len(all_channels)}")

    print("\n=== 应用重命名/分组 ===")
    all_channels = apply_rules(all_channels, rules)

    print("\n=== 两阶段测速 ===")
    tested = await two_stage_test(all_channels, settings)

    print("\n=== 去重优选 ===")
    final = dedup_and_select(tested, keep=keep)
    print(f"最终保留: {len(final)}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "iptv.m3u").write_text(generate_m3u(final), encoding="utf-8")
    (OUTPUT_DIR / "iptv.txt").write_text(generate_txt(final), encoding="utf-8")
    print("\n已生成 output/iptv.m3u 和 output/iptv.txt")


if __name__ == "__main__":
    asyncio.run(main())
