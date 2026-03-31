#!/usr/bin/env python3
"""
香港保险日报 - 自动生成脚本
每天 10:00 (北京时间) 由 GitHub Actions 调用，
通过 Claude API + Web Search 搜集全网「香港保险」热门话题，
生成 HTML 详情页并更新列表页。
"""

import os
import json
import re
from datetime import datetime, timezone, timedelta

import anthropic

# ── 配置 ──────────────────────────────────────────────
BEIJING_TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(BEIJING_TZ)
DATE_STR = TODAY.strftime("%Y-%m-%d")           # 2026-03-30
DATE_CN = TODAY.strftime("%Y年%-m月%-d日")        # 2026年3月30日
MONTH_NUM = TODAY.month
YEAR_NUM = TODAY.year

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETAIL_FILE = os.path.join(PROJECT_ROOT, f"hk-insurance-{DATE_STR}.html")
LIST_FILE = os.path.join(PROJECT_ROOT, "hk-insurance-reports.html")

PLATFORMS = ["小红书", "抖音", "今日头条", "B站", "微信视频号"]

# ── Claude API 调用 ───────────────────────────────────
def fetch_daily_report():
    """调用 Claude API + web_search 搜集香港保险热门话题，返回结构化 JSON。"""
    client = anthropic.Anthropic()

    system_prompt = f"""你是一个专业的香港保险市场研究助手。今天是 {DATE_CN}。

你的任务：搜索全网关于「香港保险」的最新热门话题和讨论，覆盖以下5个平台：
1. 小红书
2. 抖音
3. 今日头条
4. B站（哔哩哔哩）
5. 微信视频号

请使用 web_search 工具搜索每个平台上关于「香港保险」「港险」「香港储蓄险」「香港分红险」的热门内容。

搜索完成后，请输出严格的 JSON（不要有任何 markdown 包裹），格式如下：
{{
  "date": "{DATE_STR}",
  "key_points": [
    {{
      "title": "要点标题（15字以内）",
      "detail": "要点详细描述（50-80字）"
    }}
  ],
  "platforms": {{
    "xiaohongshu": {{
      "name": "小红书",
      "items": [
        {{
          "title": "内容标题",
          "type": "笔记/视频/...",
          "summary": "核心摘要（30-50字）"
        }}
      ],
      "note": "平台特别提示（可选）"
    }},
    "douyin": {{
      "name": "抖音",
      "items": [...],
      "note": "..."
    }},
    "toutiao": {{
      "name": "今日头条",
      "items": [...],
      "note": "..."
    }},
    "bilibili": {{
      "name": "B站",
      "items": [...],
      "note": "..."
    }},
    "shipinhao": {{
      "name": "视频号",
      "items": [...],
      "note": "..."
    }}
  }},
  "trends": [
    {{
      "title": "趋势标题（10字以内）",
      "detail": "趋势描述（30-60字）"
    }}
  ]
}}

要求：
- key_points 提供 5 条今日要点
- 每个平台提供 Top 5 热门内容
- trends 提供 3-5 条趋势观察
- type 字段使用：笔记、短视频、视频、中长视频、文章、教程
- 如有疑似推广内容，在标题后标注 [疑似推广]
- 内容必须基于你搜索到的真实信息，不要编造
- 如果某个平台搜索不到足够内容，可以基于搜到的相关信息合理推断热门方向
- 只输出 JSON，不要有其他文字"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 15}],
        messages=[{"role": "user", "content": system_prompt}],
    )

    # 提取最终文本（跳过 tool_use / tool_result 块）
    result_text = ""
    for block in message.content:
        if block.type == "text":
            result_text += block.text

    # 清理可能的 markdown 包裹
    result_text = result_text.strip()
    if result_text.startswith("```"):
        result_text = re.sub(r"^```(?:json)?\s*", "", result_text)
        result_text = re.sub(r"\s*```$", "", result_text)

    return json.loads(result_text)


# ── HTML 生成 ─────────────────────────────────────────
TYPE_CLASS_MAP = {
    "笔记":   "type-note",
    "短视频": "type-video",
    "视频":   "type-video",
    "中长视频":"type-video",
    "文章":   "type-article",
    "教程":   "type-tutorial",
}

PLATFORM_CSS_MAP = {
    "xiaohongshu": "platform-xiaohongshu",
    "douyin":      "platform-douyin",
    "toutiao":     "platform-toutiao",
    "bilibili":    "platform-bilibili",
    "shipinhao":   "platform-shipinhao",
}


def build_key_points_html(key_points):
    items = ""
    for i, kp in enumerate(key_points, 1):
        items += f"""
    <div class="key-point">
      <div class="kp-num">{i}</div>
      <div class="kp-content">
        <div class="kp-title">{esc(kp['title'])}</div>
        <div class="kp-detail">{esc(kp['detail'])}</div>
      </div>
    </div>"""
    return items


def build_platform_html(key, platform):
    css_class = PLATFORM_CSS_MAP.get(key, "")
    name = platform["name"]
    rows = ""
    for i, item in enumerate(platform.get("items", [])[:5], 1):
        type_cls = TYPE_CLASS_MAP.get(item["type"], "type-note")
        rows += f"""
        <tr>
          <td>{i}</td>
          <td class="td-title">{esc(item['title'])}</td>
          <td><span class="td-type {type_cls}">{esc(item['type'])}</span></td>
          <td>{esc(item['summary'])}</td>
        </tr>"""

    note_html = ""
    if platform.get("note"):
        note_html = f'\n    <div class="platform-note">&#9888; {esc(platform["note"])}</div>'

    return f"""
  <div class="platform-section {css_class}">
    <div class="platform-header">
      <div class="platform-dot"></div>
      <div class="platform-name">{esc(name)} Top 5</div>
    </div>
    <table class="content-table">
      <thead><tr><th>#</th><th>标题</th><th>类型</th><th>核心摘要</th></tr></thead>
      <tbody>{rows}
      </tbody>
    </table>{note_html}
  </div>"""


def build_trends_html(trends):
    items = ""
    for t in trends:
        items += f"""
    <div class="trend-item">
      <div class="trend-title">{esc(t['title'])}</div>
      <div class="trend-detail">{esc(t['detail'])}</div>
    </div>"""
    return items


def esc(text):
    """基本 HTML 转义。"""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def generate_detail_page(data):
    """根据结构化数据生成完整的 HTML 详情页。"""
    key_points_html = build_key_points_html(data["key_points"])

    platforms_html = ""
    for key in ["xiaohongshu", "douyin", "toutiao", "bilibili", "shipinhao"]:
        if key in data["platforms"]:
            platforms_html += build_platform_html(key, data["platforms"][key])

    trends_html = build_trends_html(data["trends"])

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>香港保险日报 &middot; {DATE_STR}</title>
  <style>
    :root {{
      --navy:  #1E2761;
      --teal:  #028090;
      --mint:  #02C39A;
      --off:   #F4F6FB;
      --gray:  #8892A4;
      --dark:  #111827;
      --white: #FFFFFF;
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background: var(--off);
      color: var(--dark);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }}

    header {{
      background: linear-gradient(135deg, var(--navy) 0%, #162050 60%, var(--teal) 100%);
      color: var(--white);
      padding: 40px 40px 36px;
      position: relative;
      overflow: hidden;
    }}
    header::before {{
      content: "";
      position: absolute;
      inset: 0;
      background: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.03'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
    }}
    .header-inner {{
      position: relative;
      z-index: 1;
      max-width: 900px;
      margin: 0 auto;
    }}
    .header-top {{
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 16px;
    }}
    .back-btn {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(255,255,255,.12);
      border: 1px solid rgba(255,255,255,.2);
      border-radius: 100px;
      padding: 6px 14px;
      font-size: 13px;
      color: rgba(255,255,255,.8);
      text-decoration: none;
      transition: background .15s;
      white-space: nowrap;
    }}
    .back-btn:hover {{ background: rgba(255,255,255,.2); }}
    header h1 {{ font-size: clamp(20px,3vw,28px); font-weight: 700; }}
    .header-sub {{ font-size: 13px; opacity: .6; margin-top: 4px; }}
    .header-tags {{ margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }}
    .header-tag {{
      font-size: 11px;
      padding: 3px 10px;
      border-radius: 100px;
      background: rgba(255,255,255,.12);
      color: rgba(255,255,255,.75);
      border: 1px solid rgba(255,255,255,.15);
    }}

    main {{
      flex: 1;
      max-width: 900px;
      width: 100%;
      margin: 0 auto;
      padding: 32px 24px 80px;
    }}

    .key-points {{
      background: var(--white);
      border-radius: 16px;
      border: 1.5px solid #E8EDF8;
      padding: 28px 28px 24px;
      margin-bottom: 28px;
    }}
    .section-title {{
      font-size: 18px;
      font-weight: 700;
      color: var(--navy);
      margin-bottom: 18px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .section-title .icon {{
      width: 28px;
      height: 28px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      flex-shrink: 0;
    }}
    .key-point {{
      display: flex;
      gap: 14px;
      padding: 14px 0;
      border-bottom: 1px solid #F0F3FA;
    }}
    .key-point:last-child {{ border-bottom: none; }}
    .kp-num {{
      width: 26px;
      height: 26px;
      border-radius: 8px;
      background: linear-gradient(135deg, var(--navy), var(--teal));
      color: var(--white);
      font-size: 13px;
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      margin-top: 1px;
    }}
    .kp-content {{ flex: 1; }}
    .kp-title {{
      font-size: 14px;
      font-weight: 700;
      color: var(--dark);
      margin-bottom: 4px;
    }}
    .kp-detail {{
      font-size: 13px;
      color: var(--gray);
      line-height: 1.7;
    }}

    .platform-section {{
      background: var(--white);
      border-radius: 16px;
      border: 1.5px solid #E8EDF8;
      padding: 28px;
      margin-bottom: 20px;
    }}
    .platform-header {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 18px;
    }}
    .platform-dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex-shrink: 0;
    }}
    .platform-name {{
      font-size: 17px;
      font-weight: 700;
      color: var(--navy);
    }}

    .platform-xiaohongshu .platform-dot {{ background: #FF2442; }}
    .platform-douyin .platform-dot      {{ background: #1A1A1A; }}
    .platform-toutiao .platform-dot     {{ background: #F04142; }}
    .platform-bilibili .platform-dot    {{ background: #00A1D6; }}
    .platform-shipinhao .platform-dot   {{ background: #07C160; }}

    .content-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    .content-table thead th {{
      text-align: left;
      font-size: 12px;
      font-weight: 600;
      color: var(--gray);
      padding: 8px 10px;
      border-bottom: 2px solid #E8EDF8;
    }}
    .content-table thead th:first-child {{
      width: 36px;
      text-align: center;
    }}
    .content-table thead th:nth-child(3) {{ width: 60px; }}
    .content-table tbody tr {{
      border-bottom: 1px solid #F4F6FB;
      transition: background .1s;
    }}
    .content-table tbody tr:hover {{ background: #F8FAFF; }}
    .content-table tbody td {{
      padding: 12px 10px;
      vertical-align: top;
      line-height: 1.6;
    }}
    .content-table tbody td:first-child {{
      text-align: center;
      font-weight: 700;
      color: var(--teal);
    }}
    .content-table .td-title {{
      font-weight: 600;
      color: var(--dark);
    }}
    .content-table .td-type {{
      font-size: 11px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 100px;
      display: inline-block;
      white-space: nowrap;
    }}
    .type-note    {{ background: #FFF0F0; color: #D32F2F; }}
    .type-video   {{ background: #E8F5FF; color: #0277BD; }}
    .type-article {{ background: #FFF8E1; color: #F57F17; }}
    .type-tutorial{{ background: #E8F5E9; color: #2E7D32; }}

    .platform-note {{
      font-size: 12px;
      color: var(--gray);
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid #F0F3FA;
      line-height: 1.6;
    }}

    .trends-section {{
      background: var(--white);
      border-radius: 16px;
      border: 1.5px solid #E8EDF8;
      padding: 28px;
      margin-bottom: 20px;
    }}
    .trend-item {{
      padding: 14px 0;
      border-bottom: 1px solid #F0F3FA;
    }}
    .trend-item:last-child {{ border-bottom: none; }}
    .trend-title {{
      font-size: 14px;
      font-weight: 700;
      color: var(--teal);
      margin-bottom: 4px;
    }}
    .trend-detail {{
      font-size: 13px;
      color: var(--gray);
      line-height: 1.7;
    }}

    .disclaimer {{
      text-align: center;
      font-size: 12px;
      color: var(--gray);
      padding: 20px 24px 0;
      line-height: 1.6;
    }}
    footer {{
      background: var(--navy);
      color: rgba(255,255,255,.4);
      text-align: center;
      padding: 28px 24px;
      font-size: 12px;
      letter-spacing: 1px;
      margin-top: 40px;
    }}

    @media (max-width: 600px) {{
      header {{ padding: 28px 20px 24px; }}
      main {{ padding: 20px 14px 60px; }}
      .key-points, .platform-section, .trends-section {{ padding: 20px 16px; }}
      .content-table {{ font-size: 12px; }}
      .content-table thead th:nth-child(4),
      .content-table tbody td:nth-child(4) {{ display: none; }}
    }}
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <div class="header-top">
      <a class="back-btn" href="hk-insurance-reports.html">&larr; 返回列表</a>
    </div>
    <h1>香港保险 &middot; 每日热门资讯</h1>
    <div class="header-sub">{DATE_CN} &nbsp;&middot;&nbsp; AI 自动整理，仅供参考</div>
    <div class="header-tags">
      <span class="header-tag">储蓄险</span>
      <span class="header-tag">分红险</span>
      <span class="header-tag">美元保单</span>
      <span class="header-tag">理财类产品</span>
    </div>
  </div>
</header>

<main>

  <div class="key-points">
    <div class="section-title">
      <span class="icon" style="background:linear-gradient(135deg,var(--navy),var(--teal));color:#fff;">&#9654;</span>
      今日要点
    </div>
{key_points_html}
  </div>

{platforms_html}

  <div class="trends-section">
    <div class="section-title">
      <span class="icon" style="background:linear-gradient(135deg,#C0507A,#7B2D8B);color:#fff;">&#9670;</span>
      趋势观察
    </div>
{trends_html}
  </div>

  <div class="disclaimer">
    本报告由 AI 自动搜集整理，内容来源于公开网络信息，仅供参考，不构成投资建议。<br>
    生成时间：{DATE_STR}
  </div>

</main>

<footer>
  保心上人 &middot; CONFIDENTIAL &middot; INTERNAL USE ONLY
</footer>

</body>
</html>"""
    return html


