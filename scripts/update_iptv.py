#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import aiohttp
import re
import yaml
import time
import subprocess
import shutil
import os
import platform
from pathlib import Path
from collections import defaultdict

# ====================== 不测速直接保留的规则 ======================
SPECIAL_KEEP_PREFIXES = [
    "http://[2409:8087:",
    "http://hwltc.tv.cdn.zj.chinamobile.com",
    "https://hwltc.tv.cdn.zj.chinamobile.com",
    "rtp://",
    "udp://",
]

SKIP_SPEED_KEYWORDS = [
    "iptv.4666888.xyz",
    "211.72.174",
    "cdn6.cc.cd",
    "cdn3.indevs.in",
    "8.fast.hidns.vip",
    "my5353.com",
    "111.0.115.",
    "lotus.stingray.com",
    "cdn.qd.je",
    "38.64.72",
    "d2jc41c1shgcol.cloudfront.net",
    "b27a6dd8a86c3e4ba93fbae22aaaac64",
    "the-pet-collective-international",
    "81d1c38525014f39a1705b01e04d91c2",
    "ntd02.akamaized.net",
    "f13h.mine.nu",
    "live.wcetv.com",
]

def is_special_keep(url: str) -> bool:
    url_lower = url.lower()
    return any(url_lower.startswith(prefix.lower()) for prefix in SPECIAL_KEEP_PREFIXES)

def is_skip_keyword(url: str) -> bool:
    url_lower = url.lower()
    return any(kw in url_lower for kw in SKIP_SPEED_KEYWORDS)

def is_multicast(url: str) -> bool:
    url_lower = url.lower()
    if url_lower.startswith(("rtp://", "udp://")):
        return True
    if "2409:8087" in url_lower or "[2409:" in url_lower:
        return True
    if "hwltc.tv.cdn.zj.chinamobile.com" in url_lower:
        return True
    return False

def should_skip_speed_test(url: str) -> bool:
    return is_special_keep(url) or is_skip_keyword(url) or is_multicast(url)

# ====================== 路径配置 ======================
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

# ====================== 环境自动检测 ======================
def detect_environment():
    """自动判断当前运行环境"""
    # 1. GitHub Actions
    if os.getenv("GITHUB_ACTIONS") == "true":
        return "github"

    # 2. 路由器（OpenWrt / iStoreOS / ImmortalWrt 等）
    if Path("/etc/openwrt_release").exists():
        return "router"
    
    uname_info = (platform.uname().release + " " + platform.uname().version).lower()
    if any(x in uname_info for x in ["openwrt", "istoreos", "immortalwrt", "lede", "pandora"]):
        return "router"

    # 3. 其他都当电脑端
    return "pc"

def get_runtime_config(user_settings: dict):
    """根据环境自动生成运行参数"""
    env = detect_environment()
    print(f"检测到运行环境: {env}")

    # 用户强制指定模式优先
    force_mode = str(user_settings.get("force_mode", "")).strip().lower()
    
    # 环境默认参数
    env_defaults = {
        "github": {
            "mode": "skip",
            "concurrency": 20,
            "timeout": 8,
            "enable_ffprobe": False,
            "request_delay": 0.05,
            "keep_unicast": 8,
            "keep_multicast": 5,
        },
        "router": {
            "mode": "smart",          # 有代理就走 smart，没有就实际变成 direct
            "concurrency": 6,
            "timeout": 8,
            "enable_ffprobe": False,  # 路由器默认关闭 ffprobe
            "request_delay": 0.25,
            "keep_unicast": 5,
            "keep_multicast": 3,
        },
        "pc": {
            "mode": "smart",
            "concurrency": 16,
            "timeout": 6,
            "enable_ffprobe": True,
            "request_delay": 0.1,
            "keep_unicast": 6,
            "keep_multicast": 4,
        }
    }

    conf = env_defaults[env].copy()

    # 用户配置覆盖默认值
    for k, v in user_settings.items():
        if v is not None and v != "":
            conf[k] = v

    # 处理强制模式
    if force_mode in ("skip", "direct", "smart"):
        conf["mode"] = force_mode

    # 处理代理
    proxy = str(conf.get("proxy", "")).strip()
    if conf["mode"] == "direct":
        proxy = None
    elif conf["mode"] == "smart":
        proxy = proxy or None
    else:  # skip
        proxy = None

    conf["proxy"] = proxy
    conf["env"] = env

    # 最终模式说明
    mode_desc = {
        "skip": "跳过测速（GitHub Actions / 电脑快速模式）",
        "direct": "纯直连测速（不使用代理）",
        "smart": "智能测速（先直连，不够再走代理）"
    }
    print(f"运行模式: {conf['mode']} → {mode_desc.get(conf['mode'], conf['mode'])}")
    if conf["proxy"]:
        print(f"代理地址: {conf['proxy']}")
    else:
        print("代理: 未使用")

    return conf

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
            if len(parts) == 2 and parts[1].strip().startswith(("http", "rtp", "udp")):
                channels.append({
                    "name": parts[0].strip(),
                    "url": parts[1].strip(),
                    "origin_group": ""
                })
        i += 1
    return channels

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

