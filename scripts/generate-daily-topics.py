#!/usr/bin/env python3
"""
每日热门话题 - TikHub + DeepSeek enrich
每天由 GitHub Actions 调用：
  1) 通过 TikHub API 搜各平台 → 拉到候选条目
  2) 跨主题/跨平台去重 → 留下不重复的选题池
  3) 调 DeepSeek 给每条产出：摘要 / 爆点 / 业务相关度 / 再创作角度
  4) 按平台百分位归一化热度
  5) 渲染成 HTML 报告供编辑选题
"""

import os
import re
import json
import time
import bisect
import requests
from datetime import datetime, timezone, timedelta

# ── 配置 ──────────────────────────────────────────────
BEIJING_TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(BEIJING_TZ)
DATE_STR = TODAY.strftime("%Y-%m-%d")
DATE_CN = TODAY.strftime("%Y年%-m月%-d日")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETAIL_FILE = os.path.join(PROJECT_ROOT, "daily-topics.html")

TIKHUB_API_KEY = os.environ.get("TIKHUB_API_KEY", "")
TIKHUB_BASE = "https://api.tikhub.io"

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-pro"

# 一周前的时间戳：关键词搜索只保留最近 7 天发布的帖子
ONE_WEEK_AGO = int((TODAY - timedelta(days=7)).timestamp())

HEADERS = {
    "Authorization": f"Bearer {TIKHUB_API_KEY}",
    "Content-Type": "application/json",
}

# ── 话题/平台配置 ─────────────────────────────────────
# 所有平台列表
ALL_PLATFORMS = ["wechat_channels", "xigua", "bilibili", "douyin", "xiaohongshu"]

TOPICS = [
    {
        "name": "香港保险/分红险",
        "icon": "🛡️",
        "color": "#EC4899",
        "keywords": ["香港保险", "分红险"],
    },
    {
        "name": "内地保险/年金/增额寿",
        "icon": "🏛️",
        "color": "#0EA5E9",
        "keywords": ["内地保险", "快返年金", "增额终身寿险", "商业养老金"],
    },
    {
        "name": "存钱",
        "icon": "💰",
        "color": "#F59E0B",
        "keywords": ["存钱"],
    },
    {
        "name": "存款/定存/国债",
        "icon": "🏦",
        "color": "#6366F1",
        "keywords": ["存款", "定存", "国债"],
    },
    {
        "name": "养老",
        "icon": "🏡",
        "color": "#10B981",
        "keywords": ["养老"],
    },
]

# 各平台筛选标准
PLATFORM_FILTERS = {
    # 视频号点赞量整体偏低(几十级), 门槛设低些避免误杀; 主要靠相关度+近3月+护栏
    "wechat_channels": lambda item: item.get("like", 0) >= 10,
    "xigua":       lambda item: item.get("play", 0) >= 50000,
    "bilibili":    lambda item: item.get("play", 0) >= 10000,
    "douyin":      lambda item: item.get("like", 0) >= 200,
    "xiaohongshu": lambda item: item.get("like", 0) >= 200,
}

PLATFORM_NAMES = {
    "wechat_channels": "视频号",
    "xigua": "西瓜视频",
    "bilibili": "B站",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
}

PLATFORM_COLORS = {
    "wechat_channels": "#FA9D3B",
    "xigua": "#F04142",
    "bilibili": "#00A1D6",
    "douyin": "#1A1A1A",
    "xiaohongshu": "#FF2442",
}

PLATFORM_TAG_CLASSES = {
    "wechat_channels": "tag-channels",
    "xigua": "tag-xigua",
    "bilibili": "tag-bili",
    "douyin": "tag-douyin",
    "xiaohongshu": "tag-xhs",
}

# 对标账号日更监控(第二期)。抖音用 sec_uid(必须走 app/v3 接口才新鲜)、小红书用 user_id。
# 2026-06-13: 曾误用 web 版抖音接口(数据滞后一周)致抖音号显示"昨日无更新", 用户误以为要删;
#   已改 app/v3 修复, 抖音号恢复。视频号账号监控因 TikHub user_search/home_page 接口损坏暂不可行。
MONITOR_ACCOUNTS = [
    {"name": "深蓝保",            "platform": "douyin",      "id": "MS4wLjABAAAAiQ4RY3tqs-dJydax0-MjYuFnEviabmS2Q5ttbsOAD38"},
    {"name": "保瓶儿",            "platform": "douyin",      "id": "MS4wLjABAAAA_SCf-XEttYRH0bJmPmeOhnVAHbOGWhG8vU1jK3gRTO8"},
    {"name": "奶爸保险测评",      "platform": "douyin",      "id": "MS4wLjABAAAAk8H3SHUDYbgzj7HR9RVX96ZAW0sJW7_R52QPOT_Qixs"},
    {"name": "保瓶儿聊产品",      "platform": "douyin",      "id": "MS4wLjABAAAAYVcTd31FsiRb23i2kzb28iT-YJiYxRUdtqxX4gyhNe4"},
    {"name": "保瓶儿养老规划",    "platform": "douyin",      "id": "MS4wLjABAAAAJOfIL3zfCgjMICU7lRi1j6sl3wjAAQtJ-TYhq6NjwuZP_LInia4rmJR71ehHmgWZ"},
    {"name": "紫荆保险规划",      "platform": "douyin",      "id": "MS4wLjABAAAAM30hondWMZnmUF7AX9X8Tl26NIJGAwF0l_l1zd2vFaE"},
    {"name": "Joy张老师保险规划", "platform": "xiaohongshu", "id": "60c07116000000000100abce"},
    {"name": "Mo姐财经",          "platform": "xiaohongshu", "id": "56cd13dd84edcd1ee0154361"},
]


# ── TikHub API 搜索 ──────────────────────────────────
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0  # 秒；每次失败后等 backoff * attempt 再重试


def _request_with_retry(method, url, **kwargs):
    """对 TikHub 接口做带退避的重试。仅对 5xx / 4xx(非401/403) / 网络异常重试。"""
    last_err = ""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = requests.request(method, url, timeout=30, **kwargs)
            if resp.status_code == 200:
                return resp
            # 401/403 是 token 问题，重试无意义
            if resp.status_code in (401, 403):
                print(f"  API auth error {resp.status_code}: {resp.text[:300]}")
                return None
            last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception as e:
            last_err = f"network: {e}"
        if attempt < RETRY_ATTEMPTS:
            wait = RETRY_BACKOFF * attempt
            print(f"  [retry {attempt}/{RETRY_ATTEMPTS - 1}] {last_err[:120]} -> sleep {wait}s")
            time.sleep(wait)
    print(f"  API failed after {RETRY_ATTEMPTS} attempts: {last_err[:300]}")
    return None


def tikhub_get(path, params=None):
    url = f"{TIKHUB_BASE}{path}"
    resp = _request_with_retry("GET", url, headers=HEADERS, params=params)
    if resp is None:
        return None
    try:
        data = resp.json()
    except Exception as e:
        print(f"  JSON parse failed: {e}")
        return None
    # 调试：打印响应结构的顶层 keys
    if isinstance(data, dict):
        print(f"    Response keys: {list(data.keys())}")
        d = data.get("data")
        if isinstance(d, dict):
            print(f"    data keys: {list(d.keys())[:10]}")
    return data


def extract_items(data, list_keys):
    """从嵌套的 API 响应中提取列表数据。
    list_keys: 尝试的 key 路径列表，按优先级排列。"""
    if not data or not isinstance(data, dict):
        return []
    d = data.get("data", data)
    if isinstance(d, dict):
        for key in list_keys:
            val = d.get(key)
            if isinstance(val, list) and val:
                print(f"    Found list in data.{key} ({len(val)} items)")
                if isinstance(val[0], dict):
                    print(f"    First item keys: {list(val[0].keys())[:15]}")
                    # 打印前100字符帮助调试
                    print(f"    First item preview: {json.dumps(val[0], ensure_ascii=False)[:200]}")
                return val
        # 递归查找第一个非空列表
        for key, val in d.items():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                print(f"    Found list in data.{key} ({len(val)} items)")
                print(f"    First item keys: {list(val[0].keys())[:15]}")
                print(f"    First item preview: {json.dumps(val[0], ensure_ascii=False)[:200]}")
                return val
    if isinstance(d, list):
        return d
    return []


