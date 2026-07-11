# -*- coding: utf-8 -*-
"""sections.json → content/script.json（电台版全板块精炼口播稿）。
用法: python3 build_script.py <sections.json> <out_dir> [pub_date=YYYY-MM-DD]
输出: <out_dir>/content/script.json = [[para, "句"], ...]  每句一条字幕。
口径: 今日风向 + 6条头条(精炼) + 全8板块(板块名+每条 标题·首句精炼) + 保险视角 + 收尾。
目标 ~8-10 分钟; 每条只取首句并截断, 覆盖全板块又不冗长。"""
import json, re, sys, os
from datetime import datetime

SEC = sys.argv[1] if len(sys.argv) > 1 else "sections.json"
OUT = sys.argv[2] if len(sys.argv) > 2 else "."
PUB = sys.argv[3] if len(sys.argv) > 3 else datetime.now().strftime("%Y-%m-%d")

WK = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
dt = datetime.strptime(PUB, "%Y-%m-%d")
DATE_CN = f"{dt.month}月{dt.day}日"
WEEK_CN = WK[dt.weekday()]

def clean(t):
    return re.sub(r"<[^>]+>", "", str(t)).replace("​", "").strip()

def first_sentence(t, cap=46):
    """取第一句(。；！? 或第一个长逗号子句), 截到 cap 字以内, 末尾补句意。"""
    t = clean(t)
    # 先按强分句符切
    m = re.split(r"[。；！？]", t)
    s = m[0].strip() if m and m[0].strip() else t
    if len(s) > cap:
        # 太长再按逗号截一个完整子句
        parts = re.split(r"[，、]", s)
        acc = ""
        for p in parts:
            if len(acc) + len(p) > cap and acc:
                break
            acc += p
        s = acc or s[:cap]
    return s.strip("，、 ")

d = json.load(open(SEC, encoding="utf-8"))
S = []
S.append([0, "保心上人财经晨报"])
S.append([0, f"{DATE_CN} {WEEK_CN}"])

# 今日风向
trend = clean(d.get("trend", ""))
if trend:
    S.append([1, "先看今日风向"])
    for seg in [x for x in re.split(r"[；。]", trend) if x.strip()][:2]:
        S.append([1, seg.strip()])

# 头条精选
hl = d.get("highlights", [])
if hl:
    S.append([2, "今日头条精选"])
    for i, h in enumerate(hl, 1):
        lab = clean(h.get("label", ""))
        S.append([2, f"{lab}。{first_sentence(h.get('text',''), 40)}"])

# 三大主题: 健康 / 养老 / 传承(每主题 = 相关新闻 + 一段解读; 健康主题末尾带每日健康小课堂)
THEME_INTRO = {"健康": "先说健康", "养老": "再说养老", "传承": "最后说传承"}
tip = d.get("tip", {}) or {}
if isinstance(tip, str):
    tip = {"body": tip}
themes_in = d.get("themes", [])
if tip.get("body") and not any(clean(t.get("name", "")) == "健康" for t in themes_in):
    themes_in = [{"name": "健康", "items": [], "insight": ""}] + themes_in  # 健康档全空时也要播小课堂
para = 3
for th in themes_in:
    name = clean(th.get("name", ""))
    S.append([para, THEME_INTRO.get(name, f"下面说说{name}")])
    for it in th.get("items", []):
        lab = clean(it.get("label", ""))
        body = first_sentence(it.get("text", ""), 36)
        line = (f"{lab}，{body}" if lab else body).strip("，、 ")
        if line:
            S.append([para, line])
    insight = clean(th.get("insight", ""))
    if insight:
        S.append([para, "这跟咱的关系是"])
        # 解读较长, 按句拆成 2-3 条字幕, 别一句太长
        for seg in [x for x in re.split(r"[。；]", insight) if x.strip()][:3]:
            S.append([para, seg.strip()])
    if name == "健康" and tip.get("body"):
        S.append([para, "接下来是健康小课堂"])
        if tip.get("title"):
            S.append([para, clean(tip["title"])])
        for seg in [x for x in re.split(r"[。；]", clean(tip["body"])) if x.strip()][:6]:
            S.append([para, seg.strip()])
    para += 1

# 收尾
S.append([para, "以上就是今天的财经晨报"])
S.append([para, "完整内容请看图文版"])
S.append([para, "保心上人 让天下人老有所养"])

os.makedirs(os.path.join(OUT, "content"), exist_ok=True)
json.dump(S, open(os.path.join(OUT, "content", "script.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
# 给上层用的元信息
meta = {"date_cn": DATE_CN, "week_cn": WEEK_CN, "pub_date": PUB, "lines": len(S),
        "chars": sum(len(t) for _, t in S)}
json.dump(meta, open(os.path.join(OUT, "content", "meta.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"{len(S)} 句  {meta['chars']} 字  ≈ {meta['chars']/4.6/60:.1f} 分钟  ({DATE_CN} {WEEK_CN})")
