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

# 正文只按读者最关心的三大主题组织(每主题=当天相关新闻 + 一段解读), 不再按财经板块分类
THEMES = ["健康", "养老", "传承"]
THEME_ICON = {"健康": "🏥", "养老": "🌅", "传承": "🌳"}
THEME_GUIDE = """【三大主题怎么归类当天新闻(把每条真实新闻挑到最贴的一档, 与本主题无关的新闻直接丢掉不要硬塞)】
① 健康：医疗、医保、药品(尤其进口药/创新药)、大病/重疾、健康产业、医疗器械、卫健政策 —— 关联"看病住院能报多少、带病投保、重疾替代"。这一档新闻最少, 当天确实没有就把 items 留空、insight 里点一句"今天没有健康相关的大新闻, 但…(给一句跟他们健康保障有关的中性提醒)"。
② 养老：利率/降息/LPR、存款、国债、养老金/社保、人口老龄化、A股/基金/股市(养老钱怎么配)、宏观经济景气 —— 关联"养老钱往哪放、锁利率窗口、每月领1万得备多少本金"。这是最宽的一档, 多数宏观和市场新闻都从'影响养老钱'的角度归这里。
③ 传承：汇率、黄金、房产/楼市、高端资产价格、财富、税费、企业股权、境外资产 —— 关联"家底保值、离婚隔离、过户vs遗嘱vs保单受益人、想给又不想现在给"。"""

SYS = """你是「保心上人」的财经晨报主笔，为保险规划师及其客户编每日《财经晨报》。

【读者画像 + 兴趣罗盘，所有解读都对着这群人、往这些角度上靠】
40-60岁，住一二线城市(京沪、长三角、珠三角为主)，家底殷实但不是金融从业者。股票、基金几乎人人在买，这是跟他们对话的共同语言。
他们真正爱看的是这些具体角度——
① 健康：结节/三高怎么核保、带病怎么投保；重疾太贵了，50岁后医疗+防癌怎么替代搭配；理赔到底能报多少、进口药自费差价(真数字最戳人)。
   少写：险种百科、条款讲解、拿发病率吓人。
② 养老：数字倒推(每月想领1万、得先备多少本金)；利率一路下行、还能锁长期利率的窗口；敢做对比(港险 vs 内地产品直接比，反正他也会去问AI)。
   少写：干巴巴的宏观焦虑、政策搬运、单纯喊"去存款/买国债"、CRS。
③ 传承：钱给了孩子又怕离婚被分走(保单怎么隔离)；过户 vs 遗嘱 vs 保单受益人三种传法的成本对比(讲场景不背法条)；想给钱又不想现在就给(年金分期给付、二婚/独生子女怎么继承)。
   少写：遗产税炒作(国内没这税，一戳就穿)。
写每条解读，先回答"这条新闻跟我这个岁数、我这个家底有什么关系"，再尽量往上面这些他们爱看的角度上靠。中性、不荐具体产品、不承诺收益。

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

def build_user(themes=None, want_meta=False, want_review=False):
    """themes=本次要产出的主题(健康/养老/传承子集); want_meta=产 hook+trend+moment+highlights; want_review=产 review。"""
    head = (f"""今天的真实财经新闻电报如下（JSON 数组，id 为编号，t 为内容，已含较完整细节，请把同主题多条合并成一条更完整的）：
{NEWS_JSON}

