#!/usr/bin/env python3
"""
每日热门话题 - TikHub 版自动生成脚本
每天由 GitHub Actions 调用，通过 TikHub API 搜索各平台热门内容，
生成分类热门话题 HTML 报告。

平台/关键词映射：
  存钱         → 西瓜视频、B站
  存款/定存/国债 → 西瓜视频
  香港保险/分红险 → 抖音、小红书
  养老         → 西瓜视频
"""

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# ── 配置 ──────────────────────────────────────────────
BEIJING_TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(BEIJING_TZ)
DATE_STR = TODAY.strftime("%Y-%m-%d")
DATE_CN = TODAY.strftime("%Y年%-m月%-d日")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETAIL_FILE = os.path.join(PROJECT_ROOT, f"daily-topics-{DATE_STR}.html")
LIST_FILE = os.path.join(PROJECT_ROOT, "daily-topics-reports.html")

TIKHUB_API_KEY = os.environ["TIKHUB_API_KEY"]
TIKHUB_BASE = "https://api.tikhub.io"

HEADERS = {
    "Authorization": f"Bearer {TIKHUB_API_KEY}",
    "Content-Type": "application/json",
}

# ── 话题/平台配置 ─────────────────────────────────────
TOPICS = [
    {
        "name": "存钱",
        "icon": "💰",
        "color": "#F59E0B",
        "searches": [
            {"keywords": ["存钱"], "platform": "xigua"},
            {"keywords": ["存钱"], "platform": "bilibili"},
        ],
    },
    {
        "name": "存款/定存/国债",
        "icon": "🏦",
        "color": "#6366F1",
        "searches": [
            {"keywords": ["存款", "定存", "国债"], "platform": "xigua"},
        ],
    },
    {
        "name": "香港保险/分红险",
        "icon": "🛡️",
        "color": "#EC4899",
        "searches": [
            {"keywords": ["香港保险", "分红险"], "platform": "douyin"},
            {"keywords": ["香港保险", "分红险"], "platform": "xiaohongshu"},
        ],
    },
    {
        "name": "养老",
        "icon": "🏡",
        "color": "#10B981",
        "searches": [
            {"keywords": ["养老"], "platform": "xigua"},
        ],
    },
]

PLATFORM_NAMES = {
    "xigua": "西瓜视频",
    "bilibili": "B站",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
}

PLATFORM_COLORS = {
    "xigua": "#F04142",
    "bilibili": "#00A1D6",
    "douyin": "#1A1A1A",
    "xiaohongshu": "#FF2442",
}

PLATFORM_TAG_CLASSES = {
    "xigua": "tag-xigua",
    "bilibili": "tag-bili",
    "douyin": "tag-douyin",
    "xiaohongshu": "tag-xhs",
}


# ── TikHub API 搜索 ──────────────────────────────────
def tikhub_get(path, params=None):
    url = f"{TIKHUB_BASE}{path}"
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            # 调试：打印响应结构的顶层 keys
            if isinstance(data, dict):
                print(f"    Response keys: {list(data.keys())}")
                d = data.get("data")
                if isinstance(d, dict):
                    print(f"    data keys: {list(d.keys())[:10]}")
            return data
        print(f"  API error {resp.status_code}: {resp.text[:500]}")
        return None
    except Exception as e:
        print(f"  API request failed: {e}")
        return None


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
        url = vdata.get("share_url") or vdata.get("url") or vdata.get("article_url", "")
        play = vdata.get("play_count", 0) or vdata.get("video_watch_count", 0)
        like = vdata.get("digg_count", 0) or vdata.get("like_count", 0)
        # 如果还是没 title，尝试从 itemDataStr 解析
        if not title:
            ids = item.get("itemDataStr", "")
            if isinstance(ids, str) and ids:
                try:
                    ids_data = json.loads(ids)
                    title = ids_data.get("title") or ids_data.get("content", "")
                    url = url or ids_data.get("share_url", "")
                except (json.JSONDecodeError, TypeError):
                    pass
        if title:
            items.append({"title": title, "url": url, "play": play, "like": like})
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
        if title and item.get("type", "") != "bili_user":
            items.append({"title": title, "url": url, "play": play, "like": like})
    return items


