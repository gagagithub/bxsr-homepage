# -*- coding: utf-8 -*-
"""财经晨报·AI 整理：把 news_raw.json 的真实新闻电报喂 DeepSeek，做去重/分类/挑重点/归板块/一句话保险视角。
AI 只对【已给出的真实新闻】做筛选、归类、精简改写，绝不新增任何未给出的数字/政策/事实。带合规护栏。
输出 sections.json，供 render_morning.py 使用。"""
import os
BASE = os.path.dirname(os.path.abspath(__file__))
import json, re, sys, urllib.request

YML = os.environ.get("BAOXIN_DEV_YML", os.path.join(BASE, "..", "..", "service", "src", "main", "resources", "application-dev.yml"))
def get_key():
    try:
        txt = open(YML, encoding="utf-8", errors="ignore").read()
        m = re.search(r"apiKey:\s*(sk-[A-Za-z0-9_\-]+)", txt)
        return m.group(1) if m else None
    except Exception:
        return None

RAW = json.load(open(f"{BASE}/news_raw.json"))
items = RAW["items"]

# 预过滤：丢掉过短的纯盘口异动快讯(降噪+省token)，保留有信息量的条目
import re as _re
def is_noise(t):
    t = t.strip()
    if len(t) < 28:
        return True
    # 纯个股/板块盘口异动(无实质新闻), 且较短
    if len(t) < 70 and _re.search(r"(涨停|跌停|涨超|跌超|高开|低开|封板|异动|拉升|跳水|领涨|领跌)", t) \
            and not t.startswith("【"):
        return True
    return False
kept = [it for it in items if not is_noise(it["text"])]

# 给每条编号(供 AI 引用原文链接, 避免它瞎编 link)；喂较完整正文以保留全部数字
indexed = []
idmap = {}  # 新id -> 原items下标
for i, it in enumerate(kept):
    idmap[i] = items.index(it)
    indexed.append({"id": i, "t": it["text"][:460]})
print(f"预过滤后喂给 AI {len(indexed)} 条 (原始 {len(items)} 条)")

SECTIONS = ["宏观经济", "地产动态", "股市盘点", "财富聚焦", "行业观察", "公司要闻", "环球视野", "金融数据"]

SYS = """你是「保心上人」的财经晨报主笔，为保险规划师及其客户编每日《财经晨报》。
读者多是关注养老、储蓄、资产传承的中老年朋友，不是金融从业者。

【文风铁律：说人话，别播新闻联播】
- 像一位懂行的老朋友早上跟你唠新闻，通篇口语化短句，一句话尽量不超过30字。
- 禁用新闻通稿腔："据悉/日前/获悉/表示/指出/称/此举旨在/持续推进"这类词一律改成"说/提到/打算/一直在做"或直接陈述。
- 专业术语要么不用，要么顺手用大白话解释一句(如"LPR，就是房贷利率的锚")。
- 每条尽量先用一句口语点破"这事说明什么/跟咱有什么关系"，再摆事实；可以少量用"说白了""注意""这个不多见"这类口头语，但别油腻、别夸张、别标题党。
- 数字是干货，一个都不能丢，但要放进顺口的句子里，读出声不别扭才算合格。

【格式颗粒，必须严格照做】
A. 每一条都写成「机构信源/主体 ＋ 完整一段」：
   - label = 这条新闻的来源机构或主体(如 财政部 / 统计局 / 央行 / 发改委 / 央视新闻 / 国家能源集团 / 美光科技 / 英伟达 / 日本央行 …)，从原文提取，2-8字。
   - text = 用大白话把这条新闻讲清楚，**原文里每一个关键数字、比例、金额、时间、机构表态全部保留**，写成2-4句、80-160字，读起来像说话、不像通稿，绝不压成干巴一句。
B. text 里所有关键数字/金额/比例/"创新高"等要点用 <b>…</b> 包起来(前端会标红)。

【铁律，违反作废】
1. 你只能对【用户给出的真实新闻条目】做：去重、筛选、归类、整理改写、合并同主题。
2. 绝对禁止新增任何原文里没有的数字、政策、机构表态、公司动作。数字必须原样保留，一个都不能改、不能编。
3. 与财经/财富/宏观无关、纯个股盘口异动("X板块高开""Y涨停")的条目，直接丢弃。
4. 禁止任何收益承诺/预测保证；禁止"稳赚/保本高收益/存款搬家/躺赚"等话术。
5. 保险相关表述用"可关注/配置参考"等中性措辞，不构成销售要约；不出现具体保险产品名。
6. 每条只引用一个原文 id（取你改写所依据的那条），用于挂原文链接。"""

