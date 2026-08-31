# -*- coding: utf-8 -*-
"""财经晨报·取真实新闻电报(东方财富全球财经快讯, 游标翻页, 带标题+摘要+时间+原文链接)。
仅采集真实信源, 不生成任何内容。AI 只在 llm_morning.py 里做分类/去重/挑重点, 绝不编数字。
输出 news_raw.json。带 signal.alarm 硬超时 + 重试(偶发 RemoteDisconnected)。"""
import os
BASE = os.path.dirname(os.path.abspath(__file__))
import socket; socket.setdefaulttimeout(25)
import signal, json, time, sys, re
import akshare as ak
import requests
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

# ---- 时间窗: 保留【昨天全天 + 今天早上】的新闻(取数要按它翻页, 故先算) ----
# ⚠2026-08-31 由日报(16:00跑, 窗口"今天全天")改回晨报(07:00跑): 早上7点"今天"几乎没有新闻,
#   窗口必须往前推到昨天0点 —— 覆盖昨天白天A股/国内的事 + 昨夜隔夜外盘 + 今天早上。
#   (历史: 6-25 晨报版是"近30小时"; 7-28 改下午发时收成"今天全天"; 现在回到晨报, 用自然日边界
#    比"近31小时"更好懂, 也保证昨天早盘的新闻不会因为跑的时刻不同而时有时无。)
# 时区: Action 跑在 UTC runner, 而东财/新浪的时间戳是北京时间, 必须换算后再比, 否则差 8 小时。
BJ_NOW = datetime.utcnow() + timedelta(hours=8)
BJ_TODAY0 = BJ_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
BJ_YDAY0 = BJ_TODAY0 - timedelta(days=1)
CUTOFF = BJ_YDAY0.strftime("%Y-%m-%d %H:%M:%S")

items = []
seen = set()

# ---- 主源: 东方财富全球财经快讯(游标翻页, 取到覆盖时间窗为止) ----
# ⚠单次只回最新 200 条 —— 实测白天只覆盖 **5.1 小时**(8-31 那次 11:56→17:00)。16:00 跑日报时
#   够用(最近5小时正好是当天下午), 但 07:00 跑晨报时 200 条只能回到昨夜, **昨天白天的新闻
#   整段取不到**, 而晨报的正题恰恰是回顾昨天 —— 改时刻不改这里, 明早就是一篇没有昨天的晨报。
#   接口的 sortEnd 是游标(传上一页末条的 realSort), 实测 5 页可回溯 3 天。akshare 那个函数
#   内部就是这个 URL 写死 sortEnd="", 所以这里直连翻页, 失败再回退 akshare 拿第一页。
EM_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"

def em_page(sort_end):
    p = {"client": "web", "biz": "web_724", "fastColumn": "102",
         "sortEnd": sort_end, "pageSize": "200", "req_trace": "1710315450384"}
    return requests.get(EM_URL, params=p, timeout=25).json()["data"]["fastNewsList"]

raw, cur = [], ""
for pg in range(1, 9):          # 上限 8 页(1600条), 防翻页失控
    lst = retry(f"东财快讯第{pg}页", lambda: em_page(cur))
    if not lst:
        break
    raw += lst
    print(f"  东财第{pg}页 {len(lst)} 条  {lst[-1]['showTime']} → {lst[0]['showTime']}")
    if lst[-1]["showTime"] < CUTOFF:
        break                   # 已翻过窗口起点
    cur = lst[-1]["realSort"]

if not raw:                     # 直连全挂时的兜底: akshare 拿最新一页(只覆盖几小时, 聊胜于无)
    em = retry("stock_info_global_em(兜底)", lambda: ak.stock_info_global_em())
    if em is not None and len(em):
        raw = [{"title": r.get("标题"), "summary": r.get("摘要"),
                "showTime": r.get("发布时间"), "code": ""} for _, r in em.iterrows()]
        print(f"⚠ 翻页失败, 回退 akshare 单页 {len(raw)} 条(仅覆盖最近几小时)")

for r in raw:
    title = clean(r.get("title"))
    summ = clean(r.get("summary"))
    ts = clean(r.get("showTime"))
    code = clean(r.get("code"))
    link = code if code.startswith("http") else (
        f"https://finance.eastmoney.com/a/{code}.html" if code else "")
    body = summ or title
    if not body:
        continue
    key = body[:30]
    if key in seen:
        continue
    seen.add(key)
    items.append({"time": ts, "title": title, "text": body, "link": link, "src": "东方财富"})
print(f"东财快讯 {len(items)} 条(翻 {min(pg, 8)} 页)")

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

def parse_ts(ts):
    """东财/新浪都给 'YYYY-MM-DD HH:MM:SS'(北京时间); 解析不了返回 None(无法判断, 一律保留)。"""
    try:
        return datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def after(cutoff):
    return [it for it in items
            if (parse_ts(it["time"]) is None) or (parse_ts(it["time"]) >= cutoff)]