请整理成《财经晨报》的【部分内容】。严格输出如下 JSON（不要多余文字、不要 markdown 代码块）：
{{""")
    parts = []
    if want_meta:
        parts.append('''  "hook": {"big":"≤14字大字钩子(必须是对象不是字符串)。抓40-60岁读者的眼，从今天真实新闻里提炼，且必须落到他们的钱/养老/健康/传承上(哪怕今天头条是科技股，也从'该不该追、跟我的养老钱有啥关系'切入)，可用设问或点破利害(如'利率又降了，养老钱往哪放?')，别标题党、别承诺收益", "sub":"≤28字副标题，承接大字，说清今天到底发生了啥、跟他们的钱有啥关系"}''')
        parts.append('  "trend": "40字内今日风向，口语化，像跟同事说\'今天就盯这一两件事\'，关键词<b>加粗</b>"')
        parts.append('''  "moment_text": "一段适合规划师发【客户朋友圈】的文案，4-6行、每行短句、可用1-2个emoji，开头点出今日财经看点(用真实数字)，结尾引导'点开看完整晨报'。务必合规：不承诺收益、不出现'稳赚/保本/存款搬家'、不荐具体产品，纯财经资讯分享口吻，专业可信"''')
        parts.append('''  "highlights": [
    {"label":"机构/主体", "text":"今日最重磅头条之一，用大白话讲成完整一段(2-3句、80-150字)，先一句点破为什么重要，保留全部关键数字，<b>标红</b>核心数据", "id": 原文id}
  ]''')
    if themes:
        parts.append('''  "themes": [
    {
      "name": "主题名(只能从这些里选: ''' + "、".join(themes) + '''),
      "items": [
        {"label":"机构/主体(2-8字)", "text":"用大白话把这条【与本主题相关】的新闻讲成完整一段(2-4句、80-160字)，**保留原文每一个数字与细节**，读起来像说话不像通稿，关键数据用<b>标红</b>", "id": 原文id}
      ],
      "insight": "承接上面这些新闻，用大白话讲清今天这些事对【本主题·这群40-60岁读者】到底意味着什么，往兴趣罗盘里他们爱看的角度上靠(养老=数字倒推/锁利率窗口；传承=离婚隔离/三种传法/想给又不想现在给；健康=进口药报多少/带病投保/重疾替代)，120-200字，关键处<b>标红</b>，中性、不荐具体产品、不承诺收益"
    }
  ]''')
    if want_review:
        parts.append('''  "review": {
    "title":"晨报纵览小标题(当天最大主线，20字内)",
    "paras":["3段、每段80-140字，像跟客户面对面聊天一样把当天最重要的几条新闻串成一条线讲明白，只用给定事实，最后一段务必落到'对40-60岁、一二线城市、操心健康养老传承的读者'该怎么看待今天这些消息，中性不荐品"]
  }''')
    schema = ",\n".join(parts)
    reqs = ["要求（务必做满）：",
            "- 每条新闻都必须是「label(机构/主体) + 完整一段(含全部数字)」，**不允许只有一句话、丢失数字的干瘪条目**。",
            "- 通篇口语化：短句、说人话、零新闻腔；但数字和细节一个不丢。"]
    if want_meta:
        reqs.append("- highlights 给 **5-6 条**当天最重磅的，每条写成完整一段(这块会作为开头「今日看点」下的头条精选)。")
    if themes:
        reqs.append(THEME_GUIDE)
        reqs.append(f"- 本次只产出这些主题：{'、'.join(themes)}。每个主题：items 挑 **3-8 条**当天最相关的真实新闻(健康档新闻少、可少可空)，每条写满；insight 一段必给。与三大主题都无关的新闻(如纯个股盘口)直接丢弃，不要硬塞。")
    if want_review:
        reqs.append("- review 必须写满 3 段。")
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

# 三次调用：①看点(hook/风向/头条/朋友圈) ②养老+健康主题 ③传承主题+纵览(各自都在 8K 输出上限内，保证 JSON 完整)
d0 = call(build_user(want_meta=True))                              # hook + trend + moment + highlights
d1 = call(build_user(themes=["养老", "健康"]))                     # 养老(最宽) + 健康
d2 = call(build_user(themes=["传承"], want_review=True))          # 传承 + 纵览

# 合并（主题按 THEMES 顺序：健康/养老/传承）
themap = {}
for t in (d1.get("themes", []) + d2.get("themes", [])):
    if t.get("name"):
        themap[t["name"]] = t
_hook = d0.get("hook", {}) or {}
if isinstance(_hook, str):            # DeepSeek 偶尔把 hook 直接返回成一句话, 归一成 {big}
    _hook = {"big": _hook}
data = {
    "hook": _hook,
    "trend": d0.get("trend", ""),
    "moment_text": d0.get("moment_text", ""),
    "highlights": d0.get("highlights", []),
    "themes": [themap[n] for n in THEMES if n in themap],
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
for th in data.get("themes", []):
    for it in th.get("items", []):
        attach(it)

cnt = sum(len(t.get("items", [])) for t in data.get("themes", []))
rv = data.get("review", {})
json.dump(data, open(f"{BASE}/sections.json", "w"), ensure_ascii=False, indent=2)
print(f"AI 整理完成 sections.json：头条 {len(data.get('highlights', []))} 条 / "
      f"{len(data.get('themes', []))} 主题 / 正文 {cnt} 条 / 纵览 {len(rv.get('paras', []))} 段")