NEWS_JSON = json.dumps(indexed, ensure_ascii=False)

def build_user(secs, want_meta, want_tail):
    """secs=本次要产出的板块; want_meta=是否产 trend+highlights; want_tail=是否产 insure+review。"""
    head = (f"""今天的真实财经新闻电报如下（JSON 数组，id 为编号，t 为内容，已含较完整细节，请把同主题多条合并成一条更完整的）：
{NEWS_JSON}

请整理成一份**对标财秘晨报、颗粒度一致**的《财经晨报》的【部分内容】。严格输出如下 JSON（不要多余文字、不要 markdown 代码块）：
{{""")
    parts = []
    if want_meta:
        parts.append('  "trend": "40字内今日风向，口语化，像跟同事说\'今天就盯这一两件事\'，关键词<b>加粗</b>"')
        parts.append('''  "moment_text": "一段适合规划师发【客户朋友圈】的文案，4-6行、每行短句、可用1-2个emoji，开头点出今日财经看点(用真实数字)，结尾引导'点开看完整晨报'。务必合规：不承诺收益、不出现'稳赚/保本/存款搬家'、不荐具体产品，纯财经资讯分享口吻，专业可信"''')
        parts.append('''  "highlights": [
    {"label":"机构/主体", "text":"今日最重磅头条之一，用大白话讲成完整一段(2-3句、80-150字)，先一句点破为什么重要，保留全部关键数字，<b>标红</b>核心数据", "id": 原文id}
  ]''')
    if secs:
        parts.append('''  "sections": [
    {
      "name": "板块名(只能从这些里选: ''' + "、".join(secs) + '''),
      "items": [
        {"label":"机构/主体(2-8字)", "text":"用大白话讲成完整一段(2-4句、80-160字)，**保留原文每一个数字与细节**，读起来像说话不像通稿，关键数据用<b>标红</b>", "id": 原文id}
      ]
    }
  ]''')
    if want_tail:
        parts.append('''  "insure": [
    {"ic":"💵","tx":"用大白话把今日宏观/利率/汇率新闻翻译成对美元储蓄险/分红险客户的配置参考，80字内，<b>关键短语加粗</b>"},
    {"ic":"📈","tx":"用大白话把今日股市/经济新闻翻译成对资产配置/养老储备的视角，80字内"},
    {"ic":"🛡️","tx":"避险/稳健配置视角的一句大白话，80字内"}
  ]''')
        parts.append('''  "review": {
    "title":"晨报纵览小标题(当天最大主线，20字内)",
    "paras":["3段、每段80-140字，像跟客户面对面聊天一样把当天最重要的几条新闻串成一条线讲明白，只用给定事实，落到对养老与财富配置的启示，中性不荐品"]
  }''')
    schema = ",\n".join(parts)
    reqs = ["要求（务必做满）：",
            "- 每条都必须是「label(机构/主体) + 完整一段(含全部数字)」，**不允许只有一句话、丢失数字的干瘪条目**。",
            "- 通篇口语化：短句、说人话、零新闻腔；但数字和细节一个不丢。"]
    if want_meta and not secs:
        reqs.append("- 本次只产出 trend 和 highlights；highlights 必须给 **5-6 条**当天最重磅的，每条写成完整一段。")
    if secs:
        reqs.append(f"- 本次只产出这些板块：{'、'.join(secs)}；每个板块挑 **6-12 条**，每条都写满。当天确无素材的板块才省略。")
    if want_tail:
        reqs.append("- insure 给 3 条；review 必须写满 3 段。")
    reqs.append("- 全文只用给定新闻，不得自行补充任何外部信息。")
    tail = "\n}\n\n" + "\n".join(reqs)
    return head + "\n" + schema + tail