def deep_get(d, *keys):
    """沿嵌套 dict 逐层取值，遇到非 dict 或 key 不存在则返回 {}。"""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, {})
        else:
            return {}
    return d


def search_xigua(keyword):
    """搜索西瓜视频
    响应结构: data.results[] -> 每个 item 有 data 子字段包含视频信息"""
    resp = tikhub_get("/api/v1/xigua/app/v2/search_video", {
        "keyword": keyword,
        "order_type": "play_count",
    })
    if not resp:
        return []
    raw_list = extract_items(resp, ["results", "data", "video_list"])
    items = []
    for item in raw_list[:15]:
        if not isinstance(item, dict):
            continue
        # 西瓜视频的实际数据在 item["data"] 里
        vdata = item.get("data", item)
        if isinstance(vdata, str):
            try:
                vdata = json.loads(vdata)
            except (json.JSONDecodeError, TypeError):
                vdata = item
        if not isinstance(vdata, dict):
            vdata = item
        title = (vdata.get("title") or vdata.get("video_title")
                 or vdata.get("content") or vdata.get("desc", ""))
        # 构建链接：优先 share_url，否则用 group_id 拼接
        url = vdata.get("share_url") or vdata.get("url") or vdata.get("article_url", "")
        group_id = vdata.get("group_id") or vdata.get("gid", "")
        if not url and group_id:
            url = f"https://www.ixigua.com/{group_id}"
        # 播放量：优先从 video_detail_info.video_watch_count 取
        vdi = vdata.get("video_detail_info", {})
        play = (vdi.get("video_watch_count", 0)
                or vdata.get("video_watch_count", 0)
                or vdata.get("play_count", 0))
        like = vdata.get("digg_count", 0) or vdata.get("like_count", 0)
        comment = vdata.get("comment_count", 0)
        create_time = vdata.get("create_time", 0) or vdata.get("publish_time", 0)
        # 如果还是没 title，尝试从 itemDataStr 解析
        if not title:
            ids = item.get("itemDataStr", "")
            if isinstance(ids, str) and ids:
                try:
                    ids_data = json.loads(ids)
                    title = ids_data.get("title") or ids_data.get("content", "")
                    url = url or ids_data.get("share_url", "")
                    if not url:
                        gid = ids_data.get("group_id") or ids_data.get("gid", "")
                        if gid:
                            url = f"https://www.ixigua.com/{gid}"
                    play = play or ids_data.get("play_count", 0)
                    create_time = create_time or ids_data.get("create_time", 0)
                except (json.JSONDecodeError, TypeError):
                    pass
        if title:
            items.append({"title": title, "url": url, "play": play, "like": like,
                          "comment": comment, "create_time": create_time})
    return items


def search_bilibili(keyword):
    """搜索B站
    响应结构: data -> {code, message, ttl, data} -> data 里有 result[]"""
    resp = tikhub_get("/api/v1/bilibili/web/fetch_general_search", {
        "keyword": keyword,
        "order": "totalrank",
        "page": 1,
        "page_size": 15,
    })
    if not resp:
        return []
    # B站的嵌套：TikHub.data -> Bilibili.{data:{result:[]}}
    d = resp.get("data", {})
    if isinstance(d, dict) and "data" in d:
        inner = d.get("data", {})
        if isinstance(inner, dict):
            raw_list = inner.get("result", [])
            if not raw_list:
                # 可能是 inner 直接就是列表
                for v in inner.values():
                    if isinstance(v, list) and v:
                        raw_list = v
                        break
            print(f"    Bilibili inner data keys: {list(inner.keys())[:10]}")
        else:
            raw_list = []
    else:
        raw_list = extract_items(resp, ["result", "data", "items"])
    items = []
    for item in raw_list[:15]:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or item.get("name") or "")
        title = title.replace('<em class="keyword">', '').replace('</em>', '')
        bvid = item.get("bvid", "")
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else item.get("arcurl", "")
        play = item.get("play", 0) or item.get("view", 0)
        like = item.get("like", 0) or item.get("favorites", 0)
        create_time = item.get("pubdate", 0) or item.get("senddate", 0)
        if title and item.get("type", "") != "bili_user":
            items.append({"title": title, "url": url, "play": play, "like": like,
                          "create_time": create_time})
    return items


def search_douyin(keyword):
    """搜索抖音（使用 POST /douyin/search/fetch_general_search_v2）"""
    url = f"{TIKHUB_BASE}/api/v1/douyin/search/fetch_general_search_v2"
    resp = _request_with_retry("POST", url, headers=HEADERS, json={
        "keyword": keyword,
        "offset": 0,
        "count": 15,
        "sort_type": "0",
    })
    if resp is None:
        return []
    try:
        data = resp.json()
    except Exception as e:
        print(f"  JSON parse failed: {e}")
        return []
    raw_list = extract_items(data, ["data", "aweme_list", "items", "results"])
    items = []
    for item in raw_list[:15]:
        if not isinstance(item, dict):
            continue
        # 抖音结构: item.data.aweme_info 或 item.aweme_info
        aweme = item.get("aweme_info")
        if not aweme:
            inner = item.get("data", {})
            if isinstance(inner, dict):
                aweme = inner.get("aweme_info", inner)
        if not aweme or not isinstance(aweme, dict):
            aweme = item
        desc = aweme.get("desc", "") or aweme.get("title", "")
        stats = aweme.get("statistics", {})
        share_url = aweme.get("share_url", "")
        play = stats.get("play_count", 0) or aweme.get("play_count", 0)
        like = stats.get("digg_count", 0) or aweme.get("digg_count", 0)
        create_time = aweme.get("create_time", 0)
        if desc:
            items.append({"title": desc, "url": share_url, "play": play, "like": like,
                          "create_time": create_time, "cid": aweme.get("aweme_id", "")})
    return items


def _xhs_publish_ts(note):
    """小红书搜索结果的发布时间(秒)。搜索接口大多不返 time/create_time,
    改取 corner_tag_info 里的 'YYYY-MM-DD' 显示日期(=发布日), 兜底 update_time(毫秒)。"""
    for tag in (note.get("corner_tag_info") or []):
        if not isinstance(tag, dict):
            continue
        for s in (tag.get("text_en"), tag.get("text")):
            if isinstance(s, str):
                m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", s)
                if m:
                    try:
                        return int(datetime(int(m.group(1)), int(m.group(2)),
                                            int(m.group(3)), tzinfo=BEIJING_TZ).timestamp())
                    except ValueError:
                        pass
    for k in ("time", "create_time", "last_update_time", "update_time"):
        v = note.get(k)
        if v:
            v = int(v)
            return v // 1000 if v > 1_000_000_000_000 else v   # 毫秒→秒
    return 0


