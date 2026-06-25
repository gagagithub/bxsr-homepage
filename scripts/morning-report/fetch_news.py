# -*- coding: utf-8 -*-
"""财经晨报·取真实新闻电报(东方财富全球财经快讯, ~200条/次, 带标题+摘要+时间+原文链接)。
仅采集真实信源, 不生成任何内容。AI 只在 llm_morning.py 里做分类/去重/挑重点, 绝不编数字。
输出 news_raw.json。带 signal.alarm 硬超时 + 重试(akshare 偶发 RemoteDisconnected)。"""
import os
BASE = os.path.dirname(os.path.abspath(__file__))
import socket; socket.setdefaulttimeout(25)
import signal, json, time, sys, re
import akshare as ak
from datetime import datetime, timezone, timedelta

OUT = f"{BASE}/news_raw.json"

class TO(Exception): pass
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TO()))
def retry(label, fn, tries=4, secs=25):
    last = None
    for k in range(tries):
        try:
            signal.alarm(secs); r = fn(); signal.alarm(0); return r
        except Exception as e:
            signal.alarm(0); last = e; time.sleep(1.5)
    print(f"!! {label} 失败: {type(last).__name__}: {str(last)[:90]}", file=sys.stderr)
    return None

def clean(s):
    s = str(s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

items = []
seen = set()

# ---- 主源: 东方财富全球财经快讯(200条, 标题/摘要/发布时间/链接) ----
em = retry("stock_info_global_em", lambda: ak.stock_info_global_em())
if em is not None and len(em):
    for _, r in em.iterrows():
        title = clean(r.get("标题"))
        summ = clean(r.get("摘要"))
        ts = clean(r.get("发布时间"))
        link = clean(r.get("链接"))
        body = summ or title
        if not body:
            continue
        key = body[:30]
        if key in seen:
            continue
        seen.add(key)
        items.append({"time": ts, "title": title, "text": body, "link": link, "src": "东方财富"})
    print(f"东财快讯 {len(items)} 条")

# ---- 兜底/补充: 新浪全球财经(20条, 无链接) ----
sina = retry("stock_info_global_sina", lambda: ak.stock_info_global_sina())
if sina is not None and len(sina):
    add = 0
    for _, r in sina.iterrows():
        body = clean(r.get("内容"))
        if not body:
            continue
        key = body[:30]
        if key in seen:
            continue
        seen.add(key)
        items.append({"time": clean(r.get("时间")), "title": "", "text": body, "link": "", "src": "新浪财经"})
        add += 1
    print(f"新浪补充 {add} 条")

# 只保留近 ~30 小时内(覆盖隔夜+今早), 无法解析时间的保留
def within(ts):
    try:
        t = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - t) <= timedelta(hours=30)
    except Exception:
        return True
fresh = [it for it in items if within(it["time"])]

out = {
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "count": len(fresh),
    "items": fresh,
}
json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=2)
print(f"已写 {OUT} ({len(fresh)} 条 / 原始 {len(items)} 条)")
