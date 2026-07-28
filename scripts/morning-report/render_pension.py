# -*- coding: utf-8 -*-
"""养老日报·微信公众号图文渲染(发「崔伟说养老」)：sections.json → pension.html。

跟 render_wechat.py(发「崔伟说投资」的财经日报)同一套排版风格与微信约束:
- 全部内联 style(公众号会过滤 <style>/class/<script>/iframe/外链CSS)
- <b> 关键数据 → 红色加粗 span
- 不放可点外链(非白名单域名 <a> 不可跳转), 来源以纯文字标注
- 封面走草稿 thumb_media_id, 不嵌正文

内容 = 当天养老档新闻 + 那段「这跟咱有啥关系」解读 + 速览里跟养老钱有关的几条,
不含健康/传承档, 不含股市行情表(读者是 50-70 岁关心退休金的人, 不是炒股的)。
标题/导语走 sections.json 的 pension 字段(llm_morning 单独出的, 带溯源校验);
没有就回退「养老日报 X月X日」。

pub_date 走环境变量 MX_PUB_DATE(Asia/Shanghai), 缺省取本机当天。
"""
import os, json, re
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE = os.path.dirname(os.path.abspath(__file__))
S = json.load(open(f"{BASE}/sections.json", encoding="utf-8"))

# 象牙金报刊风配色(与财经日报一致, 保持两个号视觉同源)
RED    = "#b23b2e"
ORANGE = "#a8741c"
INK    = "#222019"
SUB    = "#8a8275"
PAPER  = "#faf7ef"
GOLD   = "#9c7b2e"
WARM   = "#b9791b"   # 养老主题色(沿用日报里养老档的金橙)
WARMBG = "#fdf6ea"

def emph(t):
    if not t:
        return ""
    return str(t).replace("<b>", f'<span style="color:{RED};font-weight:700;">').replace("</b>", "</span>")

def strip_tags(s):
    return re.sub(r"<[^>]+>", "", str(s or ""))

OUT = []
def w(s):
    OUT.append(s)

pub_date = os.environ.get("MX_PUB_DATE") or datetime.now().strftime("%Y-%m-%d")
try:
    dt = datetime.strptime(pub_date, "%Y-%m-%d")
    date_cn = f"{dt.year}年{dt.month}月{dt.day}日"
    week_cn = "周" + "一二三四五六日"[dt.weekday()]
    title_date = f"{dt.month}月{dt.day}日"
except Exception:
    date_cn, week_cn, title_date = pub_date, "", pub_date

# ---------- 取养老档 ----------
theme = next((t for t in S.get("themes", []) if t.get("name") == "养老"), {}) or {}
raw_items = [it for it in theme.get("items", []) if it.get("text")]
insight = (theme.get("insight") or "").strip()
pension = S.get("pension", {}) or {}
if isinstance(pension, str):
    pension = {"title": pension}

# ---------- 投资向条目硬闸 ----------
# 养老档是按"影响养老钱"选的, 里面混着大量炒股视角的条目(大盘涨跌/ETF成交/券商观点)。
# 财经日报的读者吃这套, 但「崔伟说养老」的读者是 50-70 岁关心退休金的普通人, 看到这些直接划走。
# 与传承档资本运作硬闸同一思路: 重要口径必须程序化, 光靠提示词拦不住(7-19 教训)。
_INVEST_RE = re.compile(
    r"ETF|券商|策略(官|师)|首席|研报|大盘|上证指数|深证成指|创业板指|成交额|成交量|"
    r"净流入|净流出|回购|涨停|跌停|个股|板块|估值|仓位|A股|基金分红|新发基金")
# 但只要这条同时说到读者自己的钱, 就放行(如"存款利率下调"哪怕提了一句大盘)
_MINE_RE = re.compile(
    r"养老金|退休金|社保|医保|存款利率|定期存款|大额存单|挂牌利率|房价|房租|房贷|"
    r"物价|水电|养老院|长护险|加装电梯|老旧小区|以房养老")

def keep_for_pension(it):
    t = strip_tags(it.get("label", "")) + strip_tags(it.get("text", ""))
    if _MINE_RE.search(t):
        return True
    return not _INVEST_RE.search(t)

items = [it for it in raw_items if keep_for_pension(it)]
dropped = len(raw_items) - len(items)