def search_xiaohongshu(keyword):
    """搜索小红书（App V2 接口；旧版 /xiaohongshu/app/ 于 2026-06-19 下线）"""
    data = tikhub_get("/api/v1/xiaohongshu/app_v2/search_notes", {
        "keyword": keyword,
        "page": 1,
    })
    if not data:
        return []
    # 深度遍历找到笔记列表
    d = data.get("data", {})
    raw_list = []
    if isinstance(d, dict):
        # 可能的嵌套: data.data.items / data.data.notes / data.items
        inner = d.get("data", d)
        if isinstance(inner, dict):
            raw_list = (inner.get("items", []) or inner.get("notes", [])
                        or inner.get("note_list", []) or inner.get("data", []))
            # 如果 items 里每个元素有 note_card，说明找对了
            if not raw_list:
                for v in inner.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        raw_list = v
                        break
            print(f"    XHS inner keys: {list(inner.keys())[:10]}")
        elif isinstance(inner, list):
            raw_list = inner
        # 如果第一层 data 直接有 items
        if not raw_list:
            raw_list = d.get("items", []) or d.get("notes", [])
    # 调试
    if raw_list and isinstance(raw_list[0], dict):
        print(f"    XHS found {len(raw_list)} items")
        print(f"    XHS first item keys: {list(raw_list[0].keys())[:12]}")
        print(f"    XHS first item: {json.dumps(raw_list[0], ensure_ascii=False)[:300]}")
    else:
        # 打印完整 data 结构帮助调试
        print(f"    XHS no items found. data type: {type(d).__name__}")
        if isinstance(d, dict):
            for k, v in d.items():
                vtype = type(v).__name__
                vlen = len(v) if isinstance(v, (list, dict, str)) else ""
                print(f"      data.{k}: {vtype}({vlen})")
    items = []
    for item in raw_list[:15]:
        if not isinstance(item, dict):
            continue
        note = item.get("note_card") or item.get("note") or item
        title = (note.get("display_title") or note.get("title")
                 or note.get("desc") or note.get("name", ""))
        note_id = note.get("note_id") or note.get("id") or item.get("id", "")
        url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""
        interact = note.get("interact_info", {})
        like = interact.get("liked_count", 0) or note.get("liked_count", 0)
        if isinstance(like, str):
            like = like.replace("万", "0000")
            try:
                like = int(float(like))
            except ValueError:
                like = 0
        create_time = _xhs_publish_ts(note)
        if title:
            items.append({"title": title, "url": url, "play": 0, "like": like,
                          "create_time": create_time, "cid": note_id})
    return items


def search_wechat_channels(keyword):
    """搜索微信视频号(综合搜索)。
    旧路径 /wechat_channels/fetch_search_ordinary, 2026-06-19 起迁 v2; 参数名是 keywords(复数)。
    返回的 videoUrl 是腾讯 finder 直链(video/mp4, 浏览器可直接播放; 带签名当天有效),
    用作编辑点击观看入口。接口偶发返回空, 内部重试几次。"""
    raw_list = []
    for attempt in range(3):
        resp = tikhub_get("/api/v1/wechat_channels/fetch_search_ordinary", {"keywords": keyword})
        raw_list = _find_list(resp, ("videoUrl", "title", "docID")) or []
        if raw_list:
            break
        time.sleep(1.0)
    items = []
    for v in raw_list[:15]:
        if not isinstance(v, dict):
            continue
        # 标题里带搜索高亮 <em class="highlight">..</em>, 剥掉所有标签
        title = re.sub(r"<[^>]+>", "", v.get("title") or "").strip()
        if not title:
            continue
        like = v.get("likeNum") or 0
        if isinstance(like, str):
            like = _to_int_count(like)
        ct = v.get("pubTime") or v.get("createtime") or 0
        try:
            ct = int(ct)
        except (ValueError, TypeError):
            ct = 0
        items.append({
            "title": title,
            "url": v.get("videoUrl") or "",          # 直链, 点开即播放
            "play": 0,                                 # 视频号搜索不返播放量
            "like": like,
            "comment": 0,
            "create_time": ct,
            "cid": v.get("docID") or v.get("exportId") or "",
        })
    return items


SEARCH_FUNCS = {
    "wechat_channels": search_wechat_channels,
    "xigua": search_xigua,
    "bilibili": search_bilibili,
    "douyin": search_douyin,
    "xiaohongshu": search_xiaohongshu,
}


# 数据护栏: 剔除与保险无关的污染内容(典型: B站搜"养老"搜出《我的世界》养老整合包)
NOISE_PATTERNS = ("我的世界", "minecraft", "整合包", "种子推荐", "种子分享", "联机生存",
                  "手游", "模组", "光遇", "原神", "迷你世界", "沙盒游戏", "实况解说",
                  "钢琴教学", "皮肤下载")
def _is_noise(title):
    t = (title or "").lower()
    return any(p.lower() in t for p in NOISE_PATTERNS)


def collect_all_data():
    """每个话题搜索全部4个平台，按平台标准筛选。"""
    results = []

    for topic in TOPICS:
        topic_data = {"name": topic["name"], "icon": topic["icon"],
                      "color": topic["color"], "platforms": {}}

        for platform in ALL_PLATFORMS:
            search_fn = SEARCH_FUNCS[platform]
            quality_filter = PLATFORM_FILTERS[platform]
            merged = []
            seen_titles = set()

            for kw in topic["keywords"]:
                print(f"  [{topic['name']}] {PLATFORM_NAMES[platform]}: {kw}")
                items = search_fn(kw)
                print(f"    -> {len(items)} raw results")
                for item in items:
                    # 1) 只保留最近一周的帖子(create_time 缺失也丢弃, 避免老帖混入)
                    ct = item.get("create_time", 0)
                    if not ct or ct < ONE_WEEK_AGO:
                        continue
                    # 2) 按平台质量标准过滤
                    if not quality_filter(item):
                        continue
                    # 2.5) 数据护栏: 剔除游戏/娱乐等无关污染
                    if _is_noise(item.get("title", "")):
                        continue
                    # 按标题去重
                    key = item["title"][:20]
                    if key not in seen_titles:
                        seen_titles.add(key)
                        merged.append(item)
                time.sleep(0.5)

            print(f"    After filter: {len(merged)} items")
            topic_data["platforms"][platform] = merged[:10]

        results.append(topic_data)

    return results


# ── 后处理：去重 / 归一化 / LLM enrich ──────────────────

def _normalize_title(title):
    """去掉标点/空白/常见装饰字符后取前 30 字, 作为去重 key。"""
    t = re.sub(r"[^\w一-鿿]+", "", title or "")
    return t.lower()[:30]


def _heat_value(item):
    """跨平台可比的粗热度: max(play, like*5)。
    抖音/小红书没 play 只有 like, 乘 5 让量级接近 B 站 play。"""
    return max(item.get("play", 0) or 0, (item.get("like", 0) or 0) * 5)


def dedupe_across_topics(data):
    """全局按标准化标题去重: 同一条只保留热度最高的位置。"""
    seen = {}  # norm -> (topic_idx, platform_key, item_idx, heat)
    for ti, topic in enumerate(data):
        for plat, items in topic["platforms"].items():
            for ii, item in enumerate(items):
                norm = _normalize_title(item["title"])
                if not norm:
                    continue
                heat = _heat_value(item)
                if norm in seen:
                    prev = seen[norm]
                    if heat > prev[3]:
                        data[prev[0]]["platforms"][prev[1]][prev[2]]["_drop"] = True
                        seen[norm] = (ti, plat, ii, heat)
                    else:
                        item["_drop"] = True
                else:
                    seen[norm] = (ti, plat, ii, heat)

    removed = 0
    for topic in data:
        for plat in topic["platforms"]:
            before = len(topic["platforms"][plat])
            topic["platforms"][plat] = [i for i in topic["platforms"][plat] if not i.get("_drop")]
            removed += before - len(topic["platforms"][plat])
    print(f"  Dedupe: removed {removed} duplicates across topics/platforms")


def normalize_heat(data):
    """每个平台内, 按热度百分位算出 hotness_score 0-100。"""
    by_plat = {p: [] for p in ALL_PLATFORMS}
    for topic in data:
        for plat, items in topic["platforms"].items():
            for item in items:
                by_plat[plat].append(_heat_value(item))
    sorted_by_plat = {p: sorted(v) for p, v in by_plat.items() if v}
    for topic in data:
        for plat, items in topic["platforms"].items():
            arr = sorted_by_plat.get(plat, [])
            if not arr:
                continue
            n = max(len(arr) - 1, 1)
            for item in items:
                rank = bisect.bisect_left(arr, _heat_value(item))
                item["hotness_score"] = int(round(rank / n * 100))


