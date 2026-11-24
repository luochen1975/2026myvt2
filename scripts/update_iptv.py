#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IPTV 分组修复版 - 版本4.1 内置关键词地理分组
变更：
- 移除 pca-code.json 在线下载，改用内置省+地级市关键词表
- 跳过测速源(special_keep)与组播分桶，避免被 keep_multicast 挤掉
- 地理：地方>省 / 地方2>省县市区 / 未分类
"""

import asyncio
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


# ====================== 【内置地理关键词表】 ======================
GEO_PROV_CITY = {
    "浙江省": {
        "alias": ["浙江"],
        "city_alias": ["杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水"],
    },
    "江苏省": {
        "alias": ["江苏"],
        "city_alias": ["南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁"],
    },
    "上海市": {"alias": ["上海"], "city_alias": []},
    "北京市": {"alias": ["北京"], "city_alias": []},
    "广东省": {
        "alias": ["广东"],
        "city_alias": [
            "广州", "深圳", "珠海", "汕头", "佛山", "韶关", "湛江", "肇庆", "江门", "茂名",
            "惠州", "梅州", "汕尾", "河源", "阳江", "清远", "东莞", "中山", "潮州", "揭阳", "云浮",
        ],
    },
    "山东省": {
        "alias": ["山东"],
        "city_alias": [
            "济南", "青岛", "淄博", "枣庄", "东营", "烟台", "潍坊", "济宁", "泰安", "威海",
            "日照", "临沂", "德州", "聊城", "滨州", "菏泽",
        ],
    },
    "河南省": {
        "alias": ["河南"],
        "city_alias": [
            "郑州", "开封", "洛阳", "平顶山", "安阳", "鹤壁", "新乡", "焦作", "濮阳", "许昌",
            "漯河", "三门峡", "南阳", "商丘", "信阳", "周口", "驻马店",
        ],
    },
    "四川省": {
        "alias": ["四川"],
        "city_alias": [
            "成都", "自贡", "攀枝花", "泸州", "德阳", "绵阳", "广元", "遂宁", "内江", "乐山",
            "南充", "眉山", "宜宾", "广安", "达州", "雅安", "巴中", "资阳",
        ],
    },
    "内蒙古自治区": {
        "alias": ["内蒙", "内蒙古"],
        "city_alias": ["呼和浩特", "包头", "乌海", "赤峰", "通辽", "鄂尔多斯", "呼伦贝尔", "巴彦淖尔", "乌兰察布"],
    },
    "新疆维吾尔自治区": {
        "alias": ["新疆"],
        "city_alias": ["乌鲁木齐", "克拉玛依", "吐鲁番", "哈密"],
    },
    "安徽省": {
        "alias": ["安徽"],
        "city_alias": [
            "合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州",
            "阜阳", "宿州", "六安", "亳州", "池州", "宣城",
        ],
    },
    "陕西省": {
        "alias": ["陕西"],
        "city_alias": ["西安", "宝鸡", "咸阳", "铜川", "渭南", "延安", "汉中", "榆林", "安康", "商洛"],
    },
    "河北省": {
        "alias": ["河北"],
        "city_alias": ["石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德", "沧州", "廊坊", "衡水"],
    },
    "辽宁省": {
        "alias": ["辽宁"],
        "city_alias": [
            "沈阳", "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州", "营口", "阜新", "辽阳",
            "盘锦", "铁岭", "朝阳", "葫芦岛",
        ],
    },
    "湖北省": {
        "alias": ["湖北"],
        "city_alias": ["武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州"],
    },
    "吉林省": {
        "alias": ["吉林"],
        "city_alias": ["长春", "吉林市", "四平", "辽源", "通化", "白山", "松原", "白城"],
    },
    "广西壮族自治区": {
        "alias": ["广西"],
        "city_alias": [
            "南宁", "柳州", "桂林", "梧州", "北海", "防城港", "钦州", "贵港", "玉林", "百色",
            "贺州", "河池", "来宾", "崇左",
        ],
    },
    "海南省": {"alias": ["海南"], "city_alias": ["海口", "三亚", "三沙", "儋州"]},
    "云南省": {
        "alias": ["云南"],
        "city_alias": [
            "昆明", "曲靖", "玉溪", "保山", "昭通", "丽江", "普洱", "临沧", "楚雄", "红河",
            "文山", "西双版纳", "大理", "德宏", "怒江", "迪庆",
        ],
    },
    "天津市": {"alias": ["天津"], "city_alias": []},
    "湖南省": {
        "alias": ["湖南"],
        "city_alias": [
            "长沙", "株洲", "湘潭", "衡阳", "邵阳", "岳阳", "常德", "张家界", "益阳", "郴州",
            "永州", "怀化", "娄底",
        ],
    },
    "山西省": {
        "alias": ["山西"],
        "city_alias": ["太原", "大同", "阳泉", "长治", "晋城", "朔州", "晋中", "运城", "忻州", "临汾", "吕梁"],
    },
    "宁夏回族自治区": {
        "alias": ["宁夏"],
        "city_alias": ["银川", "石嘴山", "吴忠", "固原", "中卫"],
    },
    "甘肃省": {
        "alias": ["甘肃"],
        "city_alias": [
            "兰州", "嘉峪关", "金昌", "白银", "天水", "武威", "张掖", "平凉", "酒泉", "庆阳",
            "定西", "陇南",
        ],
    },
    "福建省": {
        "alias": ["福建"],
        "city_alias": ["福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩", "宁德"],
    },
    "江西省": {
        "alias": ["江西"],
        "city_alias": ["南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶"],
    },
    "贵州省": {
        "alias": ["贵州"],
        "city_alias": ["贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁", "黔东南", "黔南", "黔西南"],
    },
    "黑龙江省": {
        "alias": ["黑龙江"],
        "city_alias": [
            "哈尔滨", "齐齐哈尔", "鸡西", "鹤岗", "双鸭山", "大庆", "伊春", "佳木斯", "七台河",
            "牡丹江", "黑河", "绥化",
        ],
    },
    "青海省": {"alias": ["青海"], "city_alias": ["西宁"]},
    "西藏自治区": {"alias": ["西藏"], "city_alias": ["拉萨"]},
    "重庆市": {"alias": ["重庆"], "city_alias": []},
}

COUNTY_MARK = ("区", "县", "自治县", "县级市", "特区", "林区", "旗", "自治旗")
geo_debug = False


def _short_prov(prov_full: str) -> str:
    s = str(prov_full)
    for suffix in ("维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "省", "市"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s


def classify_geo_channel(channel_name: str) -> str:
    """省台/地市 → 地方>省；明显县级 → 地方2>省县市区；否则未分类。"""
    name = str(channel_name)
    hit_prov_full = None
    has_city_hit = False
    has_county_hit = any(k in name for k in COUNTY_MARK)

    for prov_full, info in GEO_PROV_CITY.items():
        if any(alias in name for alias in info["alias"]):
            hit_prov_full = prov_full
            if any(city in name for city in info.get("city_alias") or []):
                has_city_hit = True
            break

    if hit_prov_full is None:
        return "未分类"

    short = _short_prov(hit_prov_full)
    # 仅当出现县/区/旗等，且不是「城区」类误伤时可再收紧；有地级市名仍归地方>
    if has_county_hit and not has_city_hit:
        return "地方2>%s县市区" % short
    return "地方>%s" % short


def init_geo_auto_group(debug=False) -> bool:
    global geo_debug
    geo_debug = debug
    print("[GEO-INFO] 使用内置关键词地理分组，无需下载外部 json")
    return True


def apply_auto_geo_group(channels, enabled: bool):
    if not enabled:
        print("[GEO-INFO] 自动地理分组配置关闭，跳过")
        return
    for ch in channels:
        g_orig = ch.get("group", "")
        if g_orig != "未分类":
            if geo_debug:
                print("[GEO-SKIP] %s 已有分组 %s，不覆盖" % (ch["name"], g_orig))
            continue
        new_group = classify_geo_channel(ch["name"])
        ch["group"] = new_group
        if geo_debug:
            print("[GEO-MATCH] %s → %s" % (ch["name"], new_group))


# ====================== 不测速直接保留 ======================
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
    "cdn3.123.rr.kg",
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


# ====================== 路径 ======================
def locate_root():
    try:
        here = Path(__file__).resolve()
    except NameError:
        _die("检测不到 __file__，请把脚本存成文件再执行")
    for c in (here.parent.parent, here.parent, Path.cwd()):
        if (c / "config").is_dir():
            return c
    return here.parent.parent


ROOT = locate_root()
CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"
SOURCES_FILE = CONFIG_DIR / "sources.txt"
RULES_FILE = CONFIG_DIR / "rules.yaml"
BLACKLIST_FILE = CONFIG_DIR / "blacklist.txt"


def load_list(path):
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip() and not line.startswith("#")
    ]


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
                "action": (r.get("action") or "").strip().lower(),
            })
        except re.error as e:
            print("[WARN] rules[%d] 正则 %r 编译失败：%s，跳过" % (idx, r.get("filter"), e))

    settings = data.get("settings") or {}
    if not isinstance(settings, dict):
        settings = {}
    if "force_mode" not in settings and data.get("force_mode") is not None:
        settings["force_mode"] = data.get("force_mode")
    return rules, settings


# ====================== 环境 ======================
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
    "github": {
        "mode": "skip", "concurrency": 20, "timeout": 8, "enable_ffprobe": False,
        "request_delay": 0.05, "keep_unicast": 8, "keep_multicast": 5,
        "keep_skip_speed": 8, "auto_geo_group": False, "debug_geo": False,
    },
    "router": {
        "mode": "smart", "concurrency": 6, "timeout": 8, "enable_ffprobe": False,
        "request_delay": 0.25, "keep_unicast": 5, "keep_multicast": 3,
        "keep_skip_speed": 8, "auto_geo_group": False, "debug_geo": False,
    },
    "pc": {
        "mode": "smart", "concurrency": 16, "timeout": 6, "enable_ffprobe": True,
        "request_delay": 0.1, "keep_unicast": 6, "keep_multicast": 4,
        "keep_skip_speed": 8, "auto_geo_group": False, "debug_geo": False,
    },
}


def _clean_proxy(value):
    v = str(value or "").strip()
    if not v:
        return None
    if not v.lower().startswith(("http://", "https://")):
        print("[WARN] 代理 %r 不是 http:// 或 https:// 开头，已忽略" % v)
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
        print("[WARN] 未知 force_mode: %r，按环境默认" % force_mode)

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
    conf.setdefault("keep_skip_speed", 8)

    mode_desc = {
        "skip": "跳过测速（GitHub Actions / 快速模式）",
        "direct": "纯直连测速",
        "smart": "智能测速（先直连，不足再代理）",
    }
    print("运行模式: %s → %s" % (conf["mode"], mode_desc.get(conf["mode"], conf["mode"])))
    print("拉取订阅: %s" % ("经代理 %s" % fetch_proxy if fetch_proxy else "直连"))
    print("测速补测代理: %s" % (probe_proxy if probe_proxy else "无"))
    print("自动地理分组: %s | geo调试: %s" % (
        "开启" if conf.get("auto_geo_group") else "关闭",
        "开启" if conf.get("debug_geo") else "关闭",
    ))
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
                    "url": parts[1].strip().split("#", 1)[0].split("$", 1)[0].strip(),
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
            if not rule["pattern"].search(name):
                continue
            if rule.get("action") == "reject":
                matched = True
                break
            rep = rule.get("replace")
            new_name = name
            if rep is not None:
                if isinstance(rep, dict):
                    new_name = name
                    for a, b in rep.items():
                        new_name = new_name.replace(str(a), str(b))
                    new_name = new_name.strip() or name
                elif isinstance(rep, (list, tuple)) and len(rep) == 2:
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
        # 仍标记 skip 关键词，便于去重时优先保留
        for ch in channels:
            if should_skip_speed_test(ch["url"]):
                ch["special_keep"] = True
                ch["speed_kbps"] = 99999
                ch["latency_ms"] = 0
                ch["ok"] = True
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

    print("测速参数 → 并发:%s | 超时:%ss | 最低速度:%skbps | 间隔:%ss" % (
        concurrency, timeout, min_speed, request_delay))
    print("FFprobe: %s" % ("开启" if enable_ffprobe else "关闭"))
    if mode == "smart":
        print("策略: 直连不足 %skbps 时，再用代理补测一次" % min_speed)

    sem = asyncio.Semaphore(concurrency)
    light_passed = []

    async def light_worker(ch):
        if should_skip_speed_test(ch["url"]):
            ch.update({
                "latency_ms": 0, "speed_kbps": 99999, "ok": True,
                "special_keep": True, "via_proxy": False,
            })
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
                print("  进度 %d/%d | 直连 %d | 仅代理 %d | 跳过测速 %d | 不通 %d" % (
                    STATS["done"], total, STATS["direct_ok"], STATS["proxy_only"],
                    STATS["special"], STATS["dead"]))

    connector = aiohttp.TCPConnector(
        limit=concurrency * 2, ssl=False, ttl_dns_cache=300,
        force_close=True, enable_cleanup_closed=True,
    )
    async with aiohttp.ClientSession(connector=connector, trust_env=False) as session:
        await asyncio.gather(*(light_worker(ch) for ch in channels))

    print("初筛通过: %d / %d （直连 %d | 仅代理 %d | 跳过测速 %d | 不通 %d）" % (
        len(light_passed), total, STATS["direct_ok"], STATS["proxy_only"],
        STATS["special"], STATS["dead"]))

    if not enable_ffprobe or not light_passed:
        return light_passed
    if not shutil.which("ffprobe"):
        print("[WARN] 未找到 ffprobe，深度检测跳过")
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
            res = await loop.run_in_executor(
                None, ffprobe_check, ch["url"], ffprobe_timeout, use_proxy)
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


def dedup_and_select(channels, keep_unicast=6, keep_multicast=4, keep_skip_speed=8,
                     prefer_direct=True, keep_proxy_only=True):
    """special_keep（跳过测速）与组播分桶，避免被 keep_multicast 挤掉。"""
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
        unicast, multicast, forced = [], [], []
        for ch in items:
            if not keep_proxy_only and ch.get("via_proxy"):
                continue
            url = ch.get("url", "")
            # 真组播
            if is_multicast(url):
                multicast.append(ch)
            # 跳过测速但非组播（如 cdn.qd.je / indevs）
            elif ch.get("special_keep"):
                forced.append(ch)
            else:
                unicast.append(ch)

        unicast.sort(key=rank)
        multicast.sort(key=rank)
        forced.sort(key=rank)

        final.extend(forced[:keep_skip_speed])
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


GROUP_ORDER = [
    "央视",
    "卫视",
    "港澳台",
    "地方>",
    "地方2>",
    "精彩",
    "其他>体育",
    "其他>纪录",
    "其他>少儿",
    "其他>音乐",
    "其他>广播",
    "其他>NewTviHOT",
    "其他>精彩",
    "其他>影视",
    "其他>新闻",
    "其他>戏曲",
    "其他>生活",
    "其他>教育",
    "其他>军事",
    "其他",
    "未分类",
]

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

ZHEJIANG_CITY_PRIORITY = (("宁波", 1), ("杭州", 2), ("舟山", 3))

PROVINCIAL_STATION_KW = (
    "卫视", "省级", "新闻综合", "综合频道",
    "钱江", "经视", "民生休闲", "之江纪录", "教科影视",
)


def _natural_key(text):
    return tuple(
        int(p) if p.isdigit() else p.lower()
        for p in re.split(r"(\d+)", str(text))
    )


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
    """仅处理 地方> / 地方2>：省台 → 省会 → 其它拼音。其它分组不干预。"""
    g = str(group)
    n = str(name)
    if not g.startswith(("地方>", "地方2>")):
        return (0,)  # 不抢在 CCTV 台号排序前面

    if g.startswith("地方2>"):
        prov = g.split(">", 1)[-1].replace("县市区", "").strip()
    else:
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

    # 其它省：省台 → 省会 → 其它拼音
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
    """
    央视：
      CCTV-1 … 17（含 4 欧洲/美洲/亚洲、5+）
      再：CGTN、文化精品、高尔夫等
    """
    n = str(name)

    m = re.search(r"(?i)(?:CCTV|央视|中央电视台)[\s\-]*(\d+)(\+)?", n)
    if m:
        num = int(m.group(1))
        is_plus = 1 if m.group(2) else 0
        # 同一台号内：默认/中文 → 欧洲 → 美洲 → 亚洲 → 4K
        if re.search(r"4K|超高清", n, re.I):
            variant = 4
        elif "欧洲" in n:
            variant = 1
        elif "美洲" in n or "北美" in n:
            variant = 2
        elif "亚洲" in n:
            variant = 3
        else:
            variant = 0
        return (0, num, is_plus, variant)

    if re.search(r"(?i)CGTN", n):
        return (1, 0, 0, 0) + _natural_key(n)
    if re.search(r"(?i)(?:CCTV|央视|中央电视台|CETV|中国教育|中央新影)", n):
        return (1, 1, 0, 0) + _natural_key(n)

    return (2, 0, 0, 0) + pinyin_key(n)


def channel_sort_key(ch):
    group = _safe_text(ch.get("group", ""))
    name = ch.get("name", "")
    # 大类 → 组名 →（地方内部）→ 台名（央视台号 / 拼音）
    return (
        _group_rank(group),
        group,
        _local_name_rank(group, name),
        _name_rank(name),
    )


def generate_m3u(channels, conf=None):
    conf = conf or {}
    lines = ["#EXTM3U"]
    channels = sorted(channels, key=channel_sort_key)
    for ch in channels:
        g = _safe_text(ch["group"])
        lines.append('#EXTINF:-1 group-title="%s",%s' % (g, _safe_text(_display_name(ch, conf))))
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
            lines.append("%s,#genre#" % group)
            current = group
        lines.append("%s,%s" % (_safe_text(_display_name(ch, conf)), ch["url"]))
    return "\n".join(lines)


def write_proxy_report(channels, path):
    rows = [ch for ch in channels if ch.get("via_proxy")]
    lines = [
        "# 仅代理可通的源",
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
            async with session.get(
                TEST_URL, timeout=aiohttp.ClientTimeout(total=15), ssl=False, proxy=None
            ) as r:
                n = len(await r.read())
            print("[直连] HTTP %d，%d 字节，%.2fs" % (r.status, n, time.perf_counter() - t0))
        except Exception as e:
            print("[直连] %s: %s" % (type(e).__name__, e))

        if not proxy:
            print("\n结论: 未配置代理。")
            return

        try:
            t0 = time.perf_counter()
            async with session.get(
                TEST_URL, timeout=aiohttp.ClientTimeout(total=15), ssl=False, proxy=proxy
            ) as r:
                n = len(await r.read())
            print("[代理] HTTP %d，%d 字节，%.2fs" % (r.status, n, time.perf_counter() - t0))
            if r.status == 407:
                print("结论: 代理需要账号密码。")
            elif r.status == 200:
                print("结论: 代理可用。")
        except Exception as e:
            print("[代理] %s: %s" % (type(e).__name__, e))


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
                                    print("  [代理需要认证] 407")
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
                            print("  [代理需要认证] 407")
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
    if geo_enabled:
        init_geo_auto_group(debug=geo_debug)

    print("\n=== 拉取源 ===")
    urls = load_list(SOURCES_FILE)
    if not urls:
        _die("%s 为空或不存在" % SOURCES_FILE)
    all_channels = await fetch_sources(urls, conf)
    print("原始总计: %d" % len(all_channels))
    if not all_channels:
        _die("一条源都没拉到")

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
        for g, n in sorted(cnt_map.items(), key=lambda x: (_group_rank(x[0]), x[0])):
            print("  %s : %d 条" % (g, n))

    group_kw = _arg_value(sys.argv[1:], "--group", "") or ""
    limit = 0
    _lim = _arg_value(sys.argv[1:], "--limit")
    if _lim:
        try:
            limit = max(0, int(_lim))
        except ValueError:
            print("[WARN] --limit 需为整数")

    if group_kw:
        all_channels = [c for c in all_channels if group_kw in str(c.get("group", ""))]
        print("按分组筛选「%s」后: %d" % (group_kw, len(all_channels)))
        if not all_channels:
            _die("分组筛选结果为 0")

    sample_mode = bool(limit or group_kw)
    if limit and limit < len(all_channels):
        all_channels = random.Random(0).sample(all_channels, limit)
        print("抽样: %d 条" % limit)

    print("\n=== 测速阶段 ===")
    tested = await two_stage_test(all_channels, conf)

    keep_unicast = int(conf.get("keep_unicast", 6))
    keep_multicast = int(conf.get("keep_multicast", 4))
    keep_skip_speed = int(conf.get("keep_skip_speed", 8))
    print("\n=== 去重优选（单播≤%s，组播≤%s，跳过测速≤%s） ===" % (
        keep_unicast, keep_multicast, keep_skip_speed))
    final = dedup_and_select(
        tested,
        keep_unicast=keep_unicast,
        keep_multicast=keep_multicast,
        keep_skip_speed=keep_skip_speed,
        prefer_direct=bool(conf.get("prefer_direct", True)),
        keep_proxy_only=bool(conf.get("keep_proxy_only", True)),
    )
    print("最终保留: %d" % len(final))

    n_proxy_only = len([c for c in final if c.get("via_proxy")])
    if n_proxy_only:
        print("其中仅代理可通: %d" % n_proxy_only)

    if not final:
        _die("最终 0 条，拒绝覆盖输出", code=3)

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _die("输出目录不可写: %s" % e)

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
        _die("没有写权限: %s" % e)

    print("\n已生成: %s" % m3u_path)
    print("已生成: %s" % txt_path)


if __name__ == "__main__":
    try:
        if "--check-proxy" in sys.argv[1:]:
            _rules, user_settings = load_rules()
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