# 标题对应的那条置顶: 读者是被标题点进来的, 第一屏必须就是它, 否则扑空(7-25 完读率教训)。
# 匹配靠 label + 导语共有的数字, 不依赖 AI 再报一次 id。
_lead_txt = strip_tags(pension.get("lead", ""))
if _lead_txt and len(items) > 1:
    def _score(it):
        lab = strip_tags(it.get("label", ""))
        s = 2 if (lab and lab in _lead_txt) else 0
        nums = set(re.findall(r"\d+(?:\.\d+)?", strip_tags(it.get("text", ""))))
        s += len(nums & set(re.findall(r"\d+(?:\.\d+)?", _lead_txt)))
        return s
    best = max(items, key=_score)
    if _score(best) > 0:
        items = [best] + [it for it in items if it is not best]

# 速览里跟养老钱直接相关的补充条目(楼市/宏观), 最多 5 条
BRIEF_OK = {"楼市", "宏观"}
briefs = [b for b in (S.get("briefs") or []) if b.get("cat") in BRIEF_OK and str(b.get("text", "")).strip()][:5]

# ---------- 「大家最关心的」栏目: 养老金调整 ----------
# 事实卡(人工核定) + 网络讨论(联网检索, 通篇标注非官方)。
try:
    FACTS = json.load(open(f"{BASE}/pension_facts.json", encoding="utf-8"))
except Exception:
    FACTS = {}
try:
    BUZZ = json.load(open(f"{BASE}/pension_buzz.json", encoding="utf-8"))
except Exception:
    BUZZ = {}

# 闸门①: 事实卡没经崔伟核对(verified_by 为空), 整个栏目不出 —— 未核对的养老金数字不发给读者
FACTS_OK = bool(FACTS.get("verified_by"))

# 闸门②: 讨论条目里凡出现"X%"这类调整比例数字, 一律不自动发。
# 起因(2026-07-28 首次检索实测): 千问搜回来一条"发改委2026年计划报告提到按全国总体2%提高养老金",
# 而 2025 年的实际调整比例恰好也是 2% —— 计划报告里"上年回顾"与"本年安排"极易被读混。
# 这类数字一旦错, 是几万个关心退休金的老人被误导; 宁可少发一条, 也不赌它对。
# 要发这种条目, 只能由崔伟核实后手工登记进 pension_facts.json 的 confirmed_claims。
_RATE_RE = re.compile(r"\d+(?:\.\d+)?\s*[%％]")
_CONFIRMED = [str(c) for c in (FACTS.get("confirmed_claims") or [])]

def buzz_ok(it):
    t = strip_tags(it.get("text", ""))
    if not _RATE_RE.search(t):
        return True
    return any(c and c in t for c in _CONFIRMED)

buzz_all = (BUZZ.get("items") or [])
buzz = [it for it in buzz_all if buzz_ok(it)]
buzz_held = len(buzz_all) - len(buzz)
official = BUZZ.get("official") or {}

# 正文字号整体加大、行距放宽 —— 读者以 50-70 岁为主(与财经日报适老化同口径)
w(f'<section style="font-family:-apple-system,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;'
  f'color:{INK};font-size:19px;line-height:2.0;background:{PAPER};padding:2px;">')

# ---------- 报头 ----------
w(f'<p style="margin:6px 4px 14px;text-align:center;font-size:15px;color:{SUB};letter-spacing:1px;">'
  f'{date_cn} · {week_cn} · 崔伟说养老</p>')

# ---------- 导语(标题对应那条, 进文章第一眼兑现标题) ----------
if pension.get("lead"):
    w(f'<section style="margin:4px 4px 10px;padding:16px 15px;background:#fffdf7;'
      f'border:2px solid {GOLD};border-radius:10px;">')
    w(f'<p style="margin:0 0 8px;font-size:14px;font-weight:800;color:{GOLD};letter-spacing:2px;">📌 今日要点</p>')
    w(f'<p style="margin:0;font-size:19px;line-height:2.0;color:{INK};">{emph(pension["lead"])}</p>')
    w('</section>')