async def light_check(session, url, timeout, ua, proxy=None):
    start = time.perf_counter()
    try:
        headers = {"User-Agent": ua}
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
            ssl=False,
            proxy=proxy
        ) as resp:
            if resp.status >= 400:
                return None
            chunk = await resp.content.read(16 * 1024)
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

def ffprobe_check(url, timeout=10, proxy=None):
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
    ]
    if proxy:
        cmd.extend(["-http_proxy", proxy])
    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        if result.returncode != 0:
            return None

        has_video = has_audio = False
        width = height = 0
        video_codec = audio_codec = "unknown"

        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if not parts:
                continue
            codec_type = parts[0].lower()
            if codec_type == "video":
                has_video = True
                if len(parts) >= 4 and parts[1].isdigit() and parts[2].isdigit():
                    width, height = int(parts[1]), int(parts[2])
                    video_codec = parts[3] if len(parts) > 3 else "unknown"
            elif codec_type == "audio":
                has_audio = True
                if len(parts) >= 2:
                    audio_codec = parts[-1]

        if not has_video and not has_audio:
            return None

        return {
            "ok": True,
            "has_video": has_video,
            "has_audio": has_audio,
            "resolution": f"{width}x{height}" if width and height else "unknown",
            "codec": video_codec if has_video else audio_codec,
            "width": width,
            "height": height
        }
    except Exception:
        return None