def search_douyin(keyword):
    """搜索抖音（使用 App V3 接口）"""
    resp = tikhub_get("/api/v1/douyin/app/v3/fetch_general_search_result", {
        "keyword": keyword,
        "offset": 0,
        "count": 15,
    })
    if not resp:
        return []
    raw_list = extract_items(resp, ["data", "aweme_list", "items", "results"])
    items = []
    for item in raw_list[:15]:
        if not isinstance(item, dict):
            continue
        # 抖音搜索结果可能嵌套在 aweme_info 中
        aweme = item.get("aweme_info", item)
        # 也可能在 item.data 里
        if not aweme.get("desc") and isinstance(item.get("data"), dict):
            aweme = item["data"]
        desc = aweme.get("desc", "") or aweme.get("title", "")
        stats = aweme.get("statistics", {})
        url = aweme.get("share_url", "")
        play = stats.get("play_count", 0) or aweme.get("play_count", 0)
        like = stats.get("digg_count", 0) or aweme.get("digg_count", 0)
        if desc:
            items.append({"title": desc, "url": url, "play": play, "like": like})
    return items


def search_xiaohongshu(keyword):
    """搜索小红书"""
    # 直接用 V1 接口（V2 经常报 400）
    data = tikhub_get("/api/v1/xiaohongshu/web/search_notes", {
        "keyword": keyword,
    })
    if not data:
        return []
    # 小红书嵌套: TikHub.data -> XHS.{data:{items/notes}}
    d = data.get("data", {})
    raw_list = []
    if isinstance(d, dict):
        inner = d.get("data", d)
        if isinstance(inner, dict):
            raw_list = (inner.get("items", []) or inner.get("notes", [])
                        or inner.get("note_list", []))
            if not raw_list:
                for v in inner.values():
                    if isinstance(v, list) and v:
                        raw_list = v
                        break
            print(f"    XHS inner keys: {list(inner.keys())[:10]}")
        elif isinstance(inner, list):
            raw_list = inner
    items = []
    for item in raw_list[:15]:
        if not isinstance(item, dict):
            continue
        note = item.get("note_card", item)
        title = (note.get("display_title") or note.get("title")
                 or note.get("desc") or note.get("name", ""))
        note_id = note.get("note_id") or item.get("id") or item.get("note_id", "")
        url = f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else ""
        interact = note.get("interact_info", {})
        like = interact.get("liked_count", 0) or note.get("liked_count", 0)
        if isinstance(like, str):
            like = like.replace("万", "0000")
            try:
                like = int(float(like))
            except ValueError:
                like = 0
        if title:
            items.append({"title": title, "url": url, "play": 0, "like": like})
    return items


SEARCH_FUNCS = {
    "xigua": search_xigua,
    "bilibili": search_bilibili,
    "douyin": search_douyin,
    "xiaohongshu": search_xiaohongshu,
}


def collect_all_data():
    """按话题/平台/关键词组合搜索，返回结构化数据。"""
    results = []

    for topic in TOPICS:
        topic_data = {"name": topic["name"], "icon": topic["icon"],
                      "color": topic["color"], "platforms": {}}

        for search_cfg in topic["searches"]:
            platform = search_cfg["platform"]
            search_fn = SEARCH_FUNCS[platform]
            merged = []
            seen_titles = set()

            for kw in search_cfg["keywords"]:
                print(f"  [{topic['name']}] {PLATFORM_NAMES[platform]}: {kw}")
                items = search_fn(kw)
                print(f"    -> {len(items)} results")
                for item in items:
                    # 按标题去重
                    key = item["title"][:20]
                    if key not in seen_titles:
                        seen_titles.add(key)
                        merged.append(item)
                time.sleep(0.5)

            topic_data["platforms"][platform] = merged[:10]

        results.append(topic_data)

    return results


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


def build_platform_section(platform_key, items):
    dot_color = PLATFORM_COLORS.get(platform_key, "#888")
    platform_name = PLATFORM_NAMES.get(platform_key, platform_key)
    rows = ""
    for i, item in enumerate(items[:10], 1):
        url = item.get("url", "").strip()
        title = esc(item["title"])
        if len(title) > 60:
            title = title[:60] + "..."
        title_html = (
            f'<a href="{esc(url)}" target="_blank" rel="noopener" class="tl">{title}</a>'
            if url else title
        )
        stats = []
        play_str = format_count(item.get("play", 0))
        like_str = format_count(item.get("like", 0))
        if play_str:
            stats.append(f"▶ {play_str}")
        if like_str:
            stats.append(f"♥ {like_str}")
        stats_html = f'<span class="stats">{" · ".join(stats)}</span>' if stats else ""

        rows += f"""<tr>
            <td class="rank">{i}</td>
            <td>{title_html}{stats_html}</td>
          </tr>"""

    return f"""<div class="platform-section">
      <div class="platform-name" style="color:{dot_color};">● {platform_name}</div>
      <table><tbody>{rows}</tbody></table>
    </div>"""


