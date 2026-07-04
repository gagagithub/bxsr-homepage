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
from jinja2 import Environment, FileSystemLoader, select_autoescape

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

# 外层容器(公众号正文宽度自适应手机屏);字号整体加大、行距放宽,方便中老年阅读
w(f'<section style="font-family:-apple-system,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;'
  f'color:{INK};font-size:19px;line-height:2.0;background:{PAPER};padding:2px;">')

# ---------- 报头(纯文字, 不嵌头图) ----------
# 注:不再在正文顶部嵌封面海报头图——它和文章封面缩略图是同一张,正文里再放就重复了。
# 封面海报仍作草稿 thumb_media_id(文章封面),正文只留一行小字日期作报头。
w(f'<p style="margin:6px 4px 14px;text-align:center;font-size:15px;color:{SUB};letter-spacing:1px;">'
  f'{date_cn} · {week_cn} · 让天下人老有所养</p>')

# (今日头条卡已按崔伟要求删除, 报头之后直接进行情速览 + 三大主题)
trend = S.get("trend", "").strip()  # 仍保留供 digest 摘要用

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

rows = market_rows()  # 供文末生成 market.png 用; 图片本身移到三大主题之后再插

# ---------- 三大主题: 健康 / 养老 / 传承(每主题 = 相关新闻 + 一段解读) ----------
THEME_ORDER = ["健康", "养老", "传承"]
THEME_ICON = {"健康": "🏥", "养老": "🌅", "传承": "🌳"}
THEME_SUB = {"健康": "看病住院 · 报销保障", "养老": "养老钱 · 该往哪放", "传承": "家底保值 · 稳稳传下去"}
# 每主题一套色: 健康绿 / 养老金橙 / 传承墨绿
THEME_CLR = {"健康": ("#0f7a4a", "#f1f9f4"), "养老": ("#b9791b", "#fdf6ea"), "传承": ("#1b6b57", "#eff7f3")}
_thmap = {t.get("name"): t for t in S.get("themes", []) if t.get("name")}
for name in THEME_ORDER:
    t = _thmap.get(name)
    if not t:
        continue
    its = [it for it in t.get("items", []) if it.get("text")]
    insight = (t.get("insight") or "").strip()
    if not its and not insight:
        continue
    dark, light = THEME_CLR.get(name, (GOLD, "#fffdf7"))
    icon = THEME_ICON.get(name, "📌")
    subt = THEME_SUB.get(name, "")
    # 主题标题条(撞色底)
    w(f'<section style="margin:26px 4px 10px;padding:12px 15px;border-radius:8px;'
      f'background:{dark};display:flex;align-items:center;">')
    w(f'<span style="font-size:22px;">{icon}</span>'
      f'<span style="margin-left:8px;font-size:21px;font-weight:900;color:#fff;letter-spacing:2px;">{name}</span>')
    if subt:
        w(f'<span style="margin-left:auto;font-size:12px;color:#fff;opacity:.9;">{subt}</span>')
    w('</section>')
    # 该主题相关新闻
    for it in its:
        label = strip_tags(it.get("label", ""))
        src = strip_tags(it.get("src", ""))
        body = emph(it.get("text"))
        w(f'<p style="margin:0 0 16px;font-size:19px;line-height:2.0;color:{INK};">')
        if label:
            w(f'<span style="color:{ORANGE};font-weight:700;">【{label}】</span>')
        w(body)
        if src:
            w(f'<span style="color:{SUB};font-size:14px;">（{src}）</span>')
        w('</p>')
    # 该主题解读
    if insight:
        w(f'<section style="margin:6px 4px 4px;padding:13px 15px;background:{light};'
          f'border-left:5px solid {dark};border-radius:6px;">')
        w(f'<p style="margin:0 0 6px;font-size:15px;font-weight:800;color:{dark};">🔍 这跟咱有啥关系</p>')
        w(f'<p style="margin:0;font-size:18px;line-height:2.0;color:{INK};">'
          f'{insight.replace("<b>", f"<span style=color:{dark};font-weight:700;>").replace("</b>", "</span>")}</p>')
        w('</section>')

# ---------- 行情速览(三大主题之后、晨报纵览之前) ----------
if rows:
    # 行情速览改成一张「大字+红绿涨跌箭头」的图片(适老),正文里放占位符,
    # 服务器侧把 market.png 上传微信图床后替换为 <img>。
    w("{{MR_IMG:market}}")

# ---------- 晨报纵览 review ----------
review = S.get("review", {}) or {}
paras = review.get("paras") or []
if paras:
    w(f'<section style="margin:24px 4px;padding:16px;background:#fffdf7;border:1px dashed {GOLD};border-radius:4px;">')
    w(f'<p style="margin:0 0 12px;font-size:21px;font-weight:800;color:{GOLD};">📰 {review.get("title","晨报纵览")}</p>')
    for p in paras:
        w(f'<p style="margin:0 0 12px;font-size:19px;line-height:2.0;color:{INK};">{emph(p)}</p>')
    w('</section>')