def _extract_comment_texts(resp, max_n=8):
    """从评论接口响应里提取前 max_n 条评论文本(抖音 text / 小红书 content)。"""
    cs = _find_list((resp or {}).get("data"), ("text", "content")) or []
    out = []
    for c in cs:
        if not isinstance(c, dict):
            continue
        txt = (c.get("text") or c.get("content") or "").strip()
        if txt and len(txt) >= 3:        # 跳过纯表情/极短
            out.append(txt[:60])
        if len(out) >= max_n:
            break
    return out


def fetch_top_comments(data, max_items_per_platform=3):
    """对抖音/小红书每个话题的前 N 条爆款抓真实评论 → item['comments']。
    只抓头部几条(控成本); 评论喂给 DeepSeek 提炼"受众真实诉求"。"""
    for topic in data:
        for plat, items in topic["platforms"].items():
            for item in items:
                item.setdefault("comments", [])
    if not TIKHUB_API_KEY:
        return
    fetched = 0
    for topic in data:
        for plat, items in topic["platforms"].items():
            if plat not in ("douyin", "xiaohongshu"):
                continue
            done = 0
            for item in items:
                if done >= max_items_per_platform:
                    break
                cid = item.get("cid")
                if not cid:
                    continue
                try:
                    if plat == "douyin":
                        resp = tikhub_get("/api/v1/douyin/web/fetch_video_comments",
                                          {"aweme_id": cid, "count": 12})
                    else:
                        resp = tikhub_get("/api/v1/xiaohongshu/app_v2/get_note_comments",
                                          {"note_id": cid})
                    cmts = _extract_comment_texts(resp)
                    if cmts:
                        item["comments"] = cmts
                        fetched += 1
                    done += 1
                except Exception as e:
                    print(f"  comments {plat}/{str(cid)[:10]} failed: {e}")
                time.sleep(0.3)
    print(f"  Fetched real comments for {fetched} items (抖音+小红书)")


# ── DeepSeek LLM enrich ───────────────────────────────

ENRICH_KEYS = ("summary", "highlights", "biz_relevance", "biz_reason", "creation_angle",
               "audience_needs")
BIZ_RELEVANCE_VALUES = ("港险", "分红险", "养老", "通用获客", "不建议")


def _empty_enrich():
    return {
        "summary": "",
        "highlights": [],
        "biz_relevance": "通用获客",
        "biz_reason": "",
        "creation_angle": "",
        "audience_needs": [],
    }


def _call_deepseek_batch(topic_name, platform_key, items):
    """对单个 (topic, platform) 批次调用 DeepSeek, 返回与 items 同长度的 enrich 列表。"""
    if not DEEPSEEK_API_KEY:
        return [_empty_enrich() for _ in items]

    platform_name = PLATFORM_NAMES.get(platform_key, platform_key)
    inputs = []
    for i, item in enumerate(items, 1):
        cmts = item.get("comments") or []
        cmt_str = " ｜ ".join(cmts[:3]) if cmts else ""
        inputs.append({
            "id": i,
            "title": item.get("title", ""),
            "热度": _heat_value(item),
            "评论摘录": cmt_str,
        })

    sys_msg = (
        "你是保心上人公司（专注高端香港保险 + 分红险 + 养老规划）的内容选题分析助手, "
        "为短视频/小红书内容编辑判断每条热门内容是否适合再创作。"
        "你的输出必须是合法的 JSON。"
    )
    user_msg = f"""平台: {platform_name}
搜索主题: {topic_name}

保心上人的客户画像:
- 30-55 岁, 家庭年收入 50 万 +, 已有家庭/资产, 关心高净值传承
- 关心: 香港储蓄分红险、保险金信托、海外资产配置、养老规划
- 不匹配: 月薪 2000-5000 的学生/小镇青年存钱攒钱内容

请对下面这一批候选内容, 输出一个 JSON 对象 `{{"results": [...]}}`,
results 数组长度必须等于输入条目数, 每条包含:
  - id: 输入序号
  - summary: <=40 字, 一句话还原内容讲了什么(标题截断/不全时合理推测)
  - highlights: 3 条数组, 每条 <=10 字, 能勾住观众的钩子点
  - biz_relevance: 必须是 ["港险","分红险","养老","通用获客","不建议"] 之一
  - biz_reason: <=20 字, 简述判断理由
  - creation_angle: <=30 字, 给保心上人编辑的具体再创作角度建议
  - audience_needs: 2-3 条数组, 每条<=15 字, 受众真正关心的疑问/诉求(有"评论摘录"就从中提炼, 没有就据标题+常识推断)

判定 "不建议" 的典型情况:
  1) 负面舆情(如 "3.15 曝光香港保险""分红险暴雷")
  2) 受众画像不匹配(月薪 2700 / 高中生存钱 / 小县城日常)
  3) 反向种草("我为什么不买分红险")
  4) 信息含量极低(只有口号没有内容)

内地保险/增额终身寿/快返年金/商业养老金 这类话题的判定原则:
  - 它们是港险/分红险的竞品, 高净值客户也常被销售这些产品
  - 大部分情况应判 "通用获客", biz_reason 写 "内地竞品对标素材, 可做港险 vs 内地对比"
  - creation_angle 倾向于: "做港险 vs 内地增额寿/年金的对比测评" / "拆解内地产品话术陷阱, 引出港险方案"
  - 只有当内容是单纯吹捧内地产品 + 攻击港险, 或受众画像明显不匹配, 才判 "不建议"

输入条目:
{json.dumps(inputs, ensure_ascii=False, indent=2)}
"""

    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
    }

    last_err = ""
    for attempt in range(1, 3):
        try:
            resp = requests.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                         "Content-Type": "application/json"},
                json=body, timeout=90,
            )
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code in (401, 403):
                    print(f"    DeepSeek auth error, abort: {last_err}")
                    return [_empty_enrich() for _ in items]
                time.sleep(2)
                continue
            content = resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            arr = None
            if isinstance(parsed, dict):
                if isinstance(parsed.get("results"), list):
                    arr = parsed["results"]
                else:
                    for v in parsed.values():
                        if isinstance(v, list):
                            arr = v
                            break
            elif isinstance(parsed, list):
                arr = parsed
            if not isinstance(arr, list):
                last_err = f"no list in response: {content[:200]}"
                continue
            by_id = {d.get("id"): d for d in arr if isinstance(d, dict)}
            result = []
            for i in range(1, len(items) + 1):
                d = by_id.get(i) or (arr[i - 1] if i - 1 < len(arr) and isinstance(arr[i - 1], dict) else None)
                if d:
                    biz = d.get("biz_relevance", "通用获客")
                    if biz not in BIZ_RELEVANCE_VALUES:
                        biz = "通用获客"
                    hl = d.get("highlights") or []
                    if not isinstance(hl, list):
                        hl = [str(hl)]
                    hl = [str(x) for x in hl][:3]
                    an = d.get("audience_needs") or []
                    if not isinstance(an, list):
                        an = [str(an)]
                    an = [str(x)[:20] for x in an if x][:3]
                    result.append({
                        "summary": str(d.get("summary", ""))[:80],
                        "highlights": hl,
                        "biz_relevance": biz,
                        "biz_reason": str(d.get("biz_reason", ""))[:40],
                        "creation_angle": str(d.get("creation_angle", ""))[:60],
                        "audience_needs": an,
                    })
                else:
                    result.append(_empty_enrich())
            return result
        except Exception as e:
            last_err = f"{e}"
            time.sleep(2)
    print(f"    DeepSeek enrich failed for {topic_name}/{platform_key}: {last_err[:200]}")
    return [_empty_enrich() for _ in items]


def enrich_with_llm(data):
    if not DEEPSEEK_API_KEY:
        print("  DEEPSEEK_API_KEY not set — skipping LLM enrich (cards will show titles only)")
        for topic in data:
            for plat, items in topic["platforms"].items():
                for item in items:
                    item.update(_empty_enrich())
        return
    total = 0
    for topic in data:
        for plat, items in topic["platforms"].items():
            if not items:
                continue
            print(f"  Enriching {topic['name']} / {PLATFORM_NAMES[plat]} ({len(items)} items)")
            enriched = _call_deepseek_batch(topic["name"], plat, items)
            for item, enr in zip(items, enriched):
                item.update(enr)
            total += len(items)
            time.sleep(0.5)
    print(f"  Enriched {total} items via DeepSeek")


