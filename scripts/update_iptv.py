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
from typing import Any, Dict, List, Optional, Tuple

# 不测速直接保留的前缀
SPECIAL_KEEP_PREFIXES = [
    "http://[2409:8087:",
    "http://hwltc.tv.cdn.zj.chinamobile.com",
    "https://hwltc.tv.cdn.zj.chinamobile.com",
    "rtp://",
    "udp://",
]

# URL 包含这些关键词也不测速
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

# 轻量繁简归一（常见台标用字）
CHAR_NORMALIZATION_MAP = str.maketrans({
    "頻": "频", "視": "视", "臺": "台", "綜": "综", "聞": "闻",
    "體": "体", "藝": "艺", "經": "经", "濟": "济", "娛": "娱",
    "樂": "乐", "電": "电", "廣": "广", "畫": "画", "劇": "剧",
    "紀": "纪", "錄": "录", "網": "网", "導": "导", "衛": "卫",
    "灣": "湾", "滬": "沪", "閩": "闽", "蘇": "苏", "浙": "浙",
    "魯": "鲁", "豫": "豫", "鄂": "鄂", "湘": "湘", "粵": "粤",
    "瓊": "琼", "渝": "渝", "遼": "辽", "寧": "宁", "貴": "贵",
    "雲": "云", "陝": "陕", "晉": "晋", "冀": "冀",
})

