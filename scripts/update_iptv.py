#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IPTV 分组修复版
修复：空 origin_group 拦截地理分组、geo 不生效、txt 输出分组异常全部挤在未分类
"""

import asyncio
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


def _die(msg, code=1):
    print("\n[FATAL] " + msg, file=sys.stderr)
    sys.exit(code)


try:
    import aiohttp
except Exception as e:
    _die("无法 import aiohttp（%s）。\n路由上执行：opkg update && opkg install python3-aiohttp" % e)
try:
    import yaml
except Exception as e:
    _die("无法 import yaml（%s）。\n路由上执行：opkg update && opkg install python3-yaml" % e)


# ====================== 地理分组配置 ======================
ONLINE_GEO_DATA_URLS = [
    "https://raw.githubusercontent.com/modood/Administrative-divisions-of-China/master/dist/pca-code.json",
    "https://fastly.jsdelivr.net/gh/modood/Administrative-divisions-of-China/dist/pca-code.json",
]
GEO_CACHE_PATH = "./pca-code.json"
GEO_CACHE_EXPIRE_SEC = 86400 * 7
IGNORED_GEO_NAMES = {
    "市辖区", "开发区", "高新区", "新区", "工业园区", "示范区",
    "保税区", "自贸区", "经开区", "功能区"
}
ALIAS_MAP = {
    "甬": "宁波市",
    "彭城": "徐州市",
}

name_to_node: dict = {}
name_list_long_first: list[str] = []
node_parent: dict = {}
geo_ready = False
debug_geo = False


def download_geo_json():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    for url in ONLINE_GEO_DATA_URLS:
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=15) as resp:
                raw = resp.read()
                data = json.loads(raw)
                with open(GEO_CACHE_PATH, "wb") as f:
                    f.write(raw)
                return data
        except (URLError, HTTPError, TimeoutError, json.JSONDecodeError, OSError):
            continue
    return None


def load_geo_data():
    if os.path.exists(GEO_CACHE_PATH):
        mtime = os.path.getmtime(GEO_CACHE_PATH)
        if time.time() - mtime < GEO_CACHE_EXPIRE_SEC:
            try:
                with open(GEO_CACHE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return download_geo_json()


def build_geo_index(tree):
    """兼容 list / dict 两种区划 JSON（modood pca-code 顶层与 children 均为 list）。"""
    global name_to_node, name_list_long_first, node_parent, geo_ready
    name_to_node.clear()
    node_parent.clear()

    def iter_children(node):
        if not isinstance(node, dict):
            return
        children = node.get("children")
        if not children:
            return
        if isinstance(children, dict):
            for child in children.values():
                if isinstance(child, dict):
                    yield child
        elif isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    yield child

    if isinstance(tree, list):
        roots = [n for n in tree if isinstance(n, dict)]
    elif isinstance(tree, dict):
        if "name" in tree and ("children" in tree or "code" in tree):
            roots = [tree]
        else:
            roots = [n for n in tree.values() if isinstance(n, dict)]
    else:
        print("[WARN] 区划数据格式无法识别：%s" % type(tree))
        geo_ready = False
        return

    stack = [(node, None) for node in roots]
    while stack:
        node, parent = stack.pop()
        if not isinstance(node, dict):
            continue
        name = node.get("name")
        code = node.get("code")
        if not name:
            continue
        if name not in name_to_node:
            name_to_node[name] = node
        if code is not None:
            node_parent[code] = parent
        for child in iter_children(node):
            stack.append((child, node))

    name_list_long_first = sorted(name_to_node.keys(), key=len, reverse=True)
    geo_ready = bool(name_to_node)
    print(f"[GEO-INFO] 区划索引构建完成，一共加载 {len(name_to_node)} 个地区名称")


def get_prov_city_from_node(node: dict):
    cur = node
    city = None
    prov = None
    while cur is not None:
        code = cur.get("code")
        p = node_parent.get(code) if code is not None else None
        if p is None:
            prov = cur.get("name")
            break
        city = cur.get("name")
        cur = p
    return prov, city

def _norm_province_name(name: str) -> str:
    """浙江省 → 浙江；北京市 → 北京"""
    if not name:
        return ""
    s = str(name).strip()
    for suffix in ("维吾尔自治区", "壮族自治区", "回族自治区", "自治区",
                   "特别行政区", "省", "市"):
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
            break
    return s


def match_geo_from_channel(channel_name: str) -> dict:
    """只精确到省，分组形如 地方>浙江。"""
    if not geo_ready:
        return {"province": None, "city": None, "group": None}
    text = channel_name
    for alias, std_name in ALIAS_MAP.items():
        text = text.replace(alias, std_name)
    for geo_name in name_list_long_first:
        if geo_name in text:
            if geo_name in IGNORED_GEO_NAMES:
                continue
            node = name_to_node[geo_name]
            prov, city = get_prov_city_from_node(node)
            if prov:
                prov_short = _norm_province_name(prov)
                return {
                    "province": prov_short,
                    "city": city,
                    "group": f"地方>{prov_short}" if prov_short else None,
                }
    return {"province": None, "city": None, "group": None}


def init_geo_auto_group(debug=False) -> bool:
    global debug_geo
    debug_geo = debug
    data = load_geo_data()
    if not data:
        print("[WARN] 区划数据加载失败，自动地理分组关闭！")
        return False
    build_geo_index(data)
    return geo_ready


def apply_auto_geo_group(channels, enabled: bool):
    if not enabled:
        print("[GEO-INFO] 自动地理分组配置关闭，跳过")
        return
    if not geo_ready:
        print("[WARN] geo 模块未就绪，跳过地理分组")
        return
    for ch in channels:
        g_orig = ch.get("group", "")
        if g_orig != "未分类":
            if debug_geo:
                print(f"[GEO-SKIP] {ch['name']} 已有分组 {g_orig}，不覆盖")
            continue
        geo = match_geo_from_channel(ch["name"])
        g_new = geo.get("group")
        if g_new:
            ch["group"] = g_new
            if debug_geo:
                print(f"[GEO-MATCH] {ch['name']} → {g_new}")
        else:
            if debug_geo:
                print(f"[GEO-NONE] {ch['name']} 未识别到地区，保留未分类")


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

STATS = {
    "done": 0,
    "direct_ok": 0,
    "proxy_only": 0,
    "dead": 0,
    "proxy_auth": 0,
    "special": 0,
}


def is_special_keep(url):
    url_lower = url.lower()
    return any(url_lower.startswith(prefix.lower()) for prefix in SPECIAL_KEEP_PREFIXES)


def is_skip_keyword(url):
    url_lower = url.lower()
    return any(kw in url_lower for kw in SKIP_SPEED_KEYWORDS)


def is_multicast(url):
    url_lower = url.lower()
    if url_lower.startswith(("rtp://", "udp://")):
        return True
    if "2409:8087" in url_lower or "[2409:" in url_lower:
        return True
    if "hwltc.tv.cdn.zj.chinamobile.com" in url_lower:
        return True
    return False


def should_skip_speed_test(url):
    return is_special_keep(url) or is_skip_keyword(url) or is_multicast(url)


# ====================== 路径配置 ======================
def locate_root():
    try:
        here = Path(__file__).resolve()
    except NameError:
        _die("检测不到 __file__，请把脚本存成文件再执行，不要用管道/heredoc 喂给 python3")
    for c in (here.parent.parent, here.parent, Path.cwd()):
        if (c / "config").is_dir():
            return c
    return here.parent.parent


ROOT = locate_root()
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"
SOURCES_FILE = CONFIG_DIR / "sources.txt"
RULES_FILE = CONFIG_DIR / "rules.yaml"
KEYWORDS_FILE = CONFIG_DIR / "keywords.txt"
BLACKLIST_FILE = CONFIG_DIR / "blacklist.txt"


def load_list(path):
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip() and not line.startswith("#")]


def load_rules():
    if not RULES_FILE.exists():
        print("[WARN] 未找到 %s，按空规则继续" % RULES_FILE)
        return [], {}
    try:
        with open(RULES_FILE, encoding="utf-8", errors="ignore") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        print("[ERROR] 解析 rules.yaml 失败：%s，按空规则继续" % e)
        traceback.print_exc()
        return [], {}
    if not isinstance(data, dict):
        print("[ERROR] rules.yaml 顶层应为字典（rules: / settings:）")
        return [], {}

    rules = []
    for idx, r in enumerate(data.get("rules") or []):
        if not isinstance(r, dict) or "filter" not in r:
            print("[WARN] rules[%d] 缺 filter 字段，跳过：%r" % (idx, r))
            continue
        try:
            rules.append({
                "pattern": re.compile(r["filter"]),
                "rename": r.get("rename"),
                "replace": r.get("replace"),
                "group": r.get("group", "未分类"),
            })
        except re.error as e:
            print("[WARN] rules[%d] 正则 %r 编译失败：%s，跳过" % (idx, r.get("filter"), e))

    settings = data.get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    if "force_mode" not in settings and data.get("force_mode") is not None:
        settings["force_mode"] = data.get("force_mode")
    return rules, settings


# ====================== 环境自动检测 ======================
def detect_environment():
    if os.getenv("GITHUB_ACTIONS") == "true":
        return "github"
    if Path("/etc/openwrt_release").exists():
        return "router"
    uname_info = (platform.uname().release + " " + platform.uname().version).lower()
    if any(x in uname_info for x in ["openwrt", "istoreos", "immortalwrt", "lede", "pandora"]):
        return "router"
    return "pc"


ENV_DEFAULTS = {
    "github": {"mode": "skip", "concurrency": 20, "timeout": 8, "enable_ffprobe": False,
               "request_delay": 0.05, "keep_unicast": 8, "keep_multicast": 5, "auto_geo_group": False, "debug_geo": False},
    "router": {"mode": "smart", "concurrency": 6, "timeout": 8, "enable_ffprobe": False,
               "request_delay": 0.25, "keep_unicast": 5, "keep_multicast": 3, "auto_geo_group": False, "debug_geo": False},
    "pc": {"mode": "smart", "concurrency": 16, "timeout": 6, "enable_ffprobe": True,
           "request_delay": 0.1, "keep_unicast": 6, "keep_multicast": 4, "auto_geo_group": False, "debug_geo": False},
}


def _clean_proxy(value):
    v = str(value or "").strip()
    if not v:
        return None
    if not v.lower().startswith(("http://", "https://")):
        print("[WARN] 代理 %r 不是 http:// 或 https:// 开头，aiohttp 不支持 socks，已忽略" % v)
        return None
    return v


def get_runtime_config(user_settings):
    env = detect_environment()
    print("检测到运行环境: %s" % env)
    force_mode = str(user_settings.get("force_mode", "")).strip().lower()

    conf = ENV_DEFAULTS[env].copy()
    for k, v in user_settings.items():
        if v is not None and v != "":
            conf[k] = v
    if force_mode in ("skip", "direct", "smart"):
        conf["mode"] = force_mode
    elif force_mode:
        print("[WARN] 未知 force_mode: %r（只接受 skip/direct/smart），按 smart 处理" % force_mode)

    probe_proxy = _clean_proxy(conf.get("proxy", ""))
    if conf["mode"] in ("direct", "skip"):
        probe_proxy = None
    conf["proxy"] = probe_proxy

    fetch_proxy = None
    if bool(conf.get("fetch_via_proxy", False)):
        fetch_proxy = probe_proxy
    conf["proxy_fetch"] = fetch_proxy

    conf["env"] = env
    conf.setdefault("mark_proxy_only", True)
    conf.setdefault("prefer_direct", True)
    conf.setdefault("keep_proxy_only", True)
    conf.setdefault("auto_geo_group", False)
    conf.setdefault("debug_geo", False)

    mode_desc = {
        "skip": "跳过测速（GitHub Actions / 快速模式）",
        "direct": "纯直连测速（不使用代理）",
        "smart": "智能测速（先直连，<阈值再代理补测）",
    }
    print("运行模式: %s → %s" % (conf["mode"], mode_desc.get(conf["mode"], conf["mode"])))
    print("拉取订阅: %s" % ("经代理 %s" % fetch_proxy if fetch_proxy else "直连（不使用代理）"))
    print("测速补测代理: %s" % (probe_proxy if probe_proxy else "无"))
    print("自动地理分组: %s | geo调试: %s" % ("开启" if conf.get("auto_geo_group") else "关闭", "开启" if conf.get("debug_geo") else "关闭"))
    return conf


# ====================== 解析 ======================
def parse_content(content):
    channels = []
    lines = content.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            name = line.split(",", 1)[1].strip() if "," in line else "Unknown"
            group_match = re.search(r'group-title="([^"]*)"', line)
            group = group_match.group(1).strip() if group_match else ""
            i += 1
            while i < len(lines):
                cand = lines[i].strip()
                if not cand or cand.startswith("#"):
                    i += 1
                    continue
                url = cand.split("#", 1)[0].split("$", 1)[0].strip()
                if url.startswith(("http://", "https://", "rtp://", "udp://")):
                    channels.append({"name": name, "url": url, "origin_group": group})
                break
        elif line and not line.startswith("#") and "," in line:
            parts = line.split(",", 1)
            if len(parts) == 2 and parts[1].strip().startswith(("http", "rtp", "udp")):
                channels.append({
                    "name": parts[0].strip(),
                    "url": parts[1].strip(),
                    "origin_group": "",
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
                rep = rule.get("replace")
                new_name = name
                if rep is not None:
                    if isinstance(rep, (list, tuple)) and len(rep) == 2:
                        new_name = name.replace(str(rep[0]), str(rep[1])).strip() or name
                    else:
                        new_name = rule["pattern"].sub(str(rep), name).strip() or name
                elif rule.get("rename"):
                    new_name = rule["rename"]
                result.append({
                    "name": new_name,
                    "url": ch["url"],
                    "group": rule["group"],
                    "origin_name": name,
                })
                matched = True
                break
        if not matched:
            origin_g = ch.get("origin_group", "").strip()
            # 重点修复：空 origin_group 强制转为未分类，交给 geo 处理
            final_g = origin_g if origin_g else "未分类"
            result.append({
                "name": name,
                "url": ch["url"],
                "group": final_g,
                "origin_name": name,
            })
    return result


# ====================== 测速 ======================
def _fail(reason):
    return {"ok": False, "reason": reason}


def _http_fail(status, proxy=None):
    if status == 407 and proxy:
        return _fail("proxy_auth")
    return _fail("http_%d" % status)


async def hls_check(session, url, timeout, ua, proxy=None, depth=0):
    try:
        async with session.get(
            url, headers={"User-Agent": ua},
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True, ssl=False, proxy=proxy,
        ) as resp:
            if resp.status >= 400:
                return _http_fail(resp.status, proxy)
            base = str(resp.url)
            text = await resp.text(errors="ignore")

        seg = None
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            seg = s if s.startswith(("http://", "https://")) else urljoin(base, s)
            break
        if not seg:
            return _fail("no_segment")

        t0 = time.perf_counter()
        async with session.get(
            seg, headers={"User-Agent": ua, "Referer": base},
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True, ssl=False, proxy=proxy,
        ) as r2:
            if r2.status >= 400:
                return _http_fail(r2.status, proxy)
            data = await _read_window(r2, SAMPLE_BYTES, SAMPLE_SECONDS)

        if depth == 0 and data.lstrip().startswith(b"#EXTM3U"):
            return await hls_check(session, seg, timeout, ua, proxy, depth=1)

        elapsed = time.perf_counter() - t0
        if elapsed <= 0 or len(data) < 1024:
            return _fail("seg_too_small")
        return {
            "ok": True,
            "latency_ms": round(elapsed * 1000, 1),
            "speed_kbps": round(len(data) * 8 / elapsed / 1000, 1),
        }
    except Exception as e:
        return _fail(_classify_error(e))


def _classify_error(e):
    status = getattr(e, "status", None)
    if status == 407:
        return "proxy_auth"
    if status:
        return "http_%s" % status
    return "err:%s" % type(e).__name__


SAMPLE_BYTES = 64 * 1024
SAMPLE_SECONDS = 1.0
MIN_BYTES = 1024


async def _read_window(resp, max_bytes, max_seconds):
    buf = b""
    deadline = time.perf_counter() + max_seconds
    while len(buf) < max_bytes:
        remain = deadline - time.perf_counter()
        if remain <= 0:
            break
        try:
            part = await asyncio.wait_for(resp.content.read(8192), timeout=remain)
        except asyncio.TimeoutError:
            break
        if not part:
            break
        buf += part
    return buf


async def light_check(session, url, timeout, ua, proxy=None):
    if ".m3u8" in url.lower():
        return await hls_check(session, url, timeout, ua, proxy)

    start = time.perf_counter()
    try:
        async with session.get(
            url,
            headers={"User-Agent": ua},
            timeout=aiohttp.ClientTimeout(total=timeout),
            allow_redirects=True,
            ssl=False,
            proxy=proxy,
        ) as resp:
            if resp.status >= 400:
                return _http_fail(resp.status, proxy)
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "mpegurl" in ctype:
                return await hls_check(session, str(resp.url), timeout, ua, proxy)

            chunk = await _read_window(resp, SAMPLE_BYTES, SAMPLE_SECONDS)
            elapsed = time.perf_counter() - start
            if elapsed <= 0 or len(chunk) < MIN_BYTES:
                return _fail("too_small_or_slow")

            head = chunk[:200].decode("utf-8", errors="ignore")
            if "#EXTM3U" in head or "#EXTINF" in head:
                return await hls_check(session, str(resp.url), timeout, ua, proxy)

            return {
                "ok": True,
                "latency_ms": round(elapsed * 1000, 1),
                "speed_kbps": round(len(chunk) * 8 / elapsed / 1000, 1),
            }
    except Exception as e:
        return _fail(_classify_error(e))


def ffprobe_check(url, timeout=10, proxy=None):
    if not shutil.which("ffprobe"):
        return {"ok": True, "skipped": True}
    cmd = [
        "ffprobe", "-v", "error",
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
            "resolution": "%dx%d" % (width, height) if width and height else "unknown",
            "codec": video_codec if has_video else audio_codec,
            "width": width,
            "height": height,
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
    audio_only_group = (conf.get("audio_only_group") or "").strip()
    proxy = conf.get("proxy")
    request_delay = conf.get("request_delay", 0.2)
    total = len(channels)

    print("测速参数 → 并发:%s | 超时:%ss | 最低速度:%skbps | 间隔:%ss" % (concurrency, timeout, min_speed, request_delay))
    print("FFprobe: %s" % ("开启" if enable_ffprobe else "关闭"))
    if mode == "smart":
        print("策略: 直连不足 %skbps 时，再用代理补测一次" % min_speed)
        print("测速补测代理: %s" % (proxy if proxy else "无（只测直连）"))

    sem = asyncio.Semaphore(concurrency)
    light_passed = []

    async def light_worker(ch):
        if should_skip_speed_test(ch["url"]):
            ch.update({"latency_ms": 0, "speed_kbps": 99999, "ok": True,
                       "special_keep": True, "via_proxy": False})
            light_passed.append(ch)
            STATS["special"] += 1
            STATS["done"] += 1
            return

        async with sem:
            res = await light_check(session, ch["url"], timeout, ua, proxy=None)
            if not res.get("ok") and str(res.get("reason", "")).startswith(("err:", "too_small")):
                await asyncio.sleep(0.15)
                res = await light_check(session, ch["url"], timeout, ua, proxy=None)
            if res.get("ok") and res.get("speed_kbps", 0) >= min_speed:
                ch.update(res)
                ch["via_proxy"] = False
                ch["direct_kbps"] = res["speed_kbps"]
                light_passed.append(ch)
                STATS["direct_ok"] += 1
            else:
                ch["direct_kbps"] = res.get("speed_kbps", 0) if res.get("ok") else 0
                if mode == "smart" and proxy:
                    await asyncio.sleep(request_delay)
                    rp = await light_check(session, ch["url"], timeout, ua, proxy=proxy)
                    if rp.get("ok") and rp.get("speed_kbps", 0) >= min_speed:
                        ch.update(rp)
                        ch["via_proxy"] = True
                        ch["proxy_kbps"] = rp["speed_kbps"]
                        light_passed.append(ch)
                        STATS["proxy_only"] += 1
                    else:
                        if rp.get("reason") == "proxy_auth":
                            STATS["proxy_auth"] += 1
                        STATS["dead"] += 1
                else:
                    STATS["dead"] += 1
            STATS["done"] += 1
            if STATS["done"] % 200 == 0 or STATS["done"] == total:
                print("  进度 %d/%d | 直连通过 %d | 仅代理 %d | 不通 %d%s"
                      % (STATS["done"], total, STATS["direct_ok"], STATS["proxy_only"],
                         STATS["dead"], "（代理认证失败 %d）" % STATS["proxy_auth"] if STATS["proxy_auth"] else ""))

    connector = aiohttp.TCPConnector(limit=concurrency * 2, ssl=False, ttl_dns_cache=300,
                                     force_close=True, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector, trust_env=False) as session:
        await asyncio.gather(*(light_worker(ch) for ch in channels))

    print("初筛通过: %d / %d  （直连 %d | 仅代理 %d | 不通 %d）"
          % (len(light_passed), total, STATS["direct_ok"], STATS["proxy_only"], STATS["dead"]))

    if STATS["proxy_auth"]:
        print("[WARN] 有 %d 个源在代理补测时返回 407（代理需要账号密码）。" % STATS["proxy_auth"])
        print("       这意味着这些源被当成「不通」丢掉了，实际可能是代理把请求拒了。")
        print("       先跑：python3 scripts/update_iptv.py --check-proxy 确认。")

    if not enable_ffprobe or not light_passed:
        return light_passed
    if not shutil.which("ffprobe"):
        print("[WARN] 未找到 ffprobe，深度检测跳过（不影响初筛结果）")
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
    print("深度检测通过: %d / %d" % (len(deep_passed), len(light_passed)))
    return deep_passed


# ====================== 去重与输出 ======================
BRACKET_RE = re.compile(r"[（(【\[][^)）】\]]{0,20}[)）】\]]")
NOISE_RE = re.compile(
    r"(?i)(?:ipv6|hevc|h\.?265|h\.?264|hdr|uhd|fhd|4k|8k|高清|超清|标清|\d{3,4}[pP]"
    r"|(?<![a-z])(?:hd|sd)(?![a-z]))"
)


def dedup_key(name):
    s = BRACKET_RE.sub("", str(name))
    s = NOISE_RE.sub("", s)
    s = re.sub(r"[\s\-_·•|]+", "", s)
    return s.lower() or str(name).lower()


def dedup_and_select(channels, keep_unicast=6, keep_multicast=4, prefer_direct=True,
                     keep_proxy_only=True):
    groups = defaultdict(list)
    for ch in channels:
        groups[dedup_key(ch["name"])].append(ch)

    def rank(x):
        return (
            0 if not x.get("via_proxy") else 1 if prefer_direct else 0,
            -x.get("speed_kbps", 0),
            x.get("latency_ms", 9999),
        )

    final = []
    for items in groups.values():
        unicast, multicast = [], []
        for ch in items:
            if not keep_proxy_only and ch.get("via_proxy"):
                continue
            if is_multicast(ch["url"]) or ch.get("special_keep"):
                multicast.append(ch)
            else:
                unicast.append(ch)
        unicast.sort(key=rank)
        multicast.sort(key=rank)
        final.extend(unicast[:keep_unicast])
        final.extend(multicast[:keep_multicast])
    return final


def _safe_text(text):
    s = str(text).replace(",", " ").replace("，", " ").strip()
    return s if s else "未分类"


def _display_name(ch, conf):
    name = str(ch["name"])
    if ch.get("via_proxy") and conf.get("mark_proxy_only", True):
        return name + "〔需代理〕"
    return name


GROUP_ORDER = ["央视", "卫视", "港澳台","地方" ,"精彩", "其他","其他>影视", "其他>体育", "其他>新闻", "其他>纪录",
               "其他>少儿", "其他>戏曲", "其他>音乐", "其他>生活", "其他>教育", "其他>广播", "其他>军事", "其他>未分类"]
# 省会（其他省：省台、省会优先）
PROVINCE_CAPITAL = {
    "北京": "北京", "天津": "天津", "上海": "上海", "重庆": "重庆",
    "河北": "石家庄", "山西": "太原", "辽宁": "沈阳", "吉林": "长春",
    "黑龙江": "哈尔滨", "江苏": "南京", "浙江": "杭州", "安徽": "合肥",
    "福建": "福州", "江西": "南昌", "山东": "济南", "河南": "郑州",
    "湖北": "武汉", "湖南": "长沙", "广东": "广州", "海南": "海口",
    "四川": "成都", "贵州": "贵阳", "云南": "昆明", "陕西": "西安",
    "甘肃": "兰州", "青海": "西宁", "台湾": "台北",
    "内蒙古": "呼和浩特", "广西": "南宁", "西藏": "拉萨",
    "宁夏": "银川", "新疆": "乌鲁木齐",
}

# 浙江：省台 → 宁波 → 杭州 → 舟山 → 其余
ZHEJIANG_CITY_PRIORITY = (
    ("宁波", 1),
    ("杭州", 2),
    ("舟山", 3),
)

PROVINCIAL_STATION_KW = (
    "卫视", "省级", "新闻综合", "综合频道",
    "钱江", "经视", "民生休闲", "之江纪录", "教科影视",
)


def _natural_key(text):
    return tuple(int(p) if p.isdigit() else p.lower()
                 for p in re.split(r"(\d+)", str(text)))


try:
    from pypinyin import lazy_pinyin, Style

    def pinyin_key(text: str):
        return tuple(lazy_pinyin(str(text), style=Style.NORMAL))
except ImportError:
    def pinyin_key(text: str):
        return _natural_key(text)


def _is_provincial_station(name: str, prov: str) -> bool:
    n = str(name)
    if any(kw in n for kw in PROVINCIAL_STATION_KW):
        return True
    if prov and n.startswith(prov):
        capital = PROVINCE_CAPITAL.get(prov, "")
        if capital and capital in n and prov not in ("北京", "上海", "天津", "重庆"):
            return False
        return True
    return False


def _local_name_rank(group: str, name: str):
    """地方>省 内排序。"""
    g = str(group)
    n = str(name)
    if not g.startswith("地方>"):
        return (50, pinyin_key(n))

    prov = g.split(">", 1)[-1].strip()

    # 浙江：省台 → 宁波 → 杭州 → 舟山 → 其它
    if prov == "浙江":
        if _is_provincial_station(n, "浙江") and not any(
            c in n for c, _ in ZHEJIANG_CITY_PRIORITY
        ):
            return (0, pinyin_key(n))
        for city, pri in ZHEJIANG_CITY_PRIORITY:
            if city in n:
                return (pri, pinyin_key(n))
        return (10, pinyin_key(n))

    # 其他省：省台 → 省会 → 其它
    capital = PROVINCE_CAPITAL.get(prov, "")
    if _is_provincial_station(n, prov) and not (capital and capital in n):
        return (0, pinyin_key(n))
    if capital and capital in n:
        return (1, pinyin_key(n))
    return (10, pinyin_key(n))


def _group_rank(group):
    g = str(group)
    for i, kw in enumerate(GROUP_ORDER):
        if kw in g:
            return i
    return len(GROUP_ORDER)


def _name_rank(name):
    m = re.search(r"(?i)CCTV[\s\-]?(\d+)(\+)?", str(name))
    if m:
        return (0, int(m.group(1)), 1 if m.group(2) else 0) + _natural_key(name)
    return (1, 0, 0) + _natural_key(name)


def channel_sort_key(ch):
    group = _safe_text(ch.get("group", ""))
    name = ch.get("name", "")
    return (
        _group_rank(group),
        group,
        _local_name_rank(group, name),
        _name_rank(name),
    )




def generate_m3u(channels, conf=None):
    conf = conf or {}
    lines = ['#EXTM3U']
    channels = sorted(channels, key=channel_sort_key)
    for ch in channels:
        g = _safe_text(ch["group"])
        lines.append('#EXTINF:-1 group-title="%s",%s'
                     % (g, _safe_text(_display_name(ch, conf))))
        lines.append(ch["url"])
    return "\n".join(lines)


def generate_txt(channels, conf=None):
    conf = conf or {}
    lines = []
    channels = sorted(channels, key=channel_sort_key)
    current = None
    for ch in channels:
        group = _safe_text(ch["group"])
        if group != current:
            lines.append('%s,#genre#' % group)
            current = group
        lines.append('%s,%s' % (_safe_text(_display_name(ch, conf)), ch["url"]))
    return "\n".join(lines)


def write_proxy_report(channels, path):
    rows = [ch for ch in channels if ch.get("via_proxy")]
    lines = [
        "# 以下频道的直连速度低于阈值，只有走代理补测才达到阈值",
        "# 这些源在你的电视/盒子上直连大概率放不出来，仅作参考",
        "# 频道名 | 直连kbps | 代理kbps | URL",
    ]
    for ch in sorted(rows, key=lambda x: x["name"]):
        lines.append("%s | %s | %s | %s" % (
            ch["name"], ch.get("direct_kbps", 0), ch.get("proxy_kbps", 0), ch["url"]))
    atomic_write(path, "\n".join(lines) + "\n")
    return len(rows)


def atomic_write(path, text):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ====================== 代理体检 ======================
TEST_URL = "https://cdn.jsdelivr.net/gh/luochen1975/2026myvt2@main/output/iptv.txt"


async def check_proxy_cmd(conf):
    proxy = conf.get("proxy")
    print("=== 代理体检 ===")
    print("目标: %s" % TEST_URL)
    print("代理: %s" % (proxy if proxy else "未配置"))
    async with aiohttp.ClientSession(trust_env=False) as session:
        try:
            t0 = time.perf_counter()
            async with session.get(TEST_URL, timeout=aiohttp.ClientTimeout(total=15),
                                   ssl=False, proxy=None) as r:
                n = len(await r.read())
            print("[直连] HTTP %d，%d 字节，%.2fs → %s"
                  % (r.status, n, time.perf_counter() - t0, "可用" if r.status == 200 and n else "异常"))
        except Exception as e:
            print("[直连] %s: %s" % (type(e).__name__, e))

        if not proxy:
            print("\n结论: 没配代理，拉取和测速都走直连。")
            return

        try:
            t0 = time.perf_counter()
            async with session.get(TEST_URL, timeout=aiohttp.ClientTimeout(total=15),
                                   ssl=False, proxy=proxy) as r:
                n = len(await r.read())
            print("[代理] HTTP %d，%d 字节，%.2fs → %s"
                  % (r.status, n, time.perf_counter() - t0, "不需要账号密码" if r.status == 200 else "异常"))
            if r.status == 200:
                print("\n结论: 代理可用且【不需要】账号密码。")
            elif r.status == 407:
                print("\n结论: 代理【需要】账号密码。")
        except Exception as e:
            status = getattr(e, "status", None)
            if status == 407:
                print("[代理] 407 Proxy Authentication Required")
                print("\n结论: 代理【需要】账号密码。两种改法：")
                print("  A) rules.yaml 里写 http://用户名:密码@127.0.0.1:7890（特殊字符先 URL 编码）")
                print("  B) mihomo 配置里把 authentication 清空后重启，端口就不再要密码")
            else:
                print("[代理] %s: %s" % (type(e).__name__, e))
                print("\n结论: 连不上代理。可能没启动，或端口不是 HTTP/mixed 端口。")


# ====================== 拉取 ======================
async def fetch_sources(urls, conf):
    all_channels = []
    proxy = conf.get("proxy_fetch")
    print("拉取方式: %s" % ("代理 %s" % proxy if proxy else "直连"))
    async with aiohttp.ClientSession(trust_env=False) as session:
        for url in urls:
            print("拉取: %s" % url)
            content = None
            for attempt in (1, 2):
                ssl_opt = True
                for _try in range(2):
                    try:
                        async with session.get(
                            url,
                            timeout=aiohttp.ClientTimeout(total=20),
                            proxy=proxy,
                            ssl=ssl_opt,
                            allow_redirects=True,
                        ) as resp:
                            if resp.status >= 400:
                                if resp.status == 407 and proxy:
                                    print("  [代理需要认证] 407：%s" % proxy)
                                    print("  先跑 --check-proxy 确认，或把 fetch_via_proxy 关掉走直连")
                                    return all_channels
                                print("  HTTP %d" % resp.status)
                                break
                            raw = await resp.read()
                        for enc in ("utf-8", "gb18030"):
                            try:
                                content = raw.decode(enc)
                                break
                            except UnicodeDecodeError:
                                content = None
                        break
                    except Exception as e:
                        status = getattr(e, "status", None)
                        if status == 407:
                            print("  [代理需要认证] 407：%s" % proxy)
                            print("  请跑 --check-proxy，或把 fetch_via_proxy 关掉")
                            return all_channels
                        if ssl_opt and ("SSL" in type(e).__name__ or "Certificate" in str(e)):
                            ssl_opt = False
                            continue
                        print("  第 %d 次失败: %s: %s" % (attempt, type(e).__name__, e))
                        break
                if content:
                    break
                if attempt == 1:
                    await asyncio.sleep(1)
            if not content:
                print("  → 放弃该源")
                continue
            chs = parse_content(content)
            print("  → %d 个" % len(chs))
            all_channels.extend(chs)
    return all_channels


# ====================== 主流程 ======================
def _arg_value(argv, key, default=None):
    if key in argv:
        i = argv.index(key)
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            return argv[i + 1]
    return default


async def main():
    print("=== 加载配置 ===")
    rules, user_settings = load_rules()
    blacklist = load_list(BLACKLIST_FILE)
    conf = get_runtime_config(user_settings)

    geo_enabled = bool(conf.get("auto_geo_group", False))
    geo_debug = bool(conf.get("debug_geo", False))
    geo_ok = False
    if geo_enabled:
        geo_ok = init_geo_auto_group(debug=geo_debug)

    print("\n=== 拉取源 ===")
    urls = load_list(SOURCES_FILE)
    if not urls:
        _die("%s 为空或不存在，每行写一个订阅地址" % SOURCES_FILE)
    all_channels = await fetch_sources(urls, conf)
    print("原始总计: %d" % len(all_channels))
    if not all_channels:
        _die("一条源都没拉到。先跑 --check-proxy 排查网络/代理，别去看测速")

    print("\n=== 黑名单剔除 ===")
    all_channels = filter_by_blacklist(all_channels, blacklist)
    print("黑名单后: %d" % len(all_channels))

    print("\n=== 应用重命名/分组规则 ===")
    all_channels = apply_rules(all_channels, rules)

    print("\n=== 执行地理分组 ===")
    apply_auto_geo_group(all_channels, geo_enabled)

    if geo_debug:
        print("\n=== 分组汇总（调试） ===")
        cnt_map = defaultdict(int)
        for c in all_channels:
            cnt_map[c["group"]] += 1
        for g, n in sorted(cnt_map.items(), key=lambda x: _group_rank(x[0])):
            print(f"  {g} : {n} 条")

    group_kw = _arg_value(sys.argv[1:], "--group", "") or ""
    limit = 0
    _lim = _arg_value(sys.argv[1:], "--limit")
    if _lim:
        try:
            limit = max(0, int(_lim))
        except ValueError:
            print("[WARN] --limit 后面要写整数，已忽略")

    if group_kw:
        all_channels = [c for c in all_channels if group_kw in str(c.get("group", ""))]
        print("按分组筛选「%s」后: %d" % (group_kw, len(all_channels)))
        if not all_channels:
            _die("分组「%s」筛完 0 条，检查关键字是否和 rules.yaml 里的 group 一致" % group_kw)

    sample_mode = bool(limit or group_kw)
    if limit and limit < len(all_channels):
        all_channels = random.Random(0).sample(all_channels, limit)
        print("抽样: 随机取 %d 条验证流程（去掉 --limit 即全量）" % limit)

    print("\n=== 测速阶段 ===")
    tested = await two_stage_test(all_channels, conf)

    keep_unicast = conf.get("keep_unicast", 6)
    keep_multicast = conf.get("keep_multicast", 4)
    print("\n=== 去重优选（非组播≤%s，组播≤%s） ===" % (keep_unicast, keep_multicast))
    final = dedup_and_select(
        tested,
        keep_unicast=keep_unicast,
        keep_multicast=keep_multicast,
        prefer_direct=bool(conf.get("prefer_direct", True)),
        keep_proxy_only=bool(conf.get("keep_proxy_only", True)),
    )
    print("最终保留: %d" % len(final))

    n_proxy_only = len([c for c in final if c.get("via_proxy")])
    if n_proxy_only:
        print("其中【仅代理可通】%d 条（名字带〔需代理〕，另见 output/仅代理可用.txt）" % n_proxy_only)

    if not final:
        _die("最终 0 条，拒绝覆盖已有输出（旧文件保持不动）", code=3)

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _die("输出目录不可写：%s\n%s\n（软路由 /usr 只读时，把项目放到 /root 或 /mnt）" % (OUTPUT_DIR, e))

    if conf["mode"] == "skip":
        m3u_path, txt_path = OUTPUT_DIR / "iptv.m3u", OUTPUT_DIR / "iptv.txt"
    else:
        m3u_path, txt_path = OUTPUT_DIR / "移动iptv.m3u", OUTPUT_DIR / "移动iptv.txt"
    report_path = OUTPUT_DIR / "仅代理可用.txt"
    if sample_mode:
        m3u_path = m3u_path.with_name(m3u_path.stem + "_抽样" + m3u_path.suffix)
        txt_path = txt_path.with_name(txt_path.stem + "_抽样" + txt_path.suffix)
        report_path = report_path.with_name(report_path.stem + "_抽样" + report_path.suffix)

    try:
        atomic_write(m3u_path, generate_m3u(final, conf))
        atomic_write(txt_path, generate_txt(final, conf))
        if n_proxy_only:
            write_proxy_report(final, report_path)
            print("已生成: %s" % report_path)
        elif report_path.exists():
            report_path.unlink()
    except PermissionError as e:
        _die("没有写权限：%s（软路由 /usr 只读，换到 /root 或 /mnt）" % e)

    print("\n已生成: %s" % m3u_path)
    print("已生成: %s" % txt_path)


if __name__ == "__main__":
    try:
        if "--check-proxy" in sys.argv[1:]:
            rules, user_settings = load_rules()
            conf = get_runtime_config(user_settings)
            asyncio.run(check_proxy_cmd(conf))
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已中断")
    except SystemExit:
        raise
    except Exception:
        print("\n[FATAL] 未捕获异常：", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
