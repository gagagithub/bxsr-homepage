# -*- coding: utf-8 -*-
"""财经晨报·微信公众号图文渲染：sections.json(+data.json 行情) → wechat.html。

输出的是公众号 draft/add 的「正文 content 片段」(不是完整 H5)：
- 全部内联 style(公众号会过滤 <style>/class/<script>/iframe/外链CSS)
- <b> 关键数据 → 红色加粗 span(公众号没有我们的 CSS)
- 不放可点外链(公众号正文非白名单域名 <a> 不可跳转), 来源以纯文字标注
- 封面图走草稿的 thumb_media_id, 不嵌正文
pub_date 走环境变量 MX_PUB_DATE(Asia/Shanghai), 缺省取本机当天。
"""
import os, json, re
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
S = json.load(open(f"{BASE}/sections.json", encoding="utf-8"))
try:
    D = json.load(open(f"{BASE}/data.json", encoding="utf-8"))
except Exception:
    D = {}

# 象牙金报刊风配色
RED    = "#b23b2e"   # 关键数据红
ORANGE = "#a8741c"   # 机构 label 橙金
INK    = "#222019"   # 正文墨色
SUB    = "#8a8275"   # 次要灰
LINE   = "#e2dccb"   # 分隔线
PAPER  = "#faf7ef"   # 纸色底
GOLD   = "#9c7b2e"   # 标题金

def emph(t):
    """<b>x</b> → 红色加粗 span;其余原样(text 来自 AI 整理, 可信)。"""
    if not t:
        return ""
    return t.replace("<b>", f'<span style="color:{RED};font-weight:700;">').replace("</b>", "</span>")

def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")

def fmt_pct(v):
    if v is None:
        return "—"
    return f"{v:+.2f}%"

def pct_color(v):
    if v is None or v == 0:
        return SUB
    return RED if v > 0 else "#2e7d4f"

OUT = []
def w(s):
    OUT.append(s)

pub_date = os.environ.get("MX_PUB_DATE") or datetime.now().strftime("%Y-%m-%d")
try:
    dt = datetime.strptime(pub_date, "%Y-%m-%d")
    date_cn = f"{dt.year}年{dt.month}月{dt.day}日"
    week_cn = "周" + "一二三四五六日"[dt.weekday()]
except Exception:
    date_cn, week_cn = pub_date, ""

# 外层容器(公众号正文宽度自适应手机屏)
w(f'<section style="font-family:-apple-system,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;'
  f'color:{INK};font-size:16px;line-height:1.75;background:{PAPER};padding:2px;">')

# ---------- 报头 ----------
w(f'<section style="text-align:center;padding:18px 8px 14px;border-bottom:3px double {GOLD};margin-bottom:6px;">')
w(f'<p style="margin:0;font-size:13px;letter-spacing:3px;color:{GOLD};">保 · 心 上 人 · 投 资</p>')
w(f'<p style="margin:8px 0 4px;font-size:30px;font-weight:800;letter-spacing:6px;color:{INK};">财 经 晨 报</p>')
w(f'<p style="margin:0;font-size:13px;color:{SUB};letter-spacing:1px;">{date_cn} · {week_cn} · 让天下人老有所养</p>')
w('</section>')

# ---------- 导读 trend ----------
trend = S.get("trend", "").strip()
if trend:
    w(f'<section style="margin:10px 4px 16px;padding:12px 14px;background:#fffdf7;'
      f'border-left:4px solid {GOLD};border-radius:2px;">')
    w(f'<p style="margin:0;font-size:15px;line-height:1.8;color:{INK};">'
      f'<span style="color:{GOLD};font-weight:700;">【今日导读】</span>{emph(trend)}</p>')
    w('</section>')