# 规则未命中时的智能分类兜底（按优先级）
SMART_CATEGORY_KEYWORDS = {
    "港澳台": (
        "翡翠台", "明珠台", "无线新闻", "有线新闻", "HOY", "VIU",
        "凤凰", "寰宇", "纬来", "东森", "中天", "台视", "华视",
        "民视", "三立", "非凡", "年代", "TVBS", "八大", "香港卫视",
        "澳亚", "星空卫视",
    ),
    "广播": ("广播", "电台", "FM", "AM", "之声", "之音", "伴音", "调频"),
    "少儿动漫": ("少儿", "卡通", "动漫", "动画", "童话", "小当家"),
    "体育": ("体育", "足球", "篮球", "网球", "高尔夫", "搏击", "赛事"),
    "影视": ("电影", "影院", "剧场", "电视剧", "影视", "经典剧", "CHC"),
    "文旅": ("文旅", "旅游", "景区", "风景", "风光", "古镇", "古城"),
    "新闻": ("新闻", "时政", "资讯", "观察", "焦点", "头条"),
    "纪录人文": ("纪录", "纪实", "人文", "自然", "地理", "探索"),
    "音乐": ("音乐", "MV", "演唱会", "舞曲"),
    "戏曲综艺": ("戏曲", "戏剧", "曲艺", "梨园", "相声", "小品", "综艺"),
    "法治军事": ("法治", "军事", "国防", "军旅", "警务", "普法"),
    "生活购物": ("购物", "导购", "时尚", "美食", "健康", "养生", "家居"),
    "教育党建": ("党建", "党史", "教育", "教科", "留学", "考试"),
}


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
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def load_rules():
    with open(RULES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rules = []
    for r in data.get("rules", []):
        rules.append({
            "pattern": re.compile(r["filter"]),
            "rename": r.get("rename"),
            "group": r.get("group", "未分类"),
            "replace": r.get("replace"),
        })
    settings = data.get("settings", {})
    return rules, settings


def sanitize_name(name: str) -> str:
    """清洗频道名：繁简归一、去画质标记/括号备注。"""
    if not name:
        return "Unknown"
    text = str(name).translate(CHAR_NORMALIZATION_MAP).strip()
    # 去掉括号备注
    text = re.sub(r"[（(【\[].{0,30}?[)）】\]]", "", text)
    # 去掉常见画质/编码标记
    text = re.sub(
        r"(?i)\b(?:IPV6|HEVC|H\.?265|H\.?264|HDR|UHD|FHD|HD|SD|\d{3,4}[Pp]|4K|8K|高清|超清|标清)\b",
        "",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip(" -_|·•")
    return text or "Unknown"


def match_smart_category(name: str) -> Optional[str]:
    """规则未命中时的主题兜底分类。"""
    n = name.translate(CHAR_NORMALIZATION_MAP)
    for category, keywords in SMART_CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in n.lower():
                return category
    return None


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
                    channels.append({
                        "name": sanitize_name(name),
                        "url": url,
                        "origin_group": group,
                    })
        elif "," in line and not line.startswith("#"):
            parts = line.split(",", 1)
            if len(parts) == 2 and parts[1].strip().startswith(("http", "rtp", "udp")):
                channels.append({
                    "name": sanitize_name(parts[0].strip()),
                    "url": parts[1].strip(),
                    "origin_group": "",
                })
        i += 1
    return channels


def filter_by_keywords(channels, keywords):
    # 白名单可选，不删频道
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
        name = sanitize_name(ch["name"])
        matched = False
        for rule in rules:
            if rule["pattern"].search(name):
                # 支持 replace 清空类规则
                if rule.get("replace") is not None and rule.get("rename") in (None, ""):
                    new_name = rule["pattern"].sub(rule["replace"] or "", name).strip() or name
                else:
                    new_name = rule["rename"] if rule.get("rename") else name
                result.append({
                    "name": sanitize_name(new_name),
                    "url": ch["url"],
                    "group": rule["group"],
                    "origin_name": name,
                })
                matched = True
                break
        if not matched:
            smart = match_smart_category(name)
            group = smart or ch.get("origin_group") or "未分类"
            result.append({
                "name": name,
                "url": ch["url"],
                "group": group,
                "origin_name": name,
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
            ssl=False,
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
                "ok": True,
            }
    except Exception:
        return None


def ffprobe_check(url, timeout=12, proxy=None):
    if not shutil.which("ffprobe"):
        return {
            "ok": True, "has_video": True, "has_audio": True,
            "resolution": "unknown", "codec": "unknown", "width": 0, "height": 0,
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
            "height": height,
        }
    except Exception:
        return None


async def two_stage_test(channels, settings):
    if settings.get("skip_speed_test", False):
        print("已开启 skip_speed_test，跳过所有测速，直接保留全部源")
        return channels

    timeout = settings.get("timeout", 8)
    concurrency = settings.get("concurrency", 40)
    ua = settings.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    min_speed = settings.get("min_speed_kbps", 30)
    enable_ffprobe = settings.get("enable_ffprobe", True)
    ffprobe_timeout = settings.get("ffprobe_timeout", 12)
    audio_only_group = settings.get("audio_only_group", "").strip()
    proxy = settings.get("proxy", "").strip() or None

    if proxy:
        print(f"使用代理: {proxy}")
    else:
        print("未配置代理，直连测速")

    print(f"初筛并发: {concurrency}, 超时: {timeout}s")
    sem = asyncio.Semaphore(concurrency)
    light_passed = []

    async def light_worker(ch):
        if is_special_keep(ch["url"]) or is_skip_keyword(ch["url"]):
            ch.update({
                "latency_ms": 0,
                "speed_kbps": 99999,
                "ok": True,
                "special_keep": True,
            })
            light_passed.append(ch)
            return

        async with sem:
            res = await light_check(session, ch["url"], timeout, ua)
            if res and res["ok"] and res["speed_kbps"] >= min_speed:
                ch.update(res)
                light_passed.append(ch)

    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False, ttl_dns_cache=300)
    timeout_obj = aiohttp.ClientTimeout(total=timeout)

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout_obj,
        trust_env=True,
    ) as session:
        if proxy:
            session._default_proxy = proxy
        await asyncio.gather(*(light_worker(ch) for ch in channels))

    print(f"初筛通过（含特殊直留）: {len(light_passed)} / {len(channels)}")

    if not enable_ffprobe or not light_passed:
        return light_passed

    print("开始 FFmpeg 深度检测...")
    deep_passed = []
    deep_sem = asyncio.Semaphore(min(15, max(5, concurrency // 3)))

    async def deep_worker(ch):
        if ch.get("special_keep"):
            deep_passed.append(ch)
            return
        async with deep_sem:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(
                None, ffprobe_check, ch["url"], ffprobe_timeout, proxy
            )
            if res and res.get("ok"):
                ch.update(res)
                if not res.get("has_video") and res.get("has_audio") and audio_only_group:
                    ch["group"] = audio_only_group
                deep_passed.append(ch)

    await asyncio.gather(*(deep_worker(ch) for ch in light_passed))
    print(f"深度检测通过: {len(deep_passed)} / {len(light_passed)}")
    return deep_passed


def dedup_and_select(channels, keep_unicast=8, keep_multicast=5):
    groups = defaultdict(list)
    for ch in channels:
        key = re.sub(r"[\s\-_]+", "", ch["name"]).lower()
        groups[key].append(ch)

    final = []
    for items in groups.values():
        unicast = []
        multicast = []
        for ch in items:
            if is_multicast(ch["url"]) or ch.get("special_keep"):
                multicast.append(ch)
            else:
                unicast.append(ch)

        # 速度高 > 延迟低 > https 优先
        def rank(x):
            return (
                -x.get("speed_kbps", 0),
                x.get("latency_ms", 9999),
                0 if str(x.get("url", "")).startswith("https://") else 1,
            )

        unicast.sort(key=rank)
        multicast.sort(key=rank)
        final.extend(unicast[:keep_unicast])
        final.extend(multicast[:keep_multicast])

    return final


def natural_sort_key(text: str) -> Tuple[Any, ...]:
    parts = re.split(r"(\d+)", str(text))
    key: List[Any] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())
    return tuple(key)


def cctv_sort_key(channel_name: str) -> Tuple[Any, ...]:
    """CCTV 数字频道按数字排序，+ 版靠后。"""
    match = re.search(r"(?i)CCTV[\s-]?(\d+)(\+?)", channel_name)
    if match:
        num = int(match.group(1))
        is_plus = 1 if match.group(2) == "+" else 0
        return (0, num, is_plus, natural_sort_key(channel_name))
    return (1, natural_sort_key(channel_name))


def channel_sort_key(ch: Dict[str, Any]) -> Tuple[Any, ...]:
    group = ch.get("group", "")
    name = ch.get("name", "")
    if group in ("央视", "央视/综合") or "央视" in group:
        return (group, cctv_sort_key(name))
    return (group, natural_sort_key(name))


def generate_m3u(channels):
    lines = ["#EXTM3U"]
    channels = sorted(channels, key=channel_sort_key)
    for ch in channels:
        group = ch["group"]
        lines.append(f'#EXTINF:-1 group-title="{group}",{ch["name"]}')
        lines.append(ch["url"])
    return "\n".join(lines)


def generate_txt(channels):
    lines = []
    channels = sorted(channels, key=channel_sort_key)
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

    skip_test = settings.get("skip_speed_test", False)
    keep_unicast = settings.get("keep_unicast", 8)
    keep_multicast = settings.get("keep_multicast", 5)
    proxy = settings.get("proxy", "").strip()

    print(f"跳过测速: {skip_test}")
    print(f"非组播保留: {keep_unicast} 个，组播保留: {keep_multicast} 个")
    print(f"代理: {proxy if proxy else '无'}")
    print(f"白名单关键字: {len(keywords)} 个（不删除）")
    print(f"黑名单: {len(blacklist)} 个")

    print("\n=== 拉取源 ===")
    urls = load_list(SOURCES_FILE)
    all_channels = []

    proxy_for_fetch = proxy if proxy else None
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

    print("\n=== 应用重命名/分组（含智能兜底） ===")
    all_channels = apply_rules(all_channels, rules)

    print("\n=== 测速阶段 ===")
    tested = await two_stage_test(all_channels, settings)

    print(f"\n=== 去重优选（非组播≤{keep_unicast}，组播≤{keep_multicast}） ===")
    final = dedup_and_select(tested, keep_unicast=keep_unicast, keep_multicast=keep_multicast)
    print(f"最终保留: {len(final)}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    if skip_test:
        m3u_path = OUTPUT_DIR / "iptv.m3u"
        txt_path = OUTPUT_DIR / "iptv.txt"
        print("\n模式：不测速（GitHub Actions）")
    else:
        m3u_path = OUTPUT_DIR / "移动iptv.m3u"
        txt_path = OUTPUT_DIR / "移动iptv.txt"
        print("\n模式：已测速（软路由/本地）")

    m3u_path.write_text(generate_m3u(final), encoding="utf-8")
    txt_path.write_text(generate_txt(final), encoding="utf-8")
    print(f"已生成: {m3u_path}")
    print(f"已生成: {txt_path}")


if __name__ == "__main__":
    asyncio.run(main())