# ── HTML 生成 ─────────────────────────────────────────
def esc(text):
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def format_count(n):
    """格式化数字：1234 -> 1234, 12345 -> 1.2万"""
    if isinstance(n, str):
        return n
    if not n:
        return ""
    if n >= 10000:
        return f"{n/10000:.1f}万"
    return str(n)


def format_pubdate(ts):
    """Unix 时间戳 -> 相对时间 / 短日期。空/0 返回空串。"""
    if not ts:
        return ""
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    try:
        dt = datetime.fromtimestamp(ts, BEIJING_TZ)
    except (OSError, OverflowError, ValueError):
        return ""
    delta_days = (TODAY - dt).days
    if delta_days < 0:
        return dt.strftime("%m-%d")
    if delta_days == 0:
        return "今天"
    if delta_days == 1:
        return "昨天"
    if delta_days < 7:
        return f"{delta_days}天前"
    if delta_days < 30:
        return f"{delta_days // 7}周前"
    return dt.strftime("%Y-%m-%d")


BIZ_BADGE_COLOR = {
    "港险":     ("#EC4899", "#fff"),
    "分红险":   ("#8B5CF6", "#fff"),
    "养老":     ("#10B981", "#fff"),
    "通用获客": ("#3B82F6", "#fff"),
    "不建议":   ("#E5E7EB", "#6B7280"),
}


def build_item_card(item, rank):
    """单条候选项卡片 (HTML)。包含: rank / 标题(链)/ 摘要 / 爆点chips /
    再创作角度 / 业务相关度 badge / 热度分 + 平台数据 + 发布时间。
    biz_relevance='不建议' 时整卡灰化降级。"""
    url = (item.get("url") or "").strip()
    title = esc(item.get("title") or "")
    title_html = (
        f'<a href="{esc(url)}" target="_blank" rel="noopener" class="tl">{title}</a>'
        if url else f'<span class="tl">{title}</span>'
    )

    biz = item.get("biz_relevance") or "通用获客"
    bg, fg = BIZ_BADGE_COLOR.get(biz, BIZ_BADGE_COLOR["通用获客"])
    biz_badge = f'<span class="biz-badge" style="background:{bg};color:{fg};">{esc(biz)}</span>'

    summary = esc(item.get("summary") or "")
    summary_html = f'<div class="summary">{summary}</div>' if summary else ""

    hls = item.get("highlights") or []
    if hls:
        chips = " ".join(f'<span class="hl-chip">#{esc(h)}</span>' for h in hls if h)
        highlights_html = f'<div class="hl-row">{chips}</div>' if chips else ""
    else:
        highlights_html = ""

    angle = esc(item.get("creation_angle") or "")
    angle_html = f'<div class="angle">💡 {angle}</div>' if angle else ""

    # 受众诉求 (不建议项不展示)。口播草稿/合规标题/合规提醒已按需求下线。
    is_bad = (item.get("biz_relevance") == "不建议")
    needs = item.get("audience_needs") or []
    if needs and not is_bad:
        nchips = " ".join(f'<span class="need-chip">{esc(n)}</span>' for n in needs if n)
        needs_html = f'<div class="needs-row"><span class="blk-label">💬 受众在问</span>{nchips}</div>' if nchips else ""
    else:
        needs_html = ""

    biz_reason = esc(item.get("biz_reason") or "")
    reason_html = f'<div class="biz-reason">{biz_reason}</div>' if biz == "不建议" and biz_reason else ""

    stats = []
    play_str = format_count(item.get("play", 0))
    like_str = format_count(item.get("like", 0))
    if play_str:
        stats.append(f"▶ {play_str}")
    if like_str:
        stats.append(f"♥ {like_str}")
    pub = format_pubdate(item.get("create_time", 0))
    if pub:
        stats.append(f"📅 {pub}")
    hot = item.get("hotness_score")
    if isinstance(hot, int):
        stats.append(f'<span class="hot-score">🔥 {hot}</span>')
    stats_html = " · ".join(stats)

    extra_cls = " muted" if biz == "不建议" else ""
    return f"""<div class="item-card{extra_cls}">
      <div class="rank-col">{rank}</div>
      <div class="content-col">
        <div class="title-row">{title_html}{biz_badge}</div>
        {summary_html}
        {highlights_html}
        {angle_html}
        {needs_html}
        {reason_html}
        <div class="meta-row">{stats_html}</div>
      </div>
    </div>"""


def build_platform_section(platform_key, items):
    items = [it for it in items if it.get("biz_relevance") != "不建议"]  # 不建议项不显示
    if not items:
        return ""
    dot_color = PLATFORM_COLORS.get(platform_key, "#888")
    platform_name = PLATFORM_NAMES.get(platform_key, platform_key)
    cards = "".join(build_item_card(item, i) for i, item in enumerate(items[:3], 1))
    return f"""<div class="platform-section">
      <div class="platform-name" style="color:{dot_color};">● {platform_name} <span class="plat-count">(精选 {len(items[:3])} / 共 {len(items)})</span></div>
      <div class="item-list">{cards}</div>
    </div>"""


# ── 对标账号日更监控(第二期) ──────────────────────────
def _find_list(o, key_hint, depth=0):
    """递归找第一个 list[dict], 元素含任一 key_hint 字段。"""
    if depth > 8:
        return None
    if isinstance(o, list):
        if o and isinstance(o[0], dict) and any(k in o[0] for k in key_hint):
            return o
        for v in o:
            r = _find_list(v, key_hint, depth + 1)
            if r:
                return r
    elif isinstance(o, dict):
        for v in o.values():
            r = _find_list(v, key_hint, depth + 1)
            if r:
                return r
    return None


def _to_int_count(v):
    """把点赞/收藏数解析成 int, 兼容 '1.2万'/'1234'/'1,234'/数字。"""
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if not s:
            return 0
        try:
            if s[-1] in ("万", "w", "W"):
                return int(float(s[:-1]) * 10000)
            return int(float(s))
        except ValueError:
            return 0
    return 0


def _fetch_account_posts(acc):
    """拉单个对标账号近期作品 → [{title, like, create_time, url}]。"""
    plat = acc["platform"]
    out = []
    if plat == "douyin":
        # ⚠ 必须用 app/v3: web 版数据滞后约一周(实测停在一周前), app/v3 是当天最新的
        data = tikhub_get("/api/v1/douyin/app/v3/fetch_user_post_videos",
                          {"sec_user_id": acc["id"], "count": 20, "max_cursor": 0})
        for a in (_find_list((data or {}).get("data"), ("desc", "aweme_id")) or []):
            if not isinstance(a, dict):
                continue
            if a.get("is_top"):   # 跳过置顶老作品, 不算"动向"
                continue
            st = a.get("statistics", {}) or {}
            aid = a.get("aweme_id") or st.get("aweme_id")
            out.append({
                "title": a.get("desc", "") or "",
                "like": st.get("digg_count", 0) or 0,
                "create_time": a.get("create_time", 0) or 0,
                "url": a.get("share_url", "") or (f"https://www.douyin.com/video/{aid}" if aid else ""),
            })
    elif plat == "xiaohongshu":
        data = tikhub_get("/api/v1/xiaohongshu/app_v2/get_user_posted_notes",
                          {"user_id": acc["id"]})
        for n in (_find_list((data or {}).get("data"), ("display_title", "note_id", "desc")) or []):
            if not isinstance(n, dict):
                continue
            if n.get("sticky"):   # 跳过置顶简介贴, 否则它会霸占"本批最高赞"显得不更新
                continue
            # ⚠ app_v2 get_user_posted_notes 把点赞放在顶层 likes 字段(interact_info 恒为 null);
            #   旧代码读 interact_info.liked_count → 永远 0 → 小红书号不显示♥且"最高赞"失真。
            il = n.get("interact_info") or {}
            lk = _to_int_count(
                n.get("likes") if n.get("likes") is not None
                else (il.get("liked_count") or n.get("liked_count") or n.get("nice_count"))
            )
            nid = n.get("note_id") or n.get("id") or ""
            out.append({
                "title": n.get("display_title") or n.get("title") or "",
                "like": lk,
                "create_time": int(n.get("time", 0) or n.get("create_time", 0) or 0),
                "url": f"https://www.xiaohongshu.com/explore/{nid}" if nid else "",
            })
    return out


