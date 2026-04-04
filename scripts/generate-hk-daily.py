#!/usr/bin/env python3
"""
香港保险日报 - 自动生成脚本
每天 10:00 (北京时间) 由 GitHub Actions 调用，
通过 Serper 搜索 + MiniMax 分析，生成两列对比日报 HTML：
  左列：关键词「香港保险」各平台 Top5
  右列：关键词「存款」各平台 Top5
"""

import os
import json
import re
import time
import requests
from datetime import datetime, timezone, timedelta

# ── 配置 ──────────────────────────────────────────────
BEIJING_TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(BEIJING_TZ)
DATE_STR = TODAY.strftime("%Y-%m-%d")
DATE_CN = TODAY.strftime("%Y年%-m月%-d日")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETAIL_FILE = os.path.join(PROJECT_ROOT, f"hk-insurance-{DATE_STR}.html")
LIST_FILE = os.path.join(PROJECT_ROOT, "hk-insurance-reports.html")

MINIMAX_API_KEY = os.environ["MINIMAX_API_KEY"]
SERPER_API_KEY = os.environ["SERPER_API_KEY"]

MINIMAX_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"
MINIMAX_MODEL = "MiniMax-M2.5"

PLATFORMS = [
    ("xiaohongshu", "小红书"),
    ("douyin",      "抖音"),
    ("toutiao",     "今日头条"),
    ("bilibili",    "B站"),
    ("shipinhao",   "微信视频号"),
]

# ── Serper 搜索 ───────────────────────────────────────
def serper_search(query, num=8):
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "hl": "zh-cn", "gl": "cn", "num": num},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {"title": item.get("title", ""), "snippet": item.get("snippet", ""), "link": item.get("link", "")}
            for item in data.get("organic", [])
        ]
    except Exception as e:
        print(f"  Serper search failed for '{query}': {e}")
        return []


def collect_search_results():
    """分别搜索「香港保险」和「存款」两个关键词，每个关键词覆盖5个平台。"""
    results = {"hk_insurance": {}, "deposits": {}}

    PLATFORM_QUERIES = {
        "hk_insurance": [
            ("xiaohongshu", f"小红书 香港保险 港险 {DATE_STR[:7]}"),
            ("douyin",      f"抖音 香港保险 港险 {DATE_STR[:7]}"),
            ("toutiao",     f"今日头条 香港保险 港险 {DATE_STR[:7]}"),
            ("bilibili",    f"site:bilibili.com 香港保险 OR 港险"),
            ("shipinhao",   f"微信视频号 香港保险 港险 {DATE_STR[:7]}"),
        ],
        "deposits": [
            ("xiaohongshu", f"小红书 存款 银行理财 {DATE_STR[:7]}"),
            ("douyin",      f"抖音 存款 银行理财 {DATE_STR[:7]}"),
            ("toutiao",     f"今日头条 存款 银行理财 {DATE_STR[:7]}"),
            ("bilibili",    f"site:bilibili.com 存款 银行理财"),
            ("shipinhao",   f"微信视频号 存款 银行理财 {DATE_STR[:7]}"),
        ],
    }

    for group, queries in PLATFORM_QUERIES.items():
        for platform_key, query in queries:
            print(f"  [{group}] {platform_key}: {query}")
            results[group][platform_key] = serper_search(query)
            time.sleep(0.4)

    return results


def format_results_for_prompt(results):
    """将搜索结果格式化成给 MiniMax 的文本。"""
    text_parts = []
    group_names = {"hk_insurance": "香港保险", "deposits": "存款"}
    platform_names = {k: v for k, v in PLATFORMS}

    for group_key, group_name in group_names.items():
        text_parts.append(f"\n\n===== 关键词：{group_name} =====")
        for platform_key, platform_name in PLATFORMS:
            items = results[group_key].get(platform_key, [])
            text_parts.append(f"\n--- {platform_name} ---")
            for i, r in enumerate(items[:6], 1):
                text_parts.append(f"{i}. {r['title']}")
                text_parts.append(f"   摘要：{r['snippet']}")
                text_parts.append(f"   链接：{r['link']}")

    return "\n".join(text_parts)