# ---------- 行情速览(可选) ----------
def market_rows():
    rows = []
    IDX = {"DJI": "道琼斯", "GSPC": "标普500", "IXIC": "纳斯达克"}
    for k in ["DJI", "GSPC", "IXIC"]:
        v = (D.get("indices") or {}).get(k, {})
        if v.get("cur") is not None:
            rows.append((IDX[k], f"{v['cur']:,.0f}", fmt_pct(v.get("day_pct")), v.get("day_pct")))
    cb = D.get("cn_bond", {})
    if cb.get("cn10") is not None:
        rows.append(("中国10年国债", f"{cb['cn10']:.3f}%", f"{cb.get('cn10_bp',0):+.1f}bp", cb.get("cn10_bp")))
    y = (D.get("yields") or {}).get("Y10", {})
    if y.get("level") is not None:
        rows.append(("美债10年", f"{y['level']:.3f}%", f"{y.get('bp',0):+.1f}bp", y.get("bp")))
    co = D.get("commodities", {})
    if (co.get("GOLD") or {}).get("cur") is not None:
        rows.append(("COMEX黄金", f"${co['GOLD']['cur']:,.0f}", fmt_pct(co['GOLD'].get("day_pct")), co['GOLD'].get("day_pct")))
    if (co.get("WTI") or {}).get("cur") is not None:
        rows.append(("WTI原油", f"${co['WTI']['cur']:.2f}", fmt_pct(co['WTI'].get("day_pct")), co['WTI'].get("day_pct")))
    fx = D.get("forex", {})
    if (fx.get("CNH") or {}).get("cur") is not None:
        rows.append(("离岸人民币", f"{fx['CNH']['cur']:.4f}", "", None))
    if (fx.get("DXY") or {}).get("cur") is not None:
        rows.append(("美元指数", f"{fx['DXY']['cur']:.2f}", fmt_pct(fx['DXY'].get("day_pct")), fx['DXY'].get("day_pct")))
    return rows

rows = market_rows()
if rows:
    w(f'<section style="margin:6px 4px 18px;">')
    w(f'<p style="margin:0 0 8px;font-size:17px;font-weight:800;color:{INK};">📈 行情速览</p>')
    w(f'<table style="width:100%;border-collapse:collapse;font-size:14px;">')
    for i in range(0, len(rows), 2):
        w('<tr>')
        for j in range(2):
            if i + j < len(rows):
                name, val, note, raw = rows[i + j]
                c = pct_color(raw)
                w(f'<td style="width:50%;padding:7px 8px;border-bottom:1px solid {LINE};">'
                  f'<span style="color:{SUB};">{name}</span><br>'
                  f'<span style="font-weight:700;color:{INK};font-size:15px;">{val}</span> '
                  f'<span style="color:{c};font-size:12px;">{note}</span></td>')
            else:
                w(f'<td style="width:50%;border-bottom:1px solid {LINE};"></td>')
        w('</tr>')
    w('</table>')
    w('</section>')

# ---------- 头条 highlights ----------
highlights = [h for h in S.get("highlights", []) if h.get("text") or h.get("title")]
if highlights:
    w(f'<section style="margin:18px 4px;">')
    w(f'<p style="margin:0 0 10px;font-size:19px;font-weight:800;color:{INK};'
      f'border-left:6px solid {RED};padding-left:10px;">今日头条</p>')
    for h in highlights:
        label = strip_tags(h.get("label", ""))
        body = emph(h.get("text") or h.get("title"))
        w(f'<section style="margin:0 0 14px;padding:12px 14px;background:#fff;border:1px solid {LINE};border-radius:4px;">')
        if label:
            w(f'<p style="margin:0 0 6px;font-size:15px;font-weight:800;color:{ORANGE};">▎{label}</p>')
        w(f'<p style="margin:0;font-size:15px;line-height:1.85;color:{INK};">{body}</p>')
        w('</section>')
    w('</section>')