# ---------- 大家最关心的: 养老金调整 ----------
# 排在养老要闻之前 —— 这是读者这阵子真正天天惦记的事(崔伟 7-28)。
if FACTS_OK:
    cy = FACTS.get("current_year", {}) or {}
    w(f'<section style="margin:20px 4px 10px;padding:12px 15px;border-radius:8px;background:#8c3b2f;">')
    w(f'<span style="font-size:22px;">📣</span>'
      f'<span style="margin-left:8px;font-size:21px;font-weight:900;color:#fff;letter-spacing:2px;">'
      f'大家最关心的 · 养老金调整</span>')
    w('</section>')

    # ① 官方进展(唯一的权威锚点)
    off_txt = strip_tags(official.get("text") or cy.get("status_text") or "")
    if official.get("published") and official.get("rate"):
        off_txt = f"{off_txt}（调整比例 {strip_tags(official['rate'])}）"
    if off_txt:
        w(f'<p style="margin:0 0 6px;font-size:15px;font-weight:800;color:#8c3b2f;">✅ 官方进展</p>')
        w(f'<p style="margin:0 0 16px;font-size:19px;line-height:2.0;color:{INK};">{off_txt}</p>')

    # ② 网络讨论(非官方, 醒目标注; 含比例数字的已被闸门挡下)
    if buzz:
        w(f'<p style="margin:0 0 4px;font-size:15px;font-weight:800;color:#8c3b2f;">💬 大家都在说</p>')
        w(f'<p style="margin:0 0 10px;padding:8px 11px;background:#fdf0ee;border-left:4px solid #8c3b2f;'
          f'border-radius:4px;font-size:15px;line-height:1.7;color:#7a4a42;">'
          f'⚠ 以下为网络公开讨论的汇总，<b style="color:#8c3b2f;">均非官方消息</b>，'
          f'仅供参考。今年是否调整、何时调整、调整多少，一切以人力资源社会保障部正式发布的通知为准。</p>')
        for it in buzz:
            w(f'<p style="margin:0 0 14px;font-size:18px;line-height:1.95;color:{INK};">')
            w(f'<span style="color:{SUB};font-weight:700;">［{strip_tags(it.get("stance","网络说法"))}］</span> ')
            w(strip_tags(it.get("text")))
            s = strip_tags(it.get("src", ""))
            if s:
                w(f'<span style="color:{SUB};font-size:14px;">（据{s}）</span>')
            w('</p>')

    # ③ 往年是这样(事实卡, 人工核过)
    tr = FACTS.get("timing_rule", {}) or {}
    if tr.get("text"):
        w(f'<p style="margin:0 0 6px;font-size:15px;font-weight:800;color:#8c3b2f;">📅 往年是这样</p>')
        w(f'<p style="margin:0 0 8px;font-size:19px;line-height:2.0;color:{INK};">{strip_tags(tr["text"])}</p>')
        if tr.get("retroactive"):
            w(f'<p style="margin:0 0 14px;font-size:19px;line-height:2.0;color:{INK};">'
              f'{strip_tags(tr["retroactive"])}</p>')
    yrs = FACTS.get("annual_adjust") or []
    if yrs:
        cells = "　".join(f'{y["year"]}年 <b style="color:{RED};">{y["rate"]}</b>' for y in yrs[:6])
        w(f'<p style="margin:0 0 16px;padding:10px 12px;background:{WARMBG};border-radius:6px;'
          f'font-size:18px;line-height:2.0;color:{INK};">历年调整比例：{cells}</p>')
    wt = (FACTS.get("where_to_check") or {}).get("text")
    if wt:
        w(f'<p style="margin:0 0 6px;font-size:15px;font-weight:800;color:#8c3b2f;">🔎 怎么查自己的</p>')
        w(f'<p style="margin:0 0 6px;font-size:19px;line-height:2.0;color:{INK};">{strip_tags(wt)}</p>')

# ---------- 养老要闻 ----------
if items:
    w(f'<section style="margin:24px 4px 10px;padding:12px 15px;border-radius:8px;background:{WARM};">')
    w(f'<span style="font-size:22px;">🌅</span>'
      f'<span style="margin-left:8px;font-size:21px;font-weight:900;color:#fff;letter-spacing:2px;">今日养老要闻</span>')
    w('</section>')
    for it in items:
        label = strip_tags(it.get("label", ""))
        src = strip_tags(it.get("src", ""))
        w(f'<p style="margin:0 0 16px;font-size:19px;line-height:2.0;color:{INK};">')
        if label:
            w(f'<span style="color:{ORANGE};font-weight:700;">【{label}】</span>')
        w(emph(it.get("text")))
        if src:
            w(f'<span style="color:{SUB};font-size:14px;">（{src}）</span>')
        w('</p>')

