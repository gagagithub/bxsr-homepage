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
            return resp.json()
        print(f"  API error {resp.status_code}: {resp.text[:300]}")
        return None
    except Exception as e:
        print(f"  API request failed: {e}")
        return None


def search_xigua(keyword):
    """搜索西瓜视频"""
    data = tikhub_get("/api/v1/xigua/app/v2/search_video", {"keyword": keyword})
    if not data:
        return []
    items = []
    # 尝试从不同层级提取数据
    raw_list = (data.get("data", {}).get("data", [])
                or data.get("data", {}).get("video_list", [])
                or data.get("data", []))
    if isinstance(raw_list, dict):
        raw_list = raw_list.get("data", [])
    for item in raw_list[:15]:
        if isinstance(item, dict):
            title = (item.get("title") or item.get("video_title")
                     or item.get("content", ""))
            url = item.get("share_url") or item.get("url") or ""
            play = item.get("play_count", 0) or item.get("video_watch_count", 0)
            like = item.get("digg_count", 0) or item.get("like_count", 0)
            if title:
                items.append({"title": title, "url": url, "play": play, "like": like})
    return items


def search_bilibili(keyword):
    """搜索B站"""
    data = tikhub_get("/api/v1/bilibili/web/fetch_general_search", {
        "keyword": keyword, "page": 1, "page_size": 15
    })
    if not data:
        return []
    items = []
    raw_list = (data.get("data", {}).get("result", [])
                or data.get("data", {}).get("data", [])
                or data.get("data", []))
    if isinstance(raw_list, dict):
        raw_list = raw_list.get("result", [])
    for item in raw_list[:15]:
        if isinstance(item, dict):
            title = (item.get("title") or item.get("name") or "")
            # 清除B站搜索结果中的高亮标签
            title = title.replace('<em class="keyword">', '').replace('</em>', '')
            bvid = item.get("bvid", "")
            url = f"https://www.bilibili.com/video/{bvid}" if bvid else item.get("arcurl", "")
            play = item.get("play", 0) or item.get("view", 0)
            like = item.get("like", 0) or item.get("favorites", 0)
            if title and item.get("type", "") != "bili_user":
                items.append({"title": title, "url": url, "play": play, "like": like})
    return items


def search_douyin(keyword):
    """搜索抖音"""
    data = tikhub_get("/api/v1/douyin/app/v1/fetch_general_search_result", {
        "keyword": keyword
    })
    if not data:
        return []
    items = []
    raw_list = (data.get("data", {}).get("data", [])
                or data.get("data", []))
    if isinstance(raw_list, dict):
        raw_list = raw_list.get("data", [])
    for item in raw_list[:15]:
        if isinstance(item, dict):
            # 抖音搜索结果可能嵌套在 aweme_info 中
            aweme = item.get("aweme_info", item)
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
    data = tikhub_get("/api/v1/xiaohongshu/web_v2/fetch_search_notes", {
        "keywords": keyword, "sort_type": "popularity_descending"
    })
    if not data:
        return []
    items = []
    raw_list = (data.get("data", {}).get("items", [])
                or data.get("data", {}).get("notes", [])
                or data.get("data", {}).get("data", [])
                or data.get("data", []))
    if isinstance(raw_list, dict):
        raw_list = raw_list.get("items", []) or raw_list.get("notes", [])
    for item in raw_list[:15]:
        if isinstance(item, dict):
            note = item.get("note_card", item)
            title = note.get("display_title") or note.get("title") or note.get("desc", "")
            note_id = note.get("note_id") or item.get("id", "")
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