# ---------- 8 板块 ----------
SEC_ICON = {"宏观经济": "🏛️", "地产动态": "🏙️", "股市盘点": "📊", "财富聚焦": "💰",
            "行业观察": "🔬", "公司要闻": "🏢", "环球视野": "🌍", "金融数据": "📈"}
no = 0
for sec in S.get("sections", []):
    its = [it for it in sec.get("items", []) if it.get("text")]
    if not its:
        continue
    no += 1
    name = sec.get("name", "")
    icon = SEC_ICON.get(name, "📌")
    w(f'<section style="margin:22px 4px 6px;">')
    w(f'<p style="margin:0 0 10px;font-size:18px;font-weight:800;color:{INK};">'
      f'<span style="color:{GOLD};">{no:02d}</span>&nbsp;&nbsp;{icon} {name}</p>')
    for it in its:
        label = strip_tags(it.get("label", ""))
        src = strip_tags(it.get("src", ""))
        body = emph(it.get("text"))
        w(f'<p style="margin:0 0 12px;font-size:15px;line-height:1.82;color:{INK};">')
        if label:
            w(f'<span style="color:{ORANGE};font-weight:700;">【{label}】</span>')
        w(body)
        if src:
            w(f'<span style="color:{SUB};font-size:12px;">（{src}）</span>')
        w('</p>')
    w('</section>')

# ---------- 晨报纵览 review ----------
review = S.get("review", {}) or {}
paras = review.get("paras") or []
if paras:
    w(f'<section style="margin:24px 4px;padding:14px;background:#fffdf7;border:1px dashed {GOLD};border-radius:4px;">')
    w(f'<p style="margin:0 0 10px;font-size:18px;font-weight:800;color:{GOLD};">📰 {review.get("title","晨报纵览")}</p>')
    for p in paras:
        w(f'<p style="margin:0 0 10px;font-size:15px;line-height:1.85;color:{INK};">{emph(p)}</p>')
    w('</section>')

# ---------- 保险配置视角 ----------
insure = [x for x in S.get("insure", []) if x.get("tx")]
if insure:
    w(f'<section style="margin:22px 4px;padding:14px;background:#f3f6f4;border-left:4px solid #2e7d4f;border-radius:4px;">')
    w(f'<p style="margin:0 0 10px;font-size:18px;font-weight:800;color:#2e7d4f;">🛡️ 保险配置视角</p>')
    for x in insure:
        ic = x.get("ic", "•")
        w(f'<p style="margin:0 0 10px;font-size:15px;line-height:1.82;color:{INK};">{ic} {emph(x.get("tx"))}</p>')
    w('</section>')

# ---------- 页脚 ----------
w(f'<section style="margin:24px 4px 8px;padding-top:14px;border-top:3px double {GOLD};text-align:center;">')
w(f'<p style="margin:0 0 6px;font-size:15px;font-weight:700;color:{INK};">保 · 心上人</p>')
w(f'<p style="margin:0;font-size:13px;color:{SUB};line-height:1.7;">健康 · 养老 · 传承&nbsp;&nbsp;|&nbsp;&nbsp;让天下人老有所养</p>')
w(f'<p style="margin:10px 0 0;font-size:11px;color:{SUB};line-height:1.6;">'
  f'本晨报内容综合公开财经资讯整理，仅供参考，不构成任何投资建议。市场有风险，决策需谨慎。</p>')
w('</section>')

w('</section>')

html = "".join(OUT)
open(f"{BASE}/wechat.html", "w", encoding="utf-8").write(html)

# digest 摘要(公众号文章列表/分享摘要, ≤120 字, 纯文本)
digest = strip_tags(S.get("moment_text") or trend).replace("\n", " ").strip()
digest = re.sub(r"\s+", " ", digest)[:118]
open(f"{BASE}/wechat_digest.txt", "w", encoding="utf-8").write(digest)

print(f"已渲染 wechat.html  日期={pub_date}  字节={len(html)}  板块={no}  头条={len(highlights)}  digest={len(digest)}字")
