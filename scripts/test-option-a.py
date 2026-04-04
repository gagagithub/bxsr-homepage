#!/usr/bin/env python3
"""
选项A 试跑：全网香港保险资讯聚合
不区分平台，直接搜最新资讯，MiniMax 提炼热点话题
"""

import os, json, re, requests
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(BEIJING_TZ)
DATE_CN = TODAY.strftime("%Y年%-m月%-d日")

MINIMAX_API_KEY = os.environ["MINIMAX_API_KEY"]
SERPER_API_KEY = os.environ["SERPER_API_KEY"]

QUERIES = [
    "香港保险 最新 2026",
    "港险 热门话题 2026",
    "香港储蓄险 分红险 最新动态",
    "香港保险 监管 政策 2026",
    "香港保险 内地客户 趋势",
]

def search(query):
    try:
        r = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "hl": "zh-cn", "gl": "cn", "num": 8},
            timeout=15,
        )
        r.raise_for_status()
        return [{"title": i.get("title",""), "snippet": i.get("snippet",""), "link": i.get("link","")}
                for i in r.json().get("organic", [])]
    except Exception as e:
        print(f"Search failed: {e}")
        return []

def main():
    print("=== 选项A 试跑 ===")
    print(f"日期：{DATE_CN}\n")

    # 搜索
    all_results = []
    for q in QUERIES:
        print(f"搜索：{q}")
        results = search(q)
        all_results.extend(results)

    # 去重
    seen = set()
    unique = []
    for r in all_results:
        if r["title"] not in seen:
            seen.add(r["title"])
            unique.append(r)

    print(f"\n共搜到 {len(unique)} 条不重复结果\n")

    # 格式化给 MiniMax
    text = "\n".join([f"{i+1}. {r['title']}\n   {r['snippet']}\n   {r['link']}"
                      for i, r in enumerate(unique[:25])])

    # MiniMax 分析
    prompt = f"""以下是{DATE_CN}关于「香港保险」的全网最新资讯：

{text}

请提炼成今日香港保险热点摘要，输出 JSON：
{{
  "headlines": [
    {{"title": "话题标题（20字内）", "summary": "核心内容（50-80字）", "link": "来源链接"}}
  ],
  "trends": [
    {{"title": "趋势（10字内）", "detail": "说明（30-50字）"}}
  ]
}}

要求：
- headlines 提供 8-10 条，覆盖不同角度（产品、监管、市场、客户）
- trends 提供 3-5 条
- 只输出 JSON，无其他文字"""

    print("发送给 MiniMax 分析中...\n")
    resp = requests.post(
        "https://api.minimax.chat/v1/text/chatcompletion_v2",
        headers={"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"},
        json={"model": "MiniMax-M2.5", "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 4096, "temperature": 0.3},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"base_resp: {data.get('base_resp')}")

    text_out = data["choices"][0]["message"]["content"].strip()
    if "```" in text_out:
        text_out = re.sub(r"^```(?:json)?\s*", "", text_out)
        text_out = re.sub(r"\s*```\s*$", "", text_out.strip())
    s = text_out.find("{"); e = text_out.rfind("}") + 1
    if s != -1: text_out = text_out[s:e]

    try:
        result = json.loads(text_out)
    except:
        from json_repair import repair_json
        result = json.loads(repair_json(text_out))

    print("\n======== 今日香港保险热点 ========\n")
    for i, h in enumerate(result.get("headlines", []), 1):
        print(f"{i}. {h['title']}")
        print(f"   {h['summary']}")
        print(f"   {h.get('link','')}\n")

    print("======== 趋势观察 ========\n")
    for t in result.get("trends", []):
        print(f"▶ {t['title']}")
        print(f"  {t['detail']}\n")

if __name__ == "__main__":
    main()