# ── MiniMax 分析 ──────────────────────────────────────
def analyze_with_minimax(search_text):
    prompt = f"""以下是今天（{DATE_CN}）的搜索结果，分两组关键词：「香港保险」和「存款」，每组覆盖小红书、抖音、今日头条、B站、微信视频号。

{search_text}

请整理成结构化 JSON，只输出 JSON，不要有任何其他文字或 markdown 包裹。格式：
{{
  "date": "{DATE_STR}",
  "hk_insurance": {{
    "xiaohongshu": [
      {{"title": "内容标题", "summary": "15字内摘要", "link": "原始链接或空字符串"}}
    ],
    "douyin": [...],
    "toutiao": [...],
    "bilibili": [...],
    "shipinhao": [...]
  }},
  "deposits": {{
    "xiaohongshu": [...],
    "douyin": [...],
    "toutiao": [...],
    "bilibili": [...],
    "shipinhao": [...]
  }}
}}

要求：
- 每个平台每组提供 Top 5 条目
- title 保留原标题，不要截断
- summary 控制在 15 字以内
- link 填入对应的搜索结果链接，无则留空字符串
- 只输出 JSON"""

    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MINIMAX_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8192,
        "temperature": 0.2,
    }

    resp = requests.post(MINIMAX_URL, headers=headers, json=payload, timeout=120)
    if not resp.ok:
        print(f"  MiniMax HTTP error {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()

    data = resp.json()
    print(f"  base_resp: {data.get('base_resp')}")

    choices = data.get("choices")
    if not choices:
        raise ValueError(f"MiniMax no choices. Response: {json.dumps(data, ensure_ascii=False)[:400]}")

    result_text = choices[0]["message"]["content"].strip()
    print(f"  Raw (first 200): {result_text[:200]}")

    if "```" in result_text:
        result_text = re.sub(r"^```(?:json)?\s*", "", result_text)
        result_text = re.sub(r"\s*```\s*$", "", result_text.strip())

    start = result_text.find("{")
    end = result_text.rfind("}") + 1
    if start != -1 and end > start:
        result_text = result_text[start:end]

    try:
        return json.loads(result_text)
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}, trying json_repair...")
        from json_repair import repair_json
        return json.loads(repair_json(result_text))


# ── HTML 生成 ─────────────────────────────────────────
PLATFORM_DOT_COLOR = {
    "xiaohongshu": "#FF2442",
    "douyin":      "#1A1A1A",
    "toutiao":     "#F04142",
    "bilibili":    "#00A1D6",
    "shipinhao":   "#07C160",
}

def esc(text):
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def build_platform_section(platform_key, platform_name, items):
    dot_color = PLATFORM_DOT_COLOR.get(platform_key, "#888")
    rows = ""
    for i, item in enumerate(items[:5], 1):
        link = item.get("link", "").strip()
        title_html = (
            f'<a href="{esc(link)}" target="_blank" rel="noopener" class="tl">{esc(item["title"])}</a>'
            if link else esc(item["title"])
        )
        summary = esc(item.get("summary", ""))
        rows += f"""<tr>
            <td>{i}</td>
            <td>{title_html}<br><span class="m">{summary}</span></td>
          </tr>"""

    return f"""<div class="cs">
      <div class="cpt" style="color:{dot_color};">● {platform_name}</div>
      <table><tbody>{rows}</tbody></table>
    </div>"""