def monitor_accounts():
    """对标账号监控: 这些号每天都更新 → 直接列出每个号「昨天」发布的新作品(按赞排)。"""
    if not TIKHUB_API_KEY:
        return []
    yest = TODAY - timedelta(days=1)
    yest_start = int(yest.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    yest_end = yest_start + 86400  # 昨日 00:00 ~ 今日 00:00(不含)
    result = []
    for acc in MONITOR_ACCOUNTS:
        try:
            posts = _fetch_account_posts(acc)
        except Exception as e:
            print(f"  monitor {acc['name']} failed: {e}")
            posts = []
        yposts = [p for p in posts
                  if p["create_time"] and yest_start <= p["create_time"] < yest_end]
        yposts.sort(key=lambda p: p.get("like", 0) or 0, reverse=True)
        result.append({**acc, "yesterday": yposts})
        print(f"  monitor {acc['name']}({acc['platform']}): 共{len(posts)}条, 昨日{len(yposts)}条")
        time.sleep(0.4)
    return result


def render_monitor_section(monitor):
    if not monitor:
        return ""
    rows = ""
    for m in monitor:
        pn = {"douyin": "抖音", "xiaohongshu": "小红书"}.get(m["platform"], m["platform"])
        yposts = m.get("yesterday") or []
        if yposts:
            lines = []
            for p in yposts[:5]:
                t = esc((p.get("title") or "")[:40]) or "(无标题)"
                tl = (f'<a href="{esc(p["url"])}" target="_blank" rel="noopener" style="color:#1E2761;text-decoration:none;">{t}</a>'
                      if p.get("url") else t)
                like_badge = (f' <span style="color:#f04142;font-weight:700;white-space:nowrap;">♥{format_count(p["like"])}</span>'
                              if (p.get("like") or 0) > 0 else "")
                lines.append(f'<div style="padding:3px 0;line-height:1.4;">{tl}{like_badge}</div>')
            cell = "".join(lines)
        else:
            cell = '<span style="color:#bbb;">昨日无更新</span>'
        rows += (f'<tr><td style="white-space:nowrap;vertical-align:top;">{esc(m["name"])}</td>'
                 f'<td style="color:#888;white-space:nowrap;vertical-align:top;">{pn}</td>'
                 f'<td style="vertical-align:top;">{cell}</td></tr>')
    return (f'<div class="monitor-section"><div class="monitor-title">📡 对标账号动向 · 昨日更新</div>'
            f'<table class="monitor-table"><thead><tr>'
            f'<th>对标账号</th><th>平台</th><th>昨日发布作品</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div>')


def pick_featured(data, n=6):
    """全局精选: 排除『不建议』, 业务相关度优先(港险/分红险/养老) + 热度, 取 top n。"""
    cands = []
    for topic in data:
        for plat, items in topic.get("platforms", {}).items():
            for it in items:
                if it.get("biz_relevance") == "不建议" or not it.get("title"):
                    continue
                cands.append((topic["name"], plat, it))
    pri = {"港险": 3, "分红险": 3, "养老": 2, "通用获客": 1}
    cands.sort(key=lambda x: (pri.get(x[2].get("biz_relevance"), 0), x[2].get("hotness_score", 0) or 0), reverse=True)
    return cands[:n]


def render_featured_section(featured):
    if not featured:
        return ""
    cards = ""
    for i, (tname, plat, it) in enumerate(featured, 1):
        pn = PLATFORM_NAMES.get(plat, plat)
        src = f'<div class="feat-src">{esc(pn)} · {esc(tname)}</div>'
        cards += f'<div class="feat-wrap">{src}{build_item_card(it, i)}</div>'
    return (f'<div class="featured-section"><div class="featured-title">⭐ 今日精选 · 建议优先做</div>'
            f'<div class="item-list">{cards}</div></div>')


# ── 昨日要闻(财经时事, Google News RSS, best-effort)──────
NEWS_QUERIES = [
    ("存款",     "大额存单 OR 存款利率 OR 定期存款 OR 利率下调"),
    ("港险",     "香港保险 OR 香港储蓄险"),
    ("分红险",   "分红险 OR 增额终身寿 OR 年金险 OR 预定利率"),
    ("养老",     "个人养老金 OR 商业养老金"),
]


def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def fetch_news(max_total=28):
    """Google News RSS 抓最近 2 天的财经时事。返回 [{title, source, url, topic}]。
    在 GitHub Actions(ubuntu, US)可达;本机中国网络可能 302/超时, 失败即返回空(best-effort)。"""
    import xml.etree.ElementTree as ET
    out, seen = [], set()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; baoxin-news/1.0)"}
    for label, q in NEWS_QUERIES:
        url = ("https://news.google.com/rss/search?q="
               + requests.utils.quote(q + " when:2d")
               + "&hl=zh-CN&gl=CN&ceid=CN:zh")
        try:
            r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
            if r.status_code != 200:
                print(f"  news[{label}] HTTP {r.status_code}")
                continue
            root = ET.fromstring(r.content)
            cnt = 0
            for it in root.iter("item"):
                title = _strip_tags(it.findtext("title") or "")
                link = (it.findtext("link") or "").strip()
                src_el = it.find("source")
                source = (src_el.text or "").strip() if src_el is not None else ""
                if not source and " - " in title:  # Google News 标题常是 "标题 - 来源"
                    title, source = title.rsplit(" - ", 1)
                title = title.strip()
                if not title:
                    continue
                key = _normalize_title(title)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"title": title[:90], "source": source[:18],
                            "url": link, "topic": label})
                cnt += 1
                if cnt >= 10:
                    break
        except Exception as e:
            print(f"  news[{label}] failed: {e}")
    print(f"  Fetched {len(out)} news items")
    return out[:max_total]


def curate_news(news):
    """DeepSeek 从抓取的财经新闻里挑业务相关的(<=8 条), 每条加一句'选题切入'。
    无 key / 失败时回退为原始前 8 条(无切入语)。"""
    if not news:
        return []
    # DeepSeek 缺失/失败时返回空、不渲染该块: 原始 Google News 对宽查询的结果噪声很大
    # (外媒/公司公告/股市行情混入), 必须靠 DeepSeek 过滤才有价值。
    fallback = []
    if not DEEPSEEK_API_KEY:
        return fallback
    inputs = [{"id": i + 1, "title": n["title"], "source": n.get("source", ""), "topic": n.get("topic", "")}
              for i, n in enumerate(news)]
    sys_msg = "你是保心上人(高端香港保险+分红险+养老规划)的内容选题助手, 输出必须是合法 JSON。"
    user_msg = f"""下面是抓取的财经新闻标题(存款/大额存单/利率/香港保险/分红险/增额寿/年金/养老等)。
请挑出**对保险获客内容创作最有价值**的(最多 8 条):剔除重复、与保险/存款理财无关、纯股市行情、标题党、广告。
输出 {{"results":[{{"id":输入序号,"category":"存款|港险|分红险|养老|其他","angle":"<=24字, 给编辑的选题切入(怎么把这条新闻做成获客内容)"}}]}},
只保留你选中的, 按对业务的价值从高到低排序。
新闻:
{json.dumps(inputs, ensure_ascii=False, indent=2)}
"""
    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": DEEPSEEK_MODEL,
                  "messages": [{"role": "system", "content": sys_msg},
                               {"role": "user", "content": user_msg}],
                  "temperature": 0.3, "response_format": {"type": "json_object"}, "max_tokens": 1200},
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"  news curate HTTP {resp.status_code}")
            return fallback
        parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
        arr = parsed.get("results") if isinstance(parsed, dict) else parsed
        if not isinstance(arr, list) or not arr:
            return fallback
        curated = []
        for d in arr:
            if not isinstance(d, dict):
                continue
            idx = d.get("id")
            if not isinstance(idx, int) or not (1 <= idx <= len(news)):
                continue
            n = news[idx - 1]
            curated.append({**n,
                            "category": str(d.get("category", n.get("topic", "")))[:6],
                            "angle": str(d.get("angle", ""))[:40]})
        return curated[:8] if curated else fallback
    except Exception as e:
        print(f"  news curate failed: {e}")
        return fallback