# (今日解读已并入上面三大主题, 每个主题末尾各带一段「这跟咱有啥关系」)

# ---------- 文末入口(公众号正文不能放可点外链, 引导点左下角「阅读原文」) ----------
# 有视频的当天(阅读原文跳视频页): 文末放视频封面海报(带▶) + 引导语, 图片应用户要求放文章最后。
# 微信图文正文不能内嵌外部 mp4 播放器, 占位符 {{MR_IMG:video}} 由服务器内嵌 video-poster.jpg。
# 没视频的当天(阅读原文跳H5一图全览): 放深蓝「看完整晨报」引导块。两者只出其一, 避免引导语和实际落地页不一致。
if os.path.exists(f"{BASE}/radio/renders/morning-radio.mp4"):
    w(f'<section style="margin:24px 4px 16px;">')
    w('{{MR_IMG:video}}')
    w(f'<p style="margin:8px 4px 2px;text-align:center;font-size:17px;font-weight:800;color:{ORANGE};">'
      f'🎧 今日财经晨报 · 视频版（约9分钟）</p>')
    w(f'<p style="margin:0 4px;text-align:center;font-size:14px;color:{SUB};">'
      f'点击左下角「阅读原文」，边听边看今日全球市场速览</p>')
    w('</section>')
else:
    w(f'<section style="margin:24px 4px 6px;padding:18px 16px;background:#11305f;border-radius:8px;text-align:center;">')
    w(f'<p style="margin:0;font-size:21px;font-weight:800;color:#fff;letter-spacing:1px;">▶ 看今日完整晨报</p>')
    w(f'<p style="margin:8px 0 0;font-size:16px;color:#cfe0f7;line-height:1.7;">'
      f'点击文末左下角「阅读原文」，查看全球市场 · 内地财经一图全览</p>')
    w('</section>')

# ---------- 页脚 ----------
w(f'<section style="margin:18px 4px 8px;padding-top:14px;border-top:3px double {GOLD};text-align:center;">')
w(f'<p style="margin:0 0 6px;font-size:17px;font-weight:700;color:{INK};">保 · 心上人</p>')
w(f'<p style="margin:0;font-size:15px;color:{SUB};line-height:1.8;">健康 · 养老 · 传承&nbsp;&nbsp;|&nbsp;&nbsp;让天下人老有所养</p>')
w(f'<p style="margin:10px 0 0;font-size:13px;color:{SUB};line-height:1.7;">'
  f'本晨报内容综合公开财经资讯整理，仅供参考，不构成任何投资建议。市场有风险，决策需谨慎。</p>')
w('</section>')

w('</section>')

html = "".join(OUT)
open(f"{BASE}/wechat.html", "w", encoding="utf-8").write(html)

# ---------- 行情速览图(template_market.html → market.html, 由 workflow 用 Chrome 截图为 market.png) ----------
def _cls(raw):
    if raw is None or raw == 0:
        return "flat"
    return "up" if raw > 0 else "down"

def _arrow(raw):
    if raw is None or raw == 0:
        return ""
    return "▲ " if raw > 0 else "▼ "

if rows:
    mrows = [dict(name=n, val=v, note=(note or "持平"), cls=_cls(raw), ar=_arrow(raw))
             for (n, v, note, raw) in rows]
    env = Environment(loader=FileSystemLoader(BASE), autoescape=select_autoescape(["html"]))
    mhtml = env.get_template("template_market.html").render(
        data_date=(D.get("data_date") or pub_date), rows=mrows)
    open(f"{BASE}/market.html", "w", encoding="utf-8").write(mhtml)
    # Chrome 截图窗口高度: 头部~150 + 每行~100 + 页脚~96(随行数变化, workflow 读此值)
    mh = 150 + len(mrows) * 100 + 96
    open(f"{BASE}/market_h.txt", "w", encoding="utf-8").write(str(mh))
    print(f"已渲染 market.html  行数={len(mrows)}  截图高度={mh}")

# digest 摘要(公众号文章列表/分享摘要, ≤120 字, 纯文本)
digest = strip_tags(S.get("moment_text") or trend).replace("\n", " ").strip()
digest = re.sub(r"\s+", " ", digest)[:118]
open(f"{BASE}/wechat_digest.txt", "w", encoding="utf-8").write(digest)

print(f"已渲染 wechat.html  日期={pub_date}  字节={len(html)}  主题={len(_thmap)}  digest={len(digest)}字")