def generate_detail_page(data):
    hk = data.get("hk_insurance", {})
    dep = data.get("deposits", {})

    hk_sections = ""
    dep_sections = ""
    for key, name in PLATFORMS:
        hk_sections += build_platform_section(key, name, hk.get(key, []))
        dep_sections += build_platform_section(key, name, dep.get(key, []))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>香港保险日报 · {DATE_STR}</title>
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

    .two-col {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      align-items: start;
    }}

    .col {{
      background: white;
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 0 2px 12px rgba(0,0,0,0.07);
    }}

    .ch {{
      padding: 14px 20px;
      font-size: 1.05em;
      font-weight: 700;
      text-align: center;
      letter-spacing: 1px;
    }}
    .col-hk .ch {{ background: #6366f1; color: white; }}
    .col-dep .ch {{ background: #f59e0b; color: white; }}

    .cs {{
      padding: 12px 16px;
      border-bottom: 1px solid #f0f0f0;
    }}
    .cs:last-child {{ border-bottom: none; }}

    .cpt {{
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
    td:first-child {{ color: #ccc; width: 18px; font-weight: 600; }}
    tr:last-child td {{ border-bottom: none; }}

    .tl {{
      color: #1E2761;
      text-decoration: none;
      font-weight: 500;
      line-height: 1.4;
    }}
    .tl:hover {{ color: #028090; text-decoration: underline; }}
    .m {{ color: #999; font-size: 0.88em; }}

    footer {{
      text-align: center;
      color: #aaa;
      font-size: 0.8em;
      padding: 20px;
    }}

    @media (max-width: 680px) {{
      .two-col {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <a class="back-btn" href="hk-insurance-reports.html">← 返回列表</a>
    <h1>🇭🇰 香港保险日报 · 每日热门内容榜单</h1>
    <div class="header-sub">{DATE_CN} · 抖音 / 小红书 / B站 / 今日头条 / 微信视频号</div>
  </div>
</header>

<main>
  <div class="two-col">
    <div class="col col-hk">
      <div class="ch">🔍 香港保险</div>
      {hk_sections}
    </div>
    <div class="col col-dep">
      <div class="ch">💰 存款</div>
      {dep_sections}
    </div>
  </div>
</main>

<footer>🔄 每日 10:00 自动更新 · Serper + MiniMax</footer>

</body>
</html>"""


# ── 更新列表页 ────────────────────────────────────────
def update_list_page():
    day = TODAY.day
    month = TODAY.month

    new_entry = f"""
      <a class="report-card" href="hk-insurance-{DATE_STR}.html">
        <div class="card-icon">📰</div>
        <div class="card-body">
          <div class="card-title">香港保险日报 &nbsp;&middot;&nbsp; {month}月{day}日</div>
          <div class="card-meta">
            <span>📅 {DATE_CN}</span>
            <span>🤖 香港保险 vs 存款</span>
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
        content = content.replace("</main>", new_entry + "\n</main>")

    with open(LIST_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Updated list page.")


# ── 主流程 ────────────────────────────────────────────
def main():
    print(f"=== 香港保险日报生成 · {DATE_STR} ===")

    print("Step 1: Searching with Serper...")
    results = collect_search_results()
    search_text = format_results_for_prompt(results)
    print(f"  - Search text: {len(search_text)} chars")

    print("Step 2: Analyzing with MiniMax...")
    data = analyze_with_minimax(search_text)
    data.setdefault("hk_insurance", {})
    data.setdefault("deposits", {})
    for key, _ in PLATFORMS:
        data["hk_insurance"].setdefault(key, [])
        data["deposits"].setdefault(key, [])

    for key, name in PLATFORMS:
        print(f"  - hk/{key}: {len(data['hk_insurance'][key])} items")
        print(f"  - dep/{key}: {len(data['deposits'][key])} items")

    print("Step 3: Generating detail page...")
    html = generate_detail_page(data)
    with open(DETAIL_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  - Written: {DETAIL_FILE}")

    print("Step 4: Updating list page...")
    update_list_page()

    print("=== Done ===")


if __name__ == "__main__":
    main()