def render_news_section(curated):
    if not curated:
        return ""
    rows = ""
    for n in curated:
        cat = esc(n.get("category") or n.get("topic") or "")
        cat_html = f'<span class="news-cat">{cat}</span>' if cat else ""
        title = esc(n.get("title") or "")
        url = (n.get("url") or "").strip()
        title_html = (f'<a href="{esc(url)}" target="_blank" rel="noopener">{title}</a>'
                      if url else title)
        src = esc(n.get("source") or "")
        src_html = f'<span class="news-src">{src}</span>' if src else ""
        angle = esc(n.get("angle") or "")
        angle_html = f'<div class="news-angle">📌 {angle}</div>' if angle else ""
        rows += (f'<div class="news-item">{cat_html}'
                 f'<div class="news-body"><div class="news-tl">{title_html} {src_html}</div>'
                 f'{angle_html}</div></div>')
    return (f'<div class="news-section"><div class="news-bar">📰 昨日要闻 '
            f'<span class="news-sub">存款 / 大额存单 / 港险 / 分红险</span></div>{rows}</div>')


def synthesize_insight(data):
    """对当日全部精选爆款做一句话风向综述, 给编辑定选题方向。"""
    if not DEEPSEEK_API_KEY:
        return ""
    lines = []
    for topic in data:
        for plat, items in topic.get("platforms", {}).items():
            for it in items[:3]:
                if it.get("biz_relevance") == "不建议":
                    continue
                t = (it.get("title") or "").strip()
                if t:
                    lines.append(f"[{topic['name']}] {t}")
    sample = "\n".join(lines[:40])
    if not sample:
        return ""
    sys_msg = "你是保心上人的内容选题分析助手, 只输出一句话, 不要任何前缀或解释。"
    user_msg = ("下面是今天保险相关的跨平台热门标题:\n" + sample +
                "\n\n请用<=60字总结'今天平台上什么角度/情绪在跑量', 给内容编辑一句可执行的选题方向。")
    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": DEEPSEEK_MODEL,
                  "messages": [{"role": "system", "content": sys_msg},
                               {"role": "user", "content": user_msg}],
                  "temperature": 0.4, "max_tokens": 200},
            timeout=60,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()[:160]
    except Exception as e:
        print(f"  insight synth failed: {e}")
    return ""