# ── 更新列表页 ────────────────────────────────────────
def update_list_page():
    """在 hk-insurance-reports.html 的 <!-- NEW_ENTRY_HERE --> 标记后插入新条目。"""
    day = TODAY.day
    month = TODAY.month

    new_entry = f"""
      <a class="report-card" href="hk-insurance-{DATE_STR}.html">
        <div class="card-icon">📰</div>
        <div class="card-body">
          <div class="card-title">香港保险 &middot; 每日热门资讯 &nbsp;&middot;&nbsp; {month}月{day}日</div>
          <div class="card-meta">
            <span>📅 {DATE_CN}</span>
            <span>🤖 AI 自动整理</span>
          </div>
        </div>
        <div class="card-tags">
          <span class="tag tag-xhs">小红书</span>
          <span class="tag tag-douyin">抖音</span>
          <span class="tag tag-bili">B站</span>
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
        print("WARNING: marker not found in list page, appending before </main>")
        content = content.replace("</main>", new_entry + "\n</main>")

    with open(LIST_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Updated list page: {LIST_FILE}")


# ── 主流程 ────────────────────────────────────────────
def main():
    print(f"=== 香港保险日报生成 · {DATE_STR} ===")

    # 1. 调用 Claude API 搜集内容
    print("Step 1: Calling Claude API with web search...")
    data = fetch_daily_report()
    print(f"  - Got {len(data['key_points'])} key points")
    for key in ["xiaohongshu", "douyin", "toutiao", "bilibili", "shipinhao"]:
        count = len(data["platforms"].get(key, {}).get("items", []))
        print(f"  - {key}: {count} items")
    print(f"  - Got {len(data['trends'])} trends")

    # 2. 生成 HTML 详情页
    print("Step 2: Generating detail page...")
    html = generate_detail_page(data)
    with open(DETAIL_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  - Written to: {DETAIL_FILE}")

    # 3. 更新列表页
    print("Step 3: Updating list page...")
    update_list_page()

    print("=== Done ===")


if __name__ == "__main__":
    main()