async def two_stage_test(channels, conf):
    mode = conf["mode"]
    if mode == "skip":
        print("已开启跳过测速模式，直接保留全部源")
        return channels

    timeout = conf.get("timeout", 6)
    concurrency = conf.get("concurrency", 8)
    ua = conf.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    min_speed = conf.get("min_speed_kbps", 100)
    enable_ffprobe = conf.get("enable_ffprobe", False)
    ffprobe_timeout = conf.get("ffprobe_timeout", 10)
    audio_only_group = conf.get("audio_only_group", "").strip()
    proxy = conf.get("proxy")
    request_delay = conf.get("request_delay", 0.2)

    print(f"测速参数 → 并发:{concurrency} | 超时:{timeout}s | 最低速度:{min_speed}kbps | 间隔:{request_delay}s")
    print(f"FFprobe: {'开启' if enable_ffprobe else '关闭'}")

    sem = asyncio.Semaphore(concurrency)
    light_passed = []

    async def light_worker(ch):
        if should_skip_speed_test(ch["url"]):
            ch.update({"latency_ms": 0, "speed_kbps": 99999, "ok": True, "special_keep": True})
            light_passed.append(ch)
            return

        async with sem:
            # 1. 永远先直连
            res = await light_check(session, ch["url"], timeout, ua, proxy=None)
            if res and res["ok"] and res["speed_kbps"] >= min_speed:
                ch.update(res)
                light_passed.append(ch)
                return

            # 2. 只有 smart 模式且配置了代理，才再测一次代理
            if mode == "smart" and proxy:
                await asyncio.sleep(request_delay)
                res_proxy = await light_check(session, ch["url"], timeout, ua, proxy=proxy)
                if res_proxy and res_proxy["ok"] and res_proxy["speed_kbps"] >= min_speed:
                    ch.update(res_proxy)
                    ch["via_proxy"] = True
                    light_passed.append(ch)
                    return

            # 3. 不达标直接丢弃

    connector = aiohttp.TCPConnector(
        limit=concurrency,
        ssl=False,
        ttl_dns_cache=300,
        force_close=True,
        enable_cleanup_closed=True
    )

    async with aiohttp.ClientSession(connector=connector, trust_env=False) as session:
        await asyncio.gather(*(light_worker(ch) for ch in channels))

    print(f"初筛通过: {len(light_passed)} / {len(channels)}")

    if not enable_ffprobe or not light_passed:
        return light_passed

    print("开始 FFprobe 深度检测...")
    deep_passed = []
    deep_sem = asyncio.Semaphore(max(3, concurrency // 2))

    async def deep_worker(ch):
        if ch.get("special_keep"):
            deep_passed.append(ch)
            return
        async with deep_sem:
            await asyncio.sleep(0.1)
            loop = asyncio.get_running_loop()
            use_proxy = proxy if ch.get("via_proxy") else None
            res = await loop.run_in_executor(None, ffprobe_check, ch["url"], ffprobe_timeout, use_proxy)
            if res and res.get("ok"):
                ch.update(res)
                if not res.get("has_video") and res.get("has_audio") and audio_only_group:
                    ch["group"] = audio_only_group
                deep_passed.append(ch)

    await asyncio.gather(*(deep_worker(ch) for ch in light_passed))
    print(f"深度检测通过: {len(deep_passed)} / {len(light_passed)}")
    return deep_passed

def dedup_and_select(channels, keep_unicast=6, keep_multicast=4):
    groups = defaultdict(list)
    for ch in channels:
        key = re.sub(r'[\s\-_]+', '', ch["name"]).lower()
        groups[key].append(ch)

    final = []
    for items in groups.values():
        unicast, multicast = [], []
        for ch in items:
            if is_multicast(ch["url"]) or ch.get("special_keep"):
                multicast.append(ch)
            else:
                unicast.append(ch)

        unicast.sort(key=lambda x: (-x.get("speed_kbps", 0), x.get("latency_ms", 9999)))
        multicast.sort(key=lambda x: (-x.get("speed_kbps", 0), x.get("latency_ms", 9999)))

        final.extend(unicast[:keep_unicast])
        final.extend(multicast[:keep_multicast])
    return final

def generate_m3u(channels):
    lines = ['#EXTM3U']
    channels.sort(key=lambda x: (x["group"], x["name"]))
    for ch in channels:
        lines.append(f'#EXTINF:-1 group-title="{ch["group"]}",{ch["name"]}')
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
    rules, user_settings = load_rules()
    blacklist = load_list(BLACKLIST_FILE)

    # 自动判断环境并生成最终运行配置
    conf = get_runtime_config(user_settings)

    print(f"最终并发: {conf.get('concurrency')}")
    print(f"最低速度: {conf.get('min_speed_kbps', 100)} kbps")

    print("\n=== 拉取源 ===")
    urls = load_list(SOURCES_FILE)
    all_channels = []

    proxy_for_fetch = conf.get("proxy")
    async with aiohttp.ClientSession() as session:
        for url in urls:
            print(f"拉取: {url}")
            try:
                async with session.get(url, timeout=30, proxy=proxy_for_fetch) as resp:
                    content = await resp.text(encoding="utf-8", errors="ignore")
                    chs = parse_content(content)
                    print(f"  → {len(chs)} 个")
                    all_channels.extend(chs)
            except Exception as e:
                print(f"  失败: {e}")

    print(f"原始总计: {len(all_channels)}")

    print("\n=== 黑名单剔除 ===")
    all_channels = filter_by_blacklist(all_channels, blacklist)
    print(f"黑名单后: {len(all_channels)}")

    print("\n=== 应用重命名/分组 ===")
    all_channels = apply_rules(all_channels, rules)

    print("\n=== 测速阶段 ===")
    tested = await two_stage_test(all_channels, conf)

    keep_unicast = conf.get("keep_unicast", 6)
    keep_multicast = conf.get("keep_multicast", 4)
    print(f"\n=== 去重优选（非组播≤{keep_unicast}，组播≤{keep_multicast}） ===")
    final = dedup_and_select(tested, keep_unicast=keep_unicast, keep_multicast=keep_multicast)
    print(f"最终保留: {len(final)}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    if conf["mode"] == "skip":
        m3u_path = OUTPUT_DIR / "iptv.m3u"
        txt_path = OUTPUT_DIR / "iptv.txt"
    else:
        m3u_path = OUTPUT_DIR / "移动iptv.m3u"
        txt_path = OUTPUT_DIR / "移动iptv.txt"

    m3u_path.write_text(generate_m3u(final), encoding="utf-8")
    txt_path.write_text(generate_txt(final), encoding="utf-8")
    print(f"\n已生成: {m3u_path}")
    print(f"已生成: {txt_path}")

if __name__ == "__main__":
    asyncio.run(main())