def generate_detail_page(data, insight="", monitor_html="", news_html=""):
    insight_html = ""
    if insight:
        insight_html = f'<div class="insight-banner"><span class="ib-label">🔥 今日风向</span>{esc(insight)}</div>'
    featured_html = render_featured_section(pick_featured(data, 6))
    topic_cards = insight_html + news_html + monitor_html + featured_html
    for topic in data:
        sections = ""
        for platform_key, items in topic["platforms"].items():
            if items:
                sections += build_platform_section(platform_key, items)

        if not sections:
            sections = '<div class="empty">暂无数据</div>'

        topic_cards += f"""
    <div class="topic-card">
      <div class="topic-header" style="background:{topic['color']};">
        {topic['icon']} {esc(topic['name'])}
      </div>
      {sections}
    </div>"""

    # 收集所有平台
    all_platforms = set()
    for topic in data:
        for p in topic["platforms"]:
            all_platforms.add(PLATFORM_NAMES[p])
    platforms_str = " / ".join(sorted(all_platforms))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>每日热门话题 · {DATE_STR}</title>
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{
      font-family: "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
      background: #f0f2f8;
      color: #333;
      line-height: 1.6;
    }}

    header {{
      background: linear-gradient(135deg, #1E2761 0%, #162050 60%, #028090 100%);
      color: #fff;
      padding: 28px 24px 24px;
    }}
    .header-inner {{
      max-width: 1100px;
      margin: 0 auto;
    }}
    .back-btn {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(255,255,255,.12);
      border: 1px solid rgba(255,255,255,.2);
      border-radius: 100px;
      padding: 5px 14px;
      font-size: 13px;
      color: rgba(255,255,255,.8);
      text-decoration: none;
      margin-bottom: 14px;
    }}
    .back-btn:hover {{ background: rgba(255,255,255,.2); }}
    header h1 {{ font-size: clamp(18px,3vw,26px); font-weight: 700; margin-bottom: 4px; }}
    .header-sub {{ font-size: 13px; opacity: .6; }}

    main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px 16px 60px;
    }}

    /* 瀑布流: 多列流式, 卡片紧凑往上堆, 不按行对齐留白 */
    .topic-grid {{
      column-count: 1;
      column-gap: 22px;
    }}
    .topic-grid > * {{ break-inside: avoid; margin-bottom: 22px; }}

    .topic-card {{
      background: white;
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 0 2px 12px rgba(0,0,0,0.07);
    }}

    .topic-header {{
      padding: 14px 20px;
      font-size: 1.05em;
      font-weight: 700;
      letter-spacing: 1px;
      color: white;
    }}

    .platform-section {{
      padding: 14px 18px;
      border-bottom: 1px solid #f0f0f0;
    }}
    .platform-section:last-child {{ border-bottom: none; }}

    .platform-name {{
      font-size: 0.78em;
      font-weight: 700;
      margin-bottom: 10px;
      letter-spacing: 0.5px;
    }}
    .plat-count {{ color:#bbb; font-weight: 500; }}

    .item-list {{ display: flex; flex-direction: column; gap: 10px; }}

    .item-card {{
      display: flex;
      gap: 10px;
      padding: 10px 12px;
      background: #fafbfd;
      border-radius: 10px;
      border-left: 3px solid transparent;
      transition: background .15s;
    }}
    .item-card:hover {{ background: #f3f5f9; }}
    .item-card.muted {{ opacity: 0.55; background: #f5f5f5; border-left-color:#ddd; }}
    .item-card.muted .tl {{ color: #888; }}

    .rank-col {{
      width: 22px;
      color: #ccc;
      font-weight: 700;
      font-size: 0.9em;
      flex-shrink: 0;
    }}
    .content-col {{ flex: 1; min-width: 0; }}

    .title-row {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 4px;
    }}
    .tl {{
      color: #1E2761;
      text-decoration: none;
      font-weight: 600;
      font-size: 0.95em;
      line-height: 1.35;
    }}
    .tl:hover {{ color: #028090; text-decoration: underline; }}

    .biz-badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 100px;
      font-size: 0.7em;
      font-weight: 600;
      letter-spacing: 0.5px;
      white-space: nowrap;
    }}

    .summary {{
      color: #555;
      font-size: 0.85em;
      line-height: 1.5;
      margin: 4px 0;
    }}

    .hl-row {{ margin: 4px 0; display: flex; flex-wrap: wrap; gap: 4px; }}
    .hl-chip {{
      display: inline-block;
      padding: 1px 7px;
      background: #fff;
      border: 1px solid #e5e7eb;
      border-radius: 4px;
      font-size: 0.72em;
      color: #6366F1;
      font-weight: 500;
    }}

    .angle {{
      margin: 6px 0 4px;
      padding: 5px 10px;
      background: #fff8e1;
      border-radius: 6px;
      font-size: 0.82em;
      color: #92590a;
      line-height: 1.45;
    }}
    .item-card.muted .angle {{ display: none; }}

    /* 第一期: 爆款拆解块 */
    .blk-label {{ display:inline-block; font-weight:700; font-size:0.92em; margin-right:6px; opacity:.85; }}
    .needs-row {{ margin:5px 0; font-size:0.8em; color:#475569; display:flex; flex-wrap:wrap; gap:5px; align-items:center; }}
    .need-chip {{ background:#eef2ff; border:1px solid #e0e7ff; color:#4338ca; border-radius:6px; padding:1px 8px; font-size:0.95em; }}
    /* 昨日要闻 */
    .news-section {{ column-span:all; background:#fff; border:1.5px solid #dbeafe; border-left:5px solid #2563eb; border-radius:12px; padding:14px 18px; }}
    .news-bar {{ font-weight:800; color:#1e40af; font-size:1.0em; margin-bottom:8px; }}
    .news-sub {{ font-size:0.74em; color:#93a3c0; font-weight:600; margin-left:4px; }}
    .news-item {{ display:flex; gap:10px; padding:8px 0; border-bottom:1px solid #f1f5f9; align-items:flex-start; }}
    .news-item:last-child {{ border-bottom:none; }}
    .news-cat {{ flex:none; font-size:0.72em; font-weight:700; color:#1d4ed8; background:#eff6ff; border:1px solid #dbeafe; border-radius:6px; padding:2px 8px; margin-top:2px; }}
    .news-body {{ flex:1; min-width:0; }}
    .news-tl {{ font-size:0.9em; color:#1f2937; line-height:1.5; }}
    .news-tl a {{ color:#1f2937; text-decoration:none; }}
    .news-tl a:hover {{ color:#2563eb; text-decoration:underline; }}
    .news-src {{ font-size:0.76em; color:#9ca3af; margin-left:4px; white-space:nowrap; }}
    .news-angle {{ font-size:0.8em; color:#2563eb; margin-top:3px; }}

    /* 风向洞察 */
    .insight-banner {{
      column-span:all;
      background: linear-gradient(135deg,#fff7ed,#fff);
      border:1.5px solid #fed7aa; border-left:5px solid #f97316;
      border-radius:12px; padding:14px 18px; font-size:0.92em; color:#7c2d12; line-height:1.6;
    }}
    .insight-banner .ib-label {{ font-weight:800; color:#c2410c; margin-right:8px; }}

    /* 对标账号动向 */
    .monitor-section {{ column-span:all; background:#fff; border:1.5px solid #e5e7eb; border-radius:12px; padding:14px 18px; }}
    .monitor-title {{ font-weight:800; color:#1E2761; font-size:0.98em; margin-bottom:10px; }}
    .monitor-table {{ width:100%; border-collapse:collapse; font-size:0.85em; }}
    .monitor-table th {{ text-align:left; color:#888; font-weight:600; padding:7px 12px; border-bottom:1px solid #eef0f6; font-size:0.92em; }}
    .monitor-table td {{ padding:9px 12px; border-bottom:1px solid #f5f6fa; }}
    .monitor-table tr:hover td {{ background:#fafbff; }}

    /* 今日精选 */
    .featured-section {{ column-span:all; background:linear-gradient(135deg,#fffbeb,#fff); border:1.5px solid #fde68a; border-radius:14px; padding:14px 18px; }}
    .featured-section .item-list {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:10px 16px; }}
    .featured-title {{ font-weight:800; color:#b45309; font-size:1.05em; margin-bottom:10px; }}
    .feat-wrap {{ margin-bottom:4px; }}
    .feat-src {{ font-size:0.74em; color:#92590a; font-weight:700; padding:0 0 2px 34px; }}

    .biz-reason {{
      margin: 4px 0;
      font-size: 0.78em;
      color: #9CA3AF;
      font-style: italic;
    }}

    .meta-row {{
      margin-top: 5px;
      color: #aaa;
      font-size: 0.78em;
    }}
    .hot-score {{
      color: #F59E0B;
      font-weight: 600;
    }}

    .empty {{
      padding: 20px;
      text-align: center;
      color: #ccc;
      font-size: 0.9em;
    }}

    footer {{
      text-align: center;
      color: #aaa;
      font-size: 0.8em;
      padding: 20px;
    }}

    .legend {{
      max-width: 1100px;
      margin: 16px auto 0;
      padding: 12px 16px;
      background: white;
      border-radius: 10px;
      font-size: 0.8em;
      color: #666;
      display: flex;
      flex-wrap: wrap;
      gap: 10px 18px;
      align-items: center;
    }}
    .legend b {{ color:#333; }}

    @media (min-width: 900px) {{
      .topic-grid {{ column-count: 2; }}
    }}
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <a class="back-btn" href="index.html">&larr; 返回首页</a>
    <h1>🔥 每日热门话题</h1>
    <div class="header-sub">{DATE_CN} · {platforms_str}</div>
  </div>
</header>

<div class="legend">
  <span><b>业务相关度</b>:</span>
  <span><span class="biz-badge" style="background:#EC4899;color:#fff;">港险</span> 直接对标</span>
  <span><span class="biz-badge" style="background:#8B5CF6;color:#fff;">分红险</span> 高优先</span>
  <span><span class="biz-badge" style="background:#10B981;color:#fff;">养老</span> 长线选题</span>
  <span><span class="biz-badge" style="background:#3B82F6;color:#fff;">通用获客</span> 涨粉/获客可用</span>
  <span><span class="biz-badge" style="background:#E5E7EB;color:#6B7280;">不建议</span> 不要做</span>
  <span style="margin-left:auto;color:#aaa;">🔥 = 同平台百分位热度分(0-100) · 💡 = AI 再创作角度建议</span>
</div>

<main>
  <div class="topic-grid">
    {topic_cards}
  </div>
</main>

<footer>🔄 每日自动更新 · TikHub 搜索 + DeepSeek 选题分析</footer>

</body>
</html>"""


# ── 主流程 ────────────────────────────────────────────
def main():
    print(f"=== 每日热门话题生成 · {DATE_STR} ===")
    if not TIKHUB_API_KEY:
        raise SystemExit("TIKHUB_API_KEY env var is required")

    print("Step 1: Searching via TikHub API...")
    data = collect_all_data()
    total = sum(len(items) for t in data for items in t["platforms"].values())
    print(f"  Collected {total} raw items")

    if total == 0:
        # 采到 0 条 = 上游异常(TikHub 余额耗尽 / API Key 失效 / 接口变更)。
        # 1) 绝不用空页面覆盖上一次的好数据, 否则页面直接变白;保留 daily-topics.html 原样。
        # 2) 以非 0 退出码 raise, 让 GitHub Action 标红报警, 杜绝"采到0条仍报成功"的静默断更。
        raise SystemExit(
            "ERROR: 全平台合计采到 0 条 —— 疑似 TikHub 余额耗尽 / API Key 失效 / 接口变更。"
            "已保留上一次的 daily-topics.html(不覆盖), 请检查 TikHub 账号与接口。"
        )

    print("Step 2: Dedupe across topics/platforms...")
    dedupe_across_topics(data)

    print("Step 3: Normalize heat scores...")
    normalize_heat(data)

    print("Step 4: Fetch top comments (best-effort)...")
    fetch_top_comments(data)

    print("Step 5: Enrich with DeepSeek...")
    enrich_with_llm(data)

    print("Step 5.5: Synthesize daily insight...")
    insight = synthesize_insight(data)
    if insight:
        print(f"  Insight: {insight}")

    print("Step 5.6: Monitor 对标账号...")
    monitor = monitor_accounts()

    print("Step 5.7: Fetch 昨日要闻...")
    news = curate_news(fetch_news())
    if news:
        print(f"  Curated {len(news)} news items")

    print("Step 6: Generate detail page...")
    html = generate_detail_page(data, insight, render_monitor_section(monitor),
                                render_news_section(news))
    with open(DETAIL_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Written: {DETAIL_FILE}")

    print("=== Done ===")


if __name__ == "__main__":
    main()