key = os.environ.get("DEEPSEEK_API_KEY") or get_key()
if not key:
    print("!! 未找到 DeepSeek key", file=sys.stderr); sys.exit(1)

def _call_once(user):
    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
        "temperature": 0.3, "max_tokens": 8000,
        "response_format": {"type": "json_object"},  # DeepSeek 强制返回合法 JSON, 杜绝偶发语法错
    }).encode("utf-8")
    req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=200) as r:
        resp = json.loads(r.read().decode())
    content = resp["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
    try:
        return json.loads(content)
    except Exception:
        # 兜底：万一截断, 挽救已完整的部分(截到最后一个完整 "}]" 并补全括号)
        cut = content.rfind('"}')
        if cut > 0:
            frag = content[:cut + 2]
            for closer in ('}]}', ']}', '}', ']}]}'):
                try:
                    return json.loads(frag + closer)
                except Exception:
                    continue
        raise

def call(user, tries=3):
    """DeepSeek 偶发返回不合法 JSON, 整次重试(json_object 模式下基本不会发生, 仍兜底)。"""
    last = None
    for k in range(tries):
        try:
            return _call_once(user)
        except Exception as e:
            last = e
            print(f"!! DeepSeek 第{k+1}次失败({type(e).__name__}: {str(e)[:80]})，重试", file=sys.stderr)
            import time as _t; _t.sleep(2)
    raise last

# 三次调用：①风向+头条 ②前半板块 ③后半板块+保险+纵览(各自都在 8K 输出上限内，保证 JSON 完整)
G1 = ["宏观经济", "地产动态", "股市盘点", "财富聚焦"]
G2 = ["行业观察", "公司要闻", "环球视野", "金融数据"]
d0 = call(build_user([], want_meta=True, want_tail=False))   # trend + highlights
d1 = call(build_user(G1, want_meta=False, want_tail=False))  # 前半板块
d2 = call(build_user(G2, want_meta=False, want_tail=True))   # 后半板块 + insure + review

# 合并（板块按 SECTIONS 顺序）
secmap = {}
for s in (d1.get("sections", []) + d2.get("sections", [])):
    secmap[s["name"]] = s
data = {
    "trend": d0.get("trend", ""),
    "moment_text": d0.get("moment_text", ""),
    "highlights": d0.get("highlights", []),
    "sections": [secmap[n] for n in SECTIONS if n in secmap],
    "insure": d2.get("insure", []),
    "review": d2.get("review", {}),
}

# 把 AI 引用的 id 还原成真实 link/time（绝不信任 AI 自己写的链接）；id 经 idmap 映射回原 items
def attach(obj):
    i = obj.get("id")
    oi = idmap.get(i) if isinstance(i, int) else None
    if oi is not None and 0 <= oi < len(items):
        obj["link"] = items[oi].get("link", "")
        obj["src"] = items[oi].get("src", "")
    else:
        obj["link"] = ""; obj["src"] = ""
    obj.pop("id", None)
    return obj

for h in data.get("highlights", []):
    attach(h)
for sec in data.get("sections", []):
    for it in sec.get("items", []):
        attach(it)

cnt = sum(len(s.get("items", [])) for s in data.get("sections", []))
rv = data.get("review", {})
json.dump(data, open(f"{BASE}/sections.json", "w"), ensure_ascii=False, indent=2)
print(f"AI 整理完成 sections.json：头条 {len(data.get('highlights', []))} 条 / "
      f"{len(data.get('sections', []))} 板块 / 正文 {cnt} 条 / 纵览 {len(rv.get('paras', []))} 段")