fresh = after(BJ_YDAY0)

# 兜底: 条数太少(长假/取数异常/时间戳格式变化)时再往前放宽一天, 宁可多给 AI 一点料也别开天窗
if len(fresh) < 60:
    print(f"⚠ 昨天+今早条数仅 {len(fresh)} 条, 放宽到近 48 小时")
    fresh = after(BJ_NOW - timedelta(hours=48))
print(f"时间窗内 {len(fresh)} 条(自 {CUTOFF} 起)")

# ---- 削减: 窗口拉长到 30+ 小时后, 全量喂 AI 会撑爆上下文 ----
# ⚠ 实测 8-31 一个工作日 00:00-17:00 就有 469 条 / 6万字, 加上夜间约 65K token;
#   deepseek-chat 上下文 64K(输入+输出), llm_morning 又是 3 次调用每次都带全量新闻 —— 不削必挂
#   (8-20~8-24 那次连挂 5 天就是 token 相关, 别再让它站在悬崖边上)。分两步:
#   ① 正则丢掉纯盘口/个股播报(本来 llm 铁律3 也要丢, 提前丢既省 token 又提高信噪比)
#   ② 仍超 MAX_CHARS 就按小时分桶等比抽稀 —— 分桶是为了**保证昨天白天不被今早挤掉**,
#      直接砍尾会把整段昨天白天砍没(那正是晨报最要讲的)。
NOISE = re.compile(
    r"涨停|跌停|封板|直线拉升|快速拉升|异动拉升|快速跳水|直线跳水|盘中异动|个股异动"
    r"|板块(拉升|走强|走弱|异动|活跃|领涨|领跌|大涨|大跌)|概念股(异动|拉升|走强)"
    r"|涨超\d|跌超\d|涨幅超\d|跌幅超\d|大幅(拉升|跳水)|集合竞价|竞价异动|振幅达"
    r"|股价创|盘初(拉升|跳水)|尾盘(拉升|跳水)|成交额突破|封涨停板"
    r"|ETF(涨|跌|盘中|份额|净值|溢价)|主力合约|期货(收盘|夜盘|主力)|龙虎榜|融资余额"
    r"|(增持|减持)(计划|股份|公告)|回购(股份|方案|进展)|股东(减持|增持|质押)|每股收益")
# ⚠抽稀绝不能碰读者最关心的那几类(和 llm_morning 提示词里的选题优先级一致): 利率/存款、
#   养老金社保医保、楼市房贷、汇率、个税退休。这些条目无条件全留, 剩下的名额再等比分。
MUST = re.compile(
    r"降准|降息|LPR|利率|存款|大额存单|国债|理财|养老金|社保|医保|长期护理|退休|年金|预定利率"
    r"|房贷|首付|楼市|房价|房地产政策|公积金|汇率|人民币|美元指数|个税|遗产|继承|保险|保费|理赔")
MAX_CHARS = 36000               # 喂 AI 的正文总字数上限(≈36K token, 给 3 次调用的输出留足空间)

def blen(it):
    return len(it.get("text") or "")

kept = [it for it in fresh if not NOISE.search(it.get("text") or "")]
print(f"过滤纯盘口/个股播报: 丢 {len(fresh) - len(kept)} 条, 余 {len(kept)} 条")

total = sum(blen(it) for it in kept)
if total > MAX_CHARS:
    must = [it for it in kept if MUST.search(it.get("text") or "")]
    must_ids = {id(it) for it in must}          # 按身份剔除, 别用 `it not in must`(dict 值比较, O(n²)+误伤同文条目)
    rest = [it for it in kept if id(it) not in must_ids]
    budget = MAX_CHARS - sum(blen(it) for it in must)
    if budget <= 0:
        picked = must                      # 极端情况: 光重点类就超了, 那就只留重点类
    else:
        buckets = {}
        for it in rest:
            buckets.setdefault(str(it.get("time"))[:13], []).append(it)
        rest_total = sum(blen(it) for it in rest) or 1
        ratio = min(1.0, budget / rest_total)
        picked = list(must)
        for _h, bk in buckets.items():
            # 桶内带【标题】的优先(东财加工过的正式快讯, 比裸播报有价值)
            bk = sorted(bk, key=lambda it: (0 if (it.get("title") or "").strip() else 1))
            picked += bk[:max(1, round(len(bk) * ratio))]
    picked.sort(key=lambda it: str(it.get("time")), reverse=True)
    print(f"抽稀: {len(kept)} 条 / {total} 字 → {len(picked)} 条 / "
          f"{sum(blen(it) for it in picked)} 字 (重点类全留 {len(must)} 条)")
    fresh = picked
else:
    fresh = kept
    print(f"未超上限({total} 字 ≤ {MAX_CHARS}), 不抽稀")

out = {
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "count": len(fresh),
    "items": fresh,
}
json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=2)
print(f"已写 {OUT} ({len(fresh)} 条 / 原始 {len(items)} 条)")