# ---------- 这跟咱的养老钱有啥关系 ----------
if insight:
    w(f'<section style="margin:14px 4px 4px;padding:14px 15px;background:{WARMBG};'
      f'border-left:5px solid {WARM};border-radius:6px;">')
    w(f'<p style="margin:0 0 5px;font-size:15px;font-weight:800;color:{WARM};">🔍 这跟咱的养老钱有啥关系</p>')
    w(f'<p style="margin:0;font-size:19px;line-height:2.0;color:{INK};">{emph(insight)}</p>')
    w('</section>')

# ---------- 其他要闻(楼市/宏观, 一句话) ----------
if briefs:
    w(f'<section style="margin:24px 4px;padding:14px 15px 8px;background:#f7f9fc;'
      f'border:1px solid #dfe6f0;border-radius:4px;">')
    w(f'<p style="margin:0 0 8px;font-size:20px;font-weight:800;color:{INK};">⚡ 其他要闻</p>')
    for b in briefs:
        lab = strip_tags(b.get("label", ""))
        pre = f'<span style="font-weight:700;color:#6b7a94;">【{lab}】</span>' if lab else ""
        w(f'<p style="margin:0 0 8px;font-size:17px;line-height:1.9;color:{INK};">{pre}{strip_tags(b.get("text"))}</p>')
    w('</section>')

# ---------- 页脚 ----------
w(f'<section style="margin:18px 4px 8px;padding-top:14px;border-top:3px double {GOLD};text-align:center;">')
w(f'<p style="margin:0 0 6px;font-size:17px;font-weight:700;color:{INK};">崔伟说养老</p>')
w(f'<p style="margin:0;font-size:15px;color:{SUB};line-height:1.8;">让天下人老有所养</p>')
w(f'<p style="margin:10px 0 0;font-size:13px;color:{SUB};line-height:1.7;">'
  f'本文内容综合公开财经资讯整理，仅供参考，不构成任何投资建议。市场有风险，决策需谨慎。</p>')
w('</section>')

w('</section>')

html = "\n".join(OUT)
open(f"{BASE}/pension.html", "w", encoding="utf-8").write(html)

# ---------- 标题 / 摘要 ----------
title = strip_tags(pension.get("title", "")).strip()
if title:
    suffix = f"｜养老日报{dt.month}.{dt.day}" if isinstance(dt, datetime) else "｜养老日报"
    full = title if title.endswith(suffix) else (title + suffix)
    if len(full) > 64:            # 公众号标题上限 64 字
        full = title[:64 - len(suffix)] + suffix
else:
    full = f"养老日报 {title_date}"
open(f"{BASE}/pension_title.txt", "w", encoding="utf-8").write(full)

digest = strip_tags(pension.get("lead") or insight or "")[:110]
open(f"{BASE}/pension_digest.txt", "w", encoding="utf-8").write(digest)

# ---------- 封面(复用日报封面模板, 文案换成养老口径) ----------
def shorten(s, n):
    s = strip_tags(s)
    return s if len(s) <= n else s[:n].rstrip("，。、；,. ") + "…"

env = Environment(loader=FileSystemLoader(BASE), autoescape=select_autoescape(["html"]))
cover_ctx = dict(
    pub_date=pub_date, date_cn=title_date,
    brand="养老日报", brand_sub="崔伟说养老 · 让天下人老有所养",
    bname="崔伟说养老", bsub="RETIREMENT · WEALTH · CARE", cta="点开看完整内容 ›",
    hook_big=shorten(title, 16),
    hook_sub=shorten(pension.get("lead") or insight, 30),
    trend="", trend_plain="",
    cover_items=[dict(label=strip_tags(it.get("label", "")), tx=shorten(it.get("text"), 34)) for it in items[:2]],
)
cover_html = env.get_template("template_cover.html").render(**cover_ctx)
open(f"{BASE}/pension_cover.html", "w", encoding="utf-8").write(cover_html)

print(f"已渲染 pension.html  日期={pub_date}  要闻={len(items)}条(投资向硬闸剔除{dropped}条)  "
      f"解读={'有' if insight else '无'}  其他要闻={len(briefs)}条  字节={len(html)}")
if not FACTS_OK:
    print("⚠ 事实卡未经人工核对(pension_facts.json 的 verified_by 为空), 「大家最关心的」栏目未渲染")
elif buzz_held:
    print(f"⚠ 讨论区有 {buzz_held} 条含调整比例数字被闸门挡下(需核实后登记 confirmed_claims):")
    for it in buzz_all:
        if not buzz_ok(it):
            print(f"   ［{it.get('stance')}］据{it.get('src')}：{strip_tags(it.get('text'))[:70]}…")
print(f"养老日报标题：{full}")
