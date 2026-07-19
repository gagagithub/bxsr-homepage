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

# ---- 三大主题(健康/养老/传承): 每主题 = 相关新闻 + 一段解读 ----
THEME_ORDER = ["健康", "养老", "传承"]
THEME_ICON = {"健康": "🏥", "养老": "🌅", "传承": "🌳"}
THEME_SUB = {"健康": "看病住院 · 报销保障", "养老": "养老钱 · 该往哪放", "传承": "家底保值 · 稳稳传下去"}
_thmap = {t.get("name"): t for t in S.get("themes", []) if t.get("name")}
# 健康小课堂(医保/三高/体检知识, 每日一讲): 挂在健康主题末尾; 当天健康档没新闻也靠它托底出块
tip = S.get("tip", {}) or {}
if isinstance(tip, str):
    tip = {"body": tip}
themes = []
for name in THEME_ORDER:
    t = _thmap.get(name) or {}
    its = [it for it in t.get("items", []) if it.get("text")]
    insight = (t.get("insight") or "").strip()
    ttip = tip if (name == "健康" and tip.get("body")) else {}
    if not its and not insight and not ttip:
        continue
    themes.append(dict(name=name, icon=THEME_ICON.get(name, "📌"),
                       sub=THEME_SUB.get(name, ""), items=its, insight=insight, tip=ttip))

pub_date = os.environ.get("MX_PUB_DATE") or datetime.now().strftime("%Y-%m-%d")
data_date = D.get("data_date") or pub_date

# 当天电台视频版已生成 → H5 顶部嵌一个可选播放的视频入口(朋友圈链接卡固定指图文 H5, 视频入口内嵌其中)
has_video = os.path.exists(f"{BASE}/radio/renders/morning-radio.mp4")

hook = S.get("hook", {}) or {}
if isinstance(hook, str):
    hook = {"big": hook}
lead = S.get("lead", {}) or {}
if isinstance(lead, str):
    lead = {"text": lead}

ctx = dict(
    pub_date=pub_date, data_date=data_date,
    hook=hook, lead=(lead if lead.get("text") else {}),
    trend=S.get("trend", ""),
    highlights=[h for h in S.get("highlights", []) if h.get("text") or h.get("title")],
    indices=indices, chips=chips, themes=themes,
    review=S.get("review", {}),
    briefs=S.get("briefs", []) or [],
    has_video=has_video,
)

env = Environment(loader=FileSystemLoader(BASE), autoescape=select_autoescape(["html"]))
# 文案里允许 <b>/<a> 等内联标签，单独用 |safe，不整体关 autoescape
tpl = env.get_template("template_morning.html")
html = tpl.render(**ctx)
open(f"{BASE}/morning-report.html", "w", encoding="utf-8").write(html)
print(f"已渲染 morning-report.html  日期={pub_date}  主题={len(themes)}  头条={len(ctx['highlights'])}  行情chips={len(chips)}  视频入口={'有' if has_video else '无'}")

# ---- 朋友圈封面图(cover.html → 截图在 run/workflow 里做) ----
def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")
def shorten(s, n):
    s = strip_tags(s)
    return s if len(s) <= n else s[:n].rstrip("，。、；,. ") + "…"
# 封面大字优先用 hook; hook 被溯源校验丢弃时用主打小标题兜底(它过了最严校验), 都没有回退通用版
hook_big = shorten(hook.get("big", ""), 16) or shorten(lead.get("title", ""), 16)
hook_sub = shorten(hook.get("sub", ""), 30)
# 钩子大字版占空间大, 头条精选最多带 2 条; 无钩子回退老版带 4 条
cover_items = []
for h in ctx["highlights"][:(2 if hook_big else 4)]:
    cover_items.append(dict(label=strip_tags(h.get("label", "")), tx=shorten(h.get("text") or h.get("title"), 34)))
try:
    _pd = datetime.strptime(pub_date, "%Y-%m-%d")
    date_cn = f"{_pd.month}月{_pd.day}日"
except Exception:
    date_cn = pub_date
cover_ctx = dict(pub_date=pub_date, date_cn=date_cn, trend=ctx["trend"], trend_plain=shorten(ctx["trend"], 46),
                 cover_items=cover_items, hook_big=hook_big, hook_sub=hook_sub)
cover_html = env.get_template("template_cover.html").render(**cover_ctx)
open(f"{BASE}/cover.html", "w", encoding="utf-8").write(cover_html)
# 朋友圈文案落地一份纯文本(供后端 add_moment_task 取用)
moment_text = S.get("moment_text", "").strip()
open(f"{BASE}/moment_text.txt", "w", encoding="utf-8").write(moment_text)
print(f"已渲染 cover.html (头条{len(cover_items)}条)  朋友圈文案{len(moment_text)}字")
