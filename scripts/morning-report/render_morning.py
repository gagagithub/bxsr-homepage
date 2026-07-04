# -*- coding: utf-8 -*-
"""财经晨报·渲染：data.json(行情) + sections.json(AI整理的真实新闻) → morning-report.html(移动端H5)。
pub_date 走环境变量 MX_PUB_DATE(Asia/Shanghai), 缺省取本机当天。"""
import os, json, re
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(f"{BASE}/data.json"))
S = json.load(open(f"{BASE}/sections.json"))

def rr(v, n=2):
    return None if v is None else round(v, n)

def pct_cls(v):
    if v is None: return "flat"
    return "up" if v > 0 else ("down" if v < 0 else "flat")

def arrow(v):
    if v is None: return ""
    return "▲" if v > 0 else ("▼" if v < 0 else "—")

def fmt_pct(v):
    if v is None: return "—"
    return f"{v:+.2f}%"

# ---- 行情：三大指数 ----
IDX_NAME = {"DJI": "道琼斯指数", "GSPC": "标普500指数", "IXIC": "纳斯达克指数"}
indices = []
for k in ["DJI", "GSPC", "IXIC"]:
    v = D["indices"].get(k, {})
    indices.append(dict(
        name=IDX_NAME[k],
        pts=f"{v['cur']:,.2f}" if v.get("cur") is not None else "—",
        day=fmt_pct(v.get("day_pct")), ytd=fmt_pct(v.get("ytd_pct")),
        cls=pct_cls(v.get("day_pct")), ar=arrow(v.get("day_pct")),
    ))

# ---- 行情：关键数据条(利率/汇率/商品) ----
chips = []
cb = D.get("cn_bond", {})
if cb.get("cn10") is not None:
    chips.append(dict(lab="中国10年国债", val=f"{cb['cn10']:.4f}%",
                      note=f"{cb.get('cn10_bp', 0):+.1f}bp", cls=pct_cls(cb.get("cn10_bp"))))
lpr = D.get("lpr", {})
if lpr:
    chips.append(dict(lab="LPR 1Y/5Y+", val=f"{lpr['y1']:.2f}/{lpr['y5']:.2f}%", note="月度", cls="flat"))
y = D.get("yields", {}).get("Y10", {})
if y.get("level") is not None:
    chips.append(dict(lab="美债10年", val=f"{y['level']:.3f}%",
                      note=f"{y.get('bp', 0):+.1f}bp", cls=pct_cls(y.get("bp"))))
fx = D.get("forex", {})
if fx.get("CNH", {}).get("cur") is not None:
    chips.append(dict(lab="人民币 USD/CNY", val=f"{fx['CNH']['cur']:.4f}", note="中行", cls="flat"))
if fx.get("DXY", {}).get("cur") is not None:
    chips.append(dict(lab="美元指数", val=f"{fx['DXY']['cur']:.2f}",
                      note=fmt_pct(fx['DXY'].get('day_pct')), cls=pct_cls(fx['DXY'].get('day_pct'))))
co = D.get("commodities", {})
if co.get("WTI", {}).get("cur") is not None:
    chips.append(dict(lab="WTI原油", val=f"${co['WTI']['cur']:.2f}",
                      note=fmt_pct(co['WTI'].get('day_pct')), cls=pct_cls(co['WTI'].get('day_pct'))))
if co.get("GOLD", {}).get("cur") is not None:
    chips.append(dict(lab="COMEX黄金", val=f"${co['GOLD']['cur']:,.2f}",
                      note=fmt_pct(co['GOLD'].get('day_pct')), cls=pct_cls(co['GOLD'].get('day_pct'))))

# ---- 板块序号 + 配图标 ----
SEC_ICON = {"宏观经济": "🏛️", "地产动态": "🏙️", "股市盘点": "📊", "行业观察": "🔬",
            "公司要闻": "🏢", "环球视野": "🌍", "金融数据": "📈"}
sections = []
for i, sec in enumerate(S.get("sections", []), 1):
    its = [it for it in sec.get("items", []) if it.get("text")]
    if not its:
        continue
    sections.append(dict(no=f"{i:02d}", name=sec["name"], icon=SEC_ICON.get(sec["name"], "📌"), items=its))

pub_date = os.environ.get("MX_PUB_DATE") or datetime.now().strftime("%Y-%m-%d")
data_date = D.get("data_date") or pub_date

hook = S.get("hook", {}) or {}
if isinstance(hook, str):
    hook = {"big": hook}

ctx = dict(
    pub_date=pub_date, data_date=data_date,
    hook=hook,
    trend=S.get("trend", ""),
    highlights=[h for h in S.get("highlights", []) if h.get("text") or h.get("title")],
    indices=indices, chips=chips, sections=sections,
    review=S.get("review", {}),
    insure=S.get("insure", []),
)

env = Environment(loader=FileSystemLoader(BASE), autoescape=select_autoescape(["html"]))
# 文案里允许 <b>/<a> 等内联标签，单独用 |safe，不整体关 autoescape
tpl = env.get_template("template_morning.html")
html = tpl.render(**ctx)
open(f"{BASE}/morning-report.html", "w", encoding="utf-8").write(html)
print(f"已渲染 morning-report.html  日期={pub_date}  板块={len(sections)}  头条={len(ctx['highlights'])}  行情chips={len(chips)}")

# ---- 朋友圈封面图(cover.html → 截图在 run/workflow 里做) ----
def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")
def shorten(s, n):
    s = strip_tags(s)
    return s if len(s) <= n else s[:n].rstrip("，。、；,. ") + "…"
cover_items = []
for h in ctx["highlights"][:4]:
    cover_items.append(dict(label=strip_tags(h.get("label", "")), tx=shorten(h.get("text") or h.get("title"), 34)))
cover_ctx = dict(pub_date=pub_date, trend=ctx["trend"], trend_plain=shorten(ctx["trend"], 46), cover_items=cover_items)
cover_html = env.get_template("template_cover.html").render(**cover_ctx)
open(f"{BASE}/cover.html", "w", encoding="utf-8").write(cover_html)
# 朋友圈文案落地一份纯文本(供后端 add_moment_task 取用)
moment_text = S.get("moment_text", "").strip()
open(f"{BASE}/moment_text.txt", "w", encoding="utf-8").write(moment_text)
print(f"已渲染 cover.html (头条{len(cover_items)}条)  朋友圈文案{len(moment_text)}字")