def generate_detail_page(data):
    topic_cards = ""
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

    .topic-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      align-items: start;
    }}

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
      text-align: center;
      letter-spacing: 1px;
      color: white;
    }}

    .platform-section {{
      padding: 12px 16px;
      border-bottom: 1px solid #f0f0f0;
    }}
    .platform-section:last-child {{ border-bottom: none; }}

    .platform-name {{
      font-size: 0.75em;
      font-weight: 700;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}

    table {{ width: 100%; border-collapse: collapse; font-size: 0.82em; }}
    td {{
      padding: 5px 6px;
      border-bottom: 1px solid #f8f8f8;
      vertical-align: top;
    }}
    td.rank {{ color: #ccc; width: 22px; font-weight: 600; text-align: center; }}
    tr:nth-child(-n+3) td.rank {{ color: #f59e0b; }}
    tr:last-child td {{ border-bottom: none; }}

    .tl {{
      color: #1E2761;
      text-decoration: none;
      font-weight: 500;
      line-height: 1.4;
    }}
    .tl:hover {{ color: #028090; text-decoration: underline; }}

    .stats {{
      display: block;
      color: #aaa;
      font-size: 0.85em;
      margin-top: 2px;
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

    @media (max-width: 680px) {{
      .topic-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <a class="back-btn" href="daily-topics-reports.html">&larr; 返回列表</a>
    <h1>🔥 每日热门话题</h1>
    <div class="header-sub">{DATE_CN} · {platforms_str}</div>
  </div>
</header>

<main>
  <div class="topic-grid">
    {topic_cards}
  </div>
</main>

<footer>🔄 每日自动更新 · TikHub API</footer>

</body>
</html>"""


# ── 更新列表页 ────────────────────────────────────────
def update_list_page():
    day = TODAY.day
    month = TODAY.month

    # 收集所有平台 tag
    all_tags = []
    for topic in TOPICS:
        for s in topic["searches"]:
            p = s["platform"]
            cls = PLATFORM_TAG_CLASSES.get(p, "")
            name = PLATFORM_NAMES.get(p, p)
            tag = f'<span class="tag {cls}">{name}</span>'
            if tag not in all_tags:
                all_tags.append(tag)
    tags_html = "\n          ".join(all_tags)

    new_entry = f"""
      <a class="report-card" href="daily-topics-{DATE_STR}.html">
        <div class="card-icon">🔥</div>
        <div class="card-body">
          <div class="card-title">每日热门话题 &nbsp;&middot;&nbsp; {month}月{day}日</div>
          <div class="card-meta">
            <span>📅 {DATE_CN}</span>
            <span>🤖 TikHub 自动采集</span>
          </div>
        </div>
        <div class="card-tags">
          {tags_html}
        </div>
        <div class="card-arrow">›</div>
      </a>
"""

    with open(LIST_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    marker = "<!-- NEW_ENTRY_HERE -->"
    if marker in content:
        content = content.replace(marker, marker + new_entry)
    else:
        content = content.replace("</main>", new_entry + "\n</main>")

    with open(LIST_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Updated list page.")


# ── 主流程 ────────────────────────────────────────────
def main():
    print(f"=== 每日热门话题生成 · {DATE_STR} ===")

    print("Step 1: Searching via TikHub API...")
    data = collect_all_data()

    total = sum(len(items) for t in data for items in t["platforms"].values())
    print(f"  Total results: {total}")

    if total == 0:
        print("  WARNING: No results collected. Check API key and endpoints.")

    print("Step 2: Generating detail page...")
    html = generate_detail_page(data)
    with open(DETAIL_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Written: {DETAIL_FILE}")

    print("Step 3: Updating list page...")
    update_list_page()

    print("=== Done ===")


if __name__ == "__main__":
    main()
