# -*- coding: utf-8 -*-
"""财经日报·AI 整理：把 news_raw.json 的真实新闻电报喂 DeepSeek，做去重/分类/挑重点/归板块/一句话保险视角。
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

# ---------- 养老民生素材(fetch_pension_news.py 联网检索, 崔伟 2026-07-28 要求) ----------
# 东财/新浪财经电报是给炒股的人看的, 出不来养老金/退休金/存款利率/大额存单这类内容
# (7-28 实测: 当天养老档 7 条里 0 条是这类)。这些素材单独联网搜回来, 并进新闻池供选材。
# ⚠非官方的必须带标注: 崔伟明确要求"写明非官方"。前缀直接写进喂给 AI 的正文里,
#   并在 THEME_GUIDE 里要求改写时保留出处、不许当成定论。
PENSION_SRC = {}   # id -> {"src","official","trusted"}
# ⚠正规财经媒体白名单(崔伟 2026-07-29 拍板放宽): 千问只按"是不是部委正式发布"给 official,
#   于是存款挂牌利率/大额存单这类最贴读者钱袋子、也最出爆款的题材(信源天生是财经媒体而非部委)
#   永远做不了主打 → 爆款标题连续两天(7-28/7-29)被闸门丢弃、公众号标题回退日期版。
#   这些媒体的报道视同可靠信源, 可以做主打和标题(正文仍写明出处); 名单外的仍按非官方拦。
# ⚠2026-08-07 白名单/溯源工具/小课堂主题池抽到 mr_common.py 与养老日报独立管线(llm_pension.py)共用,
#   改口径只改 mr_common, 两条管线同时生效。
from mr_common import TRUSTED_MEDIA, trusted_src as _trusted_src
try:
    _pn = json.load(open(f"{BASE}/pension_news.json", encoding="utf-8")).get("items", [])
except Exception:
    _pn = []
for it in _pn:
    t = str(it.get("text") or "").strip()
    if len(t) < 40:
        continue
    nid = len(indexed)
    src = str(it.get("src") or "").strip()
    off = bool(it.get("official"))
    trusted = _trusted_src(src)
    if off:
        tag = "【养老民生·官方发布】"
    elif trusted:
        tag = "【养老民生·媒体报道，可信财经媒体】"
    else:
        tag = "【养老民生·非官方，仅为他人说法】"
    stale = bool(it.get("stale"))
    if stale:
        tag += f"【发生于{it.get('stale_when') or '数日前'}，只进正文不做主打】"
    # 地方性(省市级)消息: 可以进正文, 但本号面向全国, 不能拿一个省的事当全国读者的主打/标题
    local = bool(it.get("local"))
    if local:
        tag += "【地方性消息，只进正文不做主打，正文必须写清是哪个省市】"
    indexed.append({"id": nid, "t": f"{tag}（据{src}）{t}"[:460]})
    # official 放宽: 部委正式发布 or 正规财经媒体报道 → 允许做主打/标题
    PENSION_SRC[nid] = {"src": src, "official": off or trusted,
                        "trusted": trusted and not off, "stale": stale, "local": local}
    # 不进 idmap: 这些素材没有东财原文链接, attach() 会自动给空 link, 渲染端本就兼容
if _pn:
    print(f"并入养老民生素材 {len(PENSION_SRC)} 条"
          f"(官方 {sum(1 for v in PENSION_SRC.values() if v['official'] and not v['trusted'])} / "
          f"可信媒体 {sum(1 for v in PENSION_SRC.values() if v['trusted'])} / "
          f"非官方 {sum(1 for v in PENSION_SRC.values() if not v['official'])})")

# 传承档禁入名单：公司资本运作类新闻(提示词拦不住 DeepSeek 硬塞, 改程序化硬闸——
# 提示词里给白名单, 合并时强制剔除违规 id)。纵览/头条/养老档不受此限。
_CAPMKT_RE = _re.compile(r"IPO|上市|退市|挂牌|分拆|增发|配股|申购|保荐|发行价|打新|"
                         r"科创板|创业板|北交所|新三板|个体工商户|借壳|重组上市")
_HERIT_RE = _re.compile(r"继承|遗产|赠与|过户|家族|信托|受益人|传承|遗嘱")
CHUANCHENG_BANNED = {e["id"] for e in indexed
                     if _CAPMKT_RE.search(e["t"]) and not _HERIT_RE.search(e["t"])}
print(f"传承档禁入(资本运作类) {len(CHUANCHENG_BANNED)} 条")

# 正文只按读者最关心的三大主题组织(每主题=当天相关新闻 + 一段解读), 不再按财经板块分类
THEMES = ["健康", "养老", "传承"]
THEME_ICON = {"健康": "🏥", "养老": "🌅", "传承": "🌳"}
THEME_GUIDE = """【三大主题怎么归类当天新闻(把每条真实新闻挑到最贴的一档, 与本主题无关的新闻直接丢掉不要硬塞)】
① 健康：只挑【跟老百姓看病、吃药、报销直接相关】的新闻——医保政策、药品集采/降价、进口药/创新药获批或进医保(落点是患者能不能用上、自费多少)、门诊住院报销、疫苗、体检筛查、慢病防治。⚠药企业绩/净利润/股价/融资这类"上市公司视角"的新闻【不要】放进健康档：除非它直接影响看病花钱，且必须改写成患者视角(别写利润涨多少，写这药治什么病、患者能省多少)；纯资本市场的医药消息归养老档(股市)或直接丢弃。这一档宁缺毋滥，当天确实没有民生向健康新闻就把 items 留空(每天固定有「健康小课堂」托底，不怕空)、insight 可留空字符串。
② 养老：⚠⚠**凡是标了【养老民生·官方发布】【养老民生·媒体报道，可信财经媒体】或【养老民生·非官方】前缀的条目, 全部优先进养老档并排在最前**——那是专门为这一档联网找回来的材料(养老金调整、退休金、存款挂牌利率、大额存单、储蓄国债、医保报销、长护险、适老化改造), 正是读者最关心的, 财经电报里没有, 一条都别浪费。⚠标【媒体报道，可信财经媒体】的条目**可以做主打和标题**(存款利率、大额存单、国债这类正是读者最想看的), 但正文必须写明是哪家媒体报道的、并保留事情发生的时间(如"建行7月10日发行"), 不许把媒体报道写成部委发文。⚠标【非官方】的条目改写时必须在正文里写明是谁说的(如"据某某媒体分析""有专家认为"), 绝不能写成板上钉钉的结论, **也不许做主打和标题**; 今年养老金调不调、几月调、调多少, 在人社部正式公布前一律只能转述, 不许下结论。其余可选题材: 利率/降息/LPR、存款、国债、养老金/社保、人口老龄化、A股/基金/股市(养老钱怎么配)、**房地产/楼市(房价、二手房、租金、房贷利率、楼市新政、收储/城市更新、以房养老)——很多读者的养老底子就是手里那套房，楼市新闻优先往养老档放**。关联"养老钱往哪放、锁利率窗口、每月领1万得备多少本金、房子还值多少钱/租金能不能养老"。⚠这一档宽但不是垃圾桶：AI/算力/数据中心/芯片/能源电力/产业数据这类纯科技产业新闻【不要】塞进来——除非能直接落到读者的钱上(如机构明确说往哪类资产配、影响股市基金怎么配)，落不到就丢弃或只进头条；"数据集建了多少个、用电负荷创新高"这种跟养老钱无关的，宁可不要。
③ 传承：汇率、黄金、高端资产价格、财富、税费、家族企业股权传承、境外资产、房产的【过户/继承/赠与/房产税】话题、名人遗产/财富故事 —— 关联"家底保值、离婚隔离、过户vs遗嘱vs保单受益人、想给又不想现在给"。(楼市行情/房价类新闻归养老档，别两头放。)⚠公司资本运作类新闻(IPO/上市/退市/分拆/增发/监管处罚/高管被查/券商业绩/个体工商户政策)【不是】传承：跟"把家底传给孩子"没关系，一律不准进传承档。这一档同样宁缺毋滥：当天贴题的新闻就 2-3 条甚至没有都完全正常，items 少放或留空即可(insight 仍可基于当天汇率/黄金走势正常写)，绝不要拿不贴题的新闻凑数。
⚠同一条新闻全篇只能出现在一个主题里一次，绝不允许同一件事在一个主题里写两条、或两个主题里各写一条。"""

# 「健康小课堂」：健康档新闻天然高大上(药企/审批/政策)，不接地气；每天固定加一段贴身知识——
# 医保实操 / 三高慢病 / 体检就医，主题按日轮转不重样，讲法对标薄世宁式大白话科普。
# 这是全篇唯一"非新闻整理"的板块，医学合规护栏写在 build_user 的 tip 规则里。
from mr_common import TIP_TOPICS
import datetime as _dt
_bj_today = _dt.datetime.utcnow() + _dt.timedelta(hours=8)   # Action 在 UTC runner 上跑, 取北京日期轮转
TIP_TOPIC = TIP_TOPICS[_bj_today.toordinal() % len(TIP_TOPICS)]
print(f"今日健康小课堂主题：{TIP_TOPIC}")

SYS = """你是「保心上人」的财经日报主笔，为保险规划师及其客户编每日《财经日报》。
这份日报**下午 6 点左右发出**，读者是下班/晚饭前后打开看的，讲的是**今天白天发生的事**(A股今天已收盘)。
所以时间措辞一律用"今天/今天上午/刚刚/今天收盘"，**不要写"昨夜/隔夜/今天早上"**——除非那条新闻本身讲的就是隔夜的美股、外盘，那种照实说"隔夜美股"。

【读者画像 + 兴趣罗盘，所有解读都对着这群人、往这些角度上靠】
45-65岁为主(46-60岁占四成、60岁以上占四分之一)，**六成多是女性**，广东、江苏、山东、京沪浙一带，家底殷实但不是金融从业者。股票、基金几乎人人在买，这是跟他们对话的共同语言；存款、大额存单、国债更是人人都有，利率一动他们最有感。
女性读者多，意味着解读多用**家庭视角**的口吻：自己的退休金、老伴的药费报销、爸妈的养老安排、留给孩子的钱——"我们家这笔钱怎么办"永远比"市场怎么走"更抓人。
他们对**具体金额的场景**最敏感："100万存款到期该怎么办""每月多领2000块养老金""进口药自费差3万"这种带真数字的身边事，远比宏观大词有吸引力。名人财富故事(遗产纠纷、大佬患病、明星理财翻车)也爱看，能借势时就借势。
他们真正爱看的是这些具体角度——
① 健康：结节/三高怎么核保、带病怎么投保；重疾太贵了，50岁后医疗+防癌怎么替代搭配；理赔到底能报多少、进口药自费差价(真数字最戳人)。
   少写：险种百科、条款讲解、拿发病率吓人。
② 养老：数字倒推(每月想领1万、得先备多少本金)；利率一路下行、还能锁长期利率的窗口；敢做对比(港险 vs 内地产品直接比，反正他也会去问AI)；**房子的事(不少读者的养老底子就是一两套房：房价涨跌、房租行情、房贷利率、楼市新政都直接关系"以房养老靠不靠得住"，这类新闻他们特别爱看，解读落点是"只靠房子养老行不行、要不要搭点别的")**。
   少写：干巴巴的宏观焦虑、政策搬运、单纯喊"去存款/买国债"、CRS、跟养老钱无关的科技产业新闻。
③ 传承：钱给了孩子又怕离婚被分走(保单怎么隔离)；过户 vs 遗嘱 vs 保单受益人三种传法的成本对比(讲场景不背法条)；想给钱又不想现在就给(年金分期给付、二婚/独生子女怎么继承)。
   少写：遗产税炒作(国内没这税，一戳就穿)。
写每条解读，先回答"这条新闻跟我这个岁数、我这个家底有什么关系"，再尽量往上面这些他们爱看的角度上靠。中性、不荐具体产品、不承诺收益。

【挑新闻的第二标准：可谈论性(转发的源头)】
相关度相当的两条新闻，永远优先挑读者会转到家族群、饭桌上能聊起来的那条——判断标准：有具体金额或惊人数字(豪宅一套5091万/茅台一天涨60元)、有画面感和身边感(黄金门店冷清了、APP偷偷诱导你借钱)、有名人或热闹事(名人拍卖/遗产/患病)。反面是文件复读型新闻(某部委印发某规划、某指数编制方案发布)：除非跟读者的钱直接相关，否则再"重要"也别选。

【文风铁律：说人话，别播新闻联播】
- 像一位懂行的老朋友傍晚收工后跟你唠今天的新闻，通篇口语化短句，一句话尽量不超过30字。
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
2. 绝对禁止新增任何原文里没有的数字、政策、机构表态、公司动作。数字必须原样保留，一个都不能改、不能编。(全篇唯一例外：如果用户消息里要求产出「健康小课堂 tip」字段，该字段允许使用新闻之外的公认医学/医保常识，专门护栏见用户消息。)
3. 与财经/财富/宏观无关、纯个股盘口异动("X板块高开""Y涨停")的条目，直接丢弃。
4. 禁止任何收益承诺/预测保证；禁止"稳赚/保本高收益/存款搬家/躺赚"等话术。
5. 保险相关表述用"可关注/配置参考"等中性措辞，不构成销售要约；不出现具体保险产品名。
6. 每条只引用一个原文 id（取你改写所依据的那条），用于挂原文链接。"""

NEWS_JSON = json.dumps(indexed, ensure_ascii=False)

def build_user(themes=None, want_meta=False, want_review=False, tip_topic=None):
    """themes=本次要产出的主题(健康/养老/传承子集); want_meta=产 hook+trend+moment+highlights; want_review=产 review; tip_topic=今天健康小课堂主题。"""
    head = (f"""今天的真实财经新闻电报如下（JSON 数组，id 为编号，t 为内容，已含较完整细节，请把同主题多条合并成一条更完整的）：
{NEWS_JSON}

请整理成《财经日报》的【部分内容】。严格输出如下 JSON（不要多余文字、不要 markdown 代码块）：
{{""")
    parts = []
    if want_meta:
        parts.append('''  "wechat_title": "公众号文章标题，18-28字，这是全篇最重要的一个字段，决定有没有人点开。第一步：从【今天给定的真实新闻】里挑对读者钱包冲击最大的一条(就是下面 lead 要写的那条)。⚠选题优先级(实测这几类阅读最高，永远优先做主打)：①存款/大额存单/定存利率下调 ②汇率(美元/日元/人民币) ③养老金/社保调整、保险预定利率、年金 ④房价/房贷/楼市新政(不少读者拿房养老) ⑤名人财富/遗产/患病故事、医保报销大事——这五类直接动到读者存款和养老钱、或能转到家族群聊的，永远比科技产业/公司资本运作/大盘涨跌优先；后者即使当天最热也别做主打，读者不关心。第二步：套这几个句式之一做成标题(方括号处必须填今天那条新闻里的真实内容，句式只是壳、内容全部来自原文)：①金额/利率场景+身份代入『手里有[金额]存款的注意，[什么]又变了』②悬念设问『[机构]刚宣布[动作]，咱的钱该挪窝吗?』③政策+切身利害『[政策变化]，以后[看病/领钱]能[具体变化]』④名人故事+启示『[人名][事件]，给咱提了个醒』。硬要求：标题里的每一个数字、机构名、事件都必须出自你挑的那条原文，一个字都不许从句式示例里搬、更不许自己编；『身份代入』部分必须跟这条新闻真实相关(新闻讲汇率就写'要换外汇/有日元资产的'，讲医保就写'常吃药的'，不许跟内容无关地硬套'有存款的注意')；口语化像邻居大姐转发时会说的话；禁止'震惊/速看/必看'式恶俗词；禁止'收益翻倍/高好几倍'式收益暗示；纯文本不带任何标签、不带日期"''')
        parts.append('''  "lead": {"label":"机构/主体(2-8字)", "title":"≤20字小标题，和 wechat_title 说的是同一件事，进文章第一眼看到它", "text":"把 wechat_title 对应的那条新闻当【今日主打】写透：4-6句、200-300字。这是留住读者的关键——标题把人骗进来了，这一段必须第一屏就把标题许的诺兑现，别让人扑空。写法：先一句直接把标题那个钩子接住(读者点进来就是想知道这个)，再把来龙去脉和全部关键数字一次讲清、讲满，让人读完这一段就觉得'这事我搞明白了、没白点'，口语化。⚠只许用 id 指向的那条原文里已有的数字，不许自己换算举例、不许从别条新闻拼数字，关键数据<b>标红</b>", "relate":"100-160字，单独一段说透这条新闻对这群45-65岁读者【具体该怎么办/怎么看】，往兴趣罗盘上靠，给得出场景就给场景(如'手里正好有笔定期到期的，这几天可以…')，中性不荐品、禁止拿任何产品类别做收益对比。⚠结尾必须单起一句、用大白话给一个【值得转发给家人的记忆点】：要么是一个让人心里一紧的真实数字对比、要么是一句好懂又好转的结论(像'钱放银行越来越不经放了，这事得早点合计'那种)，让读者看完想顺手转到家庭群——这一句直接决定这篇有没有人分享", "id": 原文id}''')
        parts.append('''  "hook": {"big":"≤14字大字钩子(必须是对象不是字符串)。和 wechat_title 同一件事的压缩版，抓眼，落到读者的钱/养老/健康/传承上，可用设问或点破利害(如'利率又降了，养老钱往哪放?')，别恶俗、别承诺收益", "sub":"≤28字副标题，承接大字，说清今天到底发生了啥、跟他们的钱有啥关系"}''')
        parts.append('  "trend": "40字内今日风向，口语化，像跟同事说\'今天就盯这一两件事\'，关键词<b>加粗</b>"')
        parts.append('''  "moment_text": "一段适合规划师发【客户朋友圈】的文案，4-6行、每行短句、可用1-2个emoji，开头点出今日财经看点(用真实数字)，结尾引导'点开看完整日报'。务必合规：不承诺收益、不出现'稳赚/保本/存款搬家'、不荐具体产品，纯财经资讯分享口吻，专业可信"''')
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
      "insight": "承接上面这些新闻，用大白话讲清今天这些事对【本主题·这群40-60岁读者】到底意味着什么，往兴趣罗盘里他们爱看的角度上靠(养老=数字倒推/锁利率窗口/以房养老靠不靠得住；传承=离婚隔离/三种传法/想给又不想现在给；健康=进口药报多少/带病投保/重疾替代)，120-200字，关键处<b>标红</b>，中性、不荐具体产品、不承诺收益"
    }
  ]''')
    if tip_topic:
        parts.append('''  "tip": {
    "title": "≤18字的大白话标题(可以设问、可以点破一个常见误区，别标题党)",
    "body": "250-330字，围绕今天指定的小课堂主题把【一个知识点】讲透：先点破一个大家普遍搞错或忽视的地方，再用大白话(可以打生活比方)讲清楚道理，最后给一条今天就能照着做的具体建议。分成3-5句，关键结论/公认标准数字用<b>标出</b>"
  }''')
    if want_review:
        parts.append('''  "review": {
    "title":"日报纵览小标题(当天最大主线，20字内)",
    "paras":["3段、每段80-140字，像跟客户面对面聊天一样把当天最重要的几条新闻串成一条线讲明白，只用给定事实，最后一段务必落到'对40-60岁、一二线城市、操心健康养老传承的读者'该怎么看待今天这些消息，中性不荐品"]
  }''')
    schema = ",\n".join(parts)
    reqs = ["要求（务必做满）：",
            "- 每条新闻都必须是「label(机构/主体) + 完整一段(含全部数字)」，**不允许只有一句话、丢失数字的干瘪条目**。",
            "- 通篇口语化：短句、说人话、零新闻腔；但数字和细节一个不丢。"]
    if want_meta:
        reqs.append("- wechat_title、lead、hook 三者必须围绕【同一条新闻】(当天对读者钱包冲击最大的那条)，标题把人骗进来、lead 第一眼就兑现标题，不许货不对板。")
        reqs.append("- ⚠主打选题铁律：优先存款利率/汇率/养老金/楼市/年金这类【直接动读者存款和养老钱】的新闻；只有当天确实没有这类，才退而挑名人财富/医保大事；纯科技产业、公司IPO/资本运作、大盘涨跌一律不做主打。")
        reqs.append("- ⚠lead.relate 结尾那句【值得转发给家人的记忆点】务必写足写好——它是这篇能不能被转发的唯一钩子(日报当前分享量近乎为零)，宁可朴实好懂，也别写空泛口号。")
        reqs.append("- highlights 给 **5-6 条**当天最重磅的，每条写成完整一段(这块会作为开头「今日看点」下的头条精选)；lead 已经写透的那条**不要**再出现在 highlights 里。")
    if themes:
        reqs.append(THEME_GUIDE)
        reqs.append(f"- 本次只产出这些主题：{'、'.join(themes)}。每个主题：items 挑 **3-8 条**当天最相关的真实新闻(健康/传承档新闻少、可少可空)，每条写满；insight 一段必给(仅 items 为空的档 insight 可留空字符串)。与三大主题都无关的新闻(如纯个股盘口)直接丢弃，不要硬塞。")
        if "养老" in themes:
            reqs.append("- ⚠养老档是最宽的一档，items 至少 3 条(利率/存款/股市/楼市/宏观里总有跟养老钱相关的)，绝不允许留空；insight 里提到的每条新闻都必须同时出现在 items 里，不许只在 insight 里点名。")
        if "传承" in themes and CHUANCHENG_BANNED:
            reqs.append("- ⚠传承档 items 禁止引用这些 id(公司资本运作类，与家庭财富传承无关，程序会强制剔除): "
                        + ",".join(map(str, sorted(CHUANCHENG_BANNED)))
                        + " 。也不要把这些新闻换个说法塞进来；贴题候选不足时 items 少放甚至留空，insight 仍按当天汇率/黄金走势正常写。")
    if tip_topic:
        reqs.append(f"""- 「健康小课堂 tip」今天的主题是【{tip_topic}】。这是全篇唯一允许使用给定新闻之外知识的字段，硬护栏如下，违反作废：
  * 只写医学界/医保制度里公认、多年稳定的常识(如高血压诊断标准140/90)；拿不准的数字宁可不写，绝对禁止编造统计数据、研究结论、政策细节。
  * 不诊断、不开方、不提任何具体药品品牌和保健品；凡涉及吃药、停药、换药、剂量，落点必须是"具体听主治医生的"。
  * 医保政策各地有差异的，点一句"各地标准不同，以当地医保部门为准"。
  * 通篇纯科普，不聊保险、不带货(有用的知识本身就是价值)；只有主题本身就是医保报销/看病花钱类时，才可以在结尾自然带一句"哪些费用医保管不到、要自己心里有数"式的中性提醒，仍然不提任何产品。
  * 口吻像一位靠谱的医生朋友傍晚跟你聊天：不吓人、不夸大、说人话，讲完让人觉得"今天学到一个真有用的"。""")
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
        "model": "deepseek-v4-pro",  # 2026-07-25: DeepSeek 下线 deepseek-chat, 只认 deepseek-v4-pro / deepseek-v4-flash
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

# ---- 数字溯源校验(防 AI 把标题示例句式里的数字/事件当成真新闻写进标题和主打) ----
from mr_common import _tokens, _isnum, _unsourced

# ⚠溯源的"源"必须覆盖【所有喂给 AI 的条目】, 不能只有东财 items ——
# 养老民生素材(联网检索来的)不在 items 里, 漏掉它们会让引用这些素材的主打/标题被误判成编造数字
# (而"基础养老金涨20元"这类恰恰最该做主打)。
# ⚠2026-08-07 抽 mr_common 时这两行被漏迁, 导致 llm_morning 直接 NameError 跑不完, 别再删。
TEXT_BY_ID = {e["id"]: e["t"] for e in indexed}
ALL_NEWS_TOKENS = _tokens(" ".join(TEXT_BY_ID.values()))

def lead_provenance_ok(d):
    """lead/wechat_title 里的每个数字必须能在其引用的那条原文里找到(整数容许四舍五入)；relate 放宽到全部给定新闻。
    校验不过=AI 编数字/引错原文, 一律判废。没有 lead 视为不通过(交给重试/回退)。"""
    lead = d.get("lead") or {}
    if isinstance(lead, str) or not lead.get("text"):
        return False, "无lead"
    i = lead.get("id")
    oi = idmap.get(i) if isinstance(i, int) else None
    # 东财条目走 items; 养老民生素材不在 items 里, 回退到 TEXT_BY_ID(喂给 AI 的原文)
    src_txt = items[oi]["text"] if (oi is not None and 0 <= oi < len(items)) \
              else (TEXT_BY_ID.get(i) if isinstance(i, int) else None)
    if not src_txt:
        return False, "lead引用了不存在的原文id"
    # ⚠非官方的养老民生素材(媒体分析/专家观点/自媒体说法)不许占主打和标题位置 ——
    # 那是全篇最显眼的地方, 只能放官方正式发布的事实; 非官方内容进正文并注明出处即可。
    if isinstance(i, int) and i in PENSION_SRC and not PENSION_SRC[i]["official"]:
        return False, f"主打引用了非官方素材(据{PENSION_SRC[i]['src']}), 不许做标题"
    # 旧闻(事情发生在数日前)可以进正文, 但不能当今天的主打/爆款标题 —— 7-28 第1期被否的真因
    if isinstance(i, int) and i in PENSION_SRC and PENSION_SRC[i].get("stale"):
        return False, f"主打引用了旧闻(据{PENSION_SRC[i]['src']}, 事情发生在数日前), 不许做标题"
    # 地方性消息同理: 本号读者遍布全国, 拿某省某市的事做爆款标题, 大多数人点进来发现跟自己无关
    # (7-29 就把标题挑成了「湖南两病取消起付线」)
    if isinstance(i, int) and i in PENSION_SRC and PENSION_SRC[i].get("local"):
        return False, f"主打引用了地方性消息(据{PENSION_SRC[i]['src']}), 全国号不许拿它做标题"
    orig_tokens = _tokens(src_txt)
    # 标题(含lead小标题)最严: 数字必须出自其引用的那条原文(防"标题讲A事、正文引B文"的货不对板+编造)
    wt = d.get("wechat_title")
    wt = (wt.get("title") or wt.get("text") or "") if isinstance(wt, dict) else (wt or "")
    bad = _unsourced(wt, orig_tokens)
    if bad:
        return False, f"标题「{re.sub(chr(60)+'[^'+chr(62)+']+'+chr(62), '', str(wt))[:30]}」数字不在所引原文里: {sorted(bad)[:5]}"
    bad = _unsourced(lead.get("title"), orig_tokens)
    if bad:
        return False, f"lead小标题数字不在所引原文里: {sorted(bad)[:5]}"
    # 正文/relate 放宽到全部给定新闻(日报允许同主题多条合并), 仍拦纯编造的数字
    bad = _unsourced(lead.get("text"), ALL_NEWS_TOKENS)
    if bad:
        return False, f"lead正文数字不在任何给定新闻里: {sorted(bad)[:5]}"
    bad = _unsourced(lead.get("relate"), ALL_NEWS_TOKENS)
    if bad:
        return False, f"relate数字不在任何给定新闻里: {sorted(bad)[:5]}"
    return True, "ok"

# 三次调用：①看点(hook/风向/头条/朋友圈) ②养老+健康主题+健康小课堂 ③传承主题+纵览(各自都在 8K 输出上限内，保证 JSON 完整)
# ⚠2026-08-05 崔伟拍板撤掉溯源拦截("不要加阀门了""我会自己检查的"):
#   闸门连续误杀(7-24 四舍五入 6↔6.17、8-05 中文数字 五年期↔5年期)导致标题老回退日期版,
#   改为只在日志报警供人工核对, 不重试不丢弃 —— 发布前由崔伟人工检查标题/主打/封面。
d0 = call(build_user(want_meta=True))                              # hook + trend + moment + highlights
_ok, _why = lead_provenance_ok(d0)
if not _ok:
    print(f"⚠ [仅报警不拦截] 主打/标题有疑似无源数字, 发布前人工核对: {_why}", file=sys.stderr)
_hk = d0.get("hook")
_hk_txt = (_hk.get("big", "") + " " + _hk.get("sub", "")) if isinstance(_hk, dict) else str(_hk or "")
_hk_bad = _unsourced(_hk_txt, ALL_NEWS_TOKENS)
if _hk_bad:
    print(f"⚠ [仅报警不拦截] hook 有疑似无源数字, 发布前人工核对: {sorted(_hk_bad)[:5]}", file=sys.stderr)
d1 = call(build_user(themes=["养老", "健康"], tip_topic=TIP_TOPIC))  # 养老(最宽) + 健康 + 小课堂
def _theme_items(d, name):
    for _t in d.get("themes", []):
        if _t.get("name") == name:
            return [x for x in _t.get("items", []) if x.get("text")]
    return []
if len(_theme_items(d1, "养老")) < 2:   # DeepSeek 偶发把新闻全写进 insight、items 留空 → 整体重试一次
    print("⚠ 养老档条目过少(<2)，重试 d1 一次")
    d1 = call(build_user(themes=["养老", "健康"], tip_topic=TIP_TOPIC))
d2 = call(build_user(themes=["传承"], want_review=True))          # 传承 + 纵览

# 合并（主题按 THEMES 顺序：健康/养老/传承）
themap = {}
for t in (d1.get("themes", []) + d2.get("themes", [])):
    if t.get("name"):
        themap[t["name"]] = t
_hook = d0.get("hook", {}) or {}
if isinstance(_hook, str):            # DeepSeek 偶尔把 hook 直接返回成一句话, 归一成 {big}
    _hook = {"big": _hook}
_tip = d1.get("tip", {}) or {}
if isinstance(_tip, str):             # 同 hook, 归一成 {body}
    _tip = {"body": _tip}
_tip["topic"] = TIP_TOPIC
_wtitle = d0.get("wechat_title", "")
if isinstance(_wtitle, dict):         # 防 DeepSeek 偶发包一层对象
    _wtitle = _wtitle.get("title") or _wtitle.get("text") or ""
_wtitle = re.sub(r"<[^>]+>", "", str(_wtitle)).strip()
_lead = d0.get("lead", {}) or {}
if isinstance(_lead, str):
    _lead = {"text": _lead}
data = {
    "wechat_title": _wtitle,
    "lead": _lead if _lead.get("text") else {},
    "hook": _hook,
    "trend": d0.get("trend", ""),
    "moment_text": d0.get("moment_text", ""),
    "highlights": d0.get("highlights", []),
    "themes": [themap[n] for n in THEMES if n in themap],
    "tip": _tip if _tip.get("body") else {},
    "review": d2.get("review", {}),
}

# 把 AI 引用的 id 还原成真实 link/time（绝不信任 AI 自己写的链接）；id 经 idmap 映射回原 items
def attach(obj):
    i = obj.get("id")
    oi = idmap.get(i) if isinstance(i, int) else None
    if oi is not None and 0 <= oi < len(items):
        obj["link"] = items[oi].get("link", "")
        obj["src"] = items[oi].get("src", "")
    elif isinstance(i, int) and i in PENSION_SRC:
        # 养老民生素材: 无原文链接, 但要把出处带出来给渲染端显示(据人社部…)
        obj["link"] = ""
        obj["src"] = PENSION_SRC[i]["src"] + ("" if PENSION_SRC[i]["official"] else "，非官方")
    else:
        obj["link"] = ""; obj["src"] = ""
    obj.pop("id", None)
    return obj

# 先记下正文已用的原文 id(供文末「其他要闻速览」排除, attach 会把 id 弹掉所以必须先收)
_used_ids = set()
if isinstance(data.get("lead", {}).get("id"), int):
    _used_ids.add(data["lead"]["id"])
for h in data.get("highlights", []):
    if isinstance(h.get("id"), int):
        _used_ids.add(h["id"])
for th in data.get("themes", []):
    for it in th.get("items", []):
        if isinstance(it.get("id"), int):
            _used_ids.add(it["id"])

if data.get("lead"):
    attach(data["lead"])
for h in data.get("highlights", []):
    attach(h)

# 跨主题/主题内程序化去重：d1(养老+健康)和 d2(传承)是两次独立调用互相看不见，
# 同一条原文可能被两边各选一次(如茅台批价曾同时进养老和传承)；按原文 id 全局只保留首次出现。
# 同时硬性执行传承档资本运作禁入名单(提示词拦不住时的最后闸门)。
_seen_ids = set()
_seen_labels = set()   # 跨主题同 label 视为同一件事(如两条不同 id 的茅台批价快讯被两次调用各选一次)
for th in data.get("themes", []):
    kept = []
    _my_labels = set()
    for it in th.get("items", []):
        i = it.get("id")
        lab = re.sub(r"\s+", "", str(it.get("label", "")))
        if isinstance(i, int):
            if i in _seen_ids:
                continue
            if lab and lab in _seen_labels and lab not in _my_labels:  # 只拦跨主题重复, 主题内同机构不同新闻放行
                print(f"跨主题同主体去重剔除：「{th.get('name')}」的【{lab}】")
                continue
            if th.get("name") == "传承" and i in CHUANCHENG_BANNED:
                print(f"传承档硬闸剔除资本运作条目 id={i}：{str(it.get('label',''))}")
                continue
            _seen_ids.add(i)
        if lab:
            _my_labels.add(lab)
        kept.append(it)
    _seen_labels |= _my_labels
    if len(kept) != len(th.get("items", [])):
        print(f"去重/硬闸：主题「{th.get('name')}」剔除 {len(th.get('items', [])) - len(kept)} 条")
    th["items"] = kept

for th in data.get("themes", []):
    for it in th.get("items", []):
        attach(it)

# ---------- 文末「其他要闻速览」(崔伟拍板≤12条)：三主题/头条/主打没用到的新闻压成一句话, 恢复一站式覆盖 ----------
BRIEF_CATS = ["股市", "楼市", "宏观", "公司", "环球"]
def gen_briefs():
    unused = [e for e in indexed if e["id"] not in _used_ids]
    if not unused:
        return []
    # 正文已覆盖的主体清单：防止同一件事换个信源/id 再进速览(如茅台批价另一条电报)
    used_labels = []
    for _src in ([data.get("lead", {})] + data.get("highlights", [])
                 + [it for t in data.get("themes", []) for it in t.get("items", [])]):
        _l = re.sub(r"\s+", "", str(_src.get("label", "")))
        if _l and _l not in used_labels:
            used_labels.append(_l)
    user = (
        "下面是今天【已在日报正文用过的新闻之外】剩下的真实新闻电报(JSON 数组, id 为编号):\n"
        + json.dumps(unused, ensure_ascii=False)
        + '\n\n请挑最多 12 条做成文末「其他要闻速览」。严格输出如下 JSON(不要多余文字、不要 markdown 代码块):\n'
          '{"briefs": [{"cat":"分类, 只能从这些里选: 股市/楼市/宏观/公司/环球", "label":"机构/主体(2-8字)", '
          '"text":"压成一句话(40字内, 口语化, 保留最关键的那个数字, 纯文本不要任何标签)", "id": 原文id}]}\n\n'
          "要求:\n"
          "- 按可谈论性挑：具体金额/惊人数字/画面感身边事/名人热闹事优先；纯个股盘口异动、文件复读型不选。\n"
          f"- ⚠日报正文已经覆盖了这些主体/事件：{('、'.join(used_labels)) or '无'}。跟它们说的是同一件事的新闻(哪怕信源不同、说法不同)一律不要再选。\n"
          "- 同一话题(如同一场冲突/同一只酒的价格)最多出 1 条，挑信息量最大的那条，别把一件事的多个侧面拆成几条。\n"
          "- 一条新闻只出一次；贴题的不足 12 条就少给，不许硬凑。\n"
          "- 只用给定新闻，数字一个不许改、不许编。"
    )
    d3 = call(user)
    out, seen = [], set()
    _used_lab_set = set(used_labels)
    for b in d3.get("briefs", []):
        i = b.get("id")
        if not isinstance(i, int) or i in _used_ids or i in seen:
            continue
        if re.sub(r"\s+", "", str(b.get("label", ""))) in _used_lab_set:  # 同名主体兜底剔除
            continue
        if not str(b.get("text", "")).strip():
            continue
        b["cat"] = b.get("cat") if b.get("cat") in BRIEF_CATS else "宏观"
        b["text"] = re.sub(r"<[^>]+>", "", str(b["text"])).strip()
        seen.add(i)
        out.append(b)
        if len(out) >= 12:
            break
    out.sort(key=lambda x: BRIEF_CATS.index(x["cat"]))  # 按 股市/楼市/宏观/公司/环球 归组, 组内保持AI给的顺序
    return out

try:
    data["briefs"] = gen_briefs()
except Exception as e:
    print(f"⚠ 其他要闻速览生成失败(不阻塞日报): {e}")
    data["briefs"] = []
for b in data.get("briefs", []):
    attach(b)

# ---------- 养老日报(发「崔伟说养老」公众号): 复用养老档已整理好的新闻, 单独出标题+导语 ----------
# 为什么单独出一次: 全局 wechat_title/lead 是按"当天对钱包冲击最大"挑的, 不保证落在养老话题上
# (今天就落在土拍)。养老号的标题必须从养老档里挑, 否则文不对题。
# 输入是养老档【已整理成稿的条目】而不是原始电报: 数字已经过一轮溯源, 这里再校验一次防二次编造。
# ⚠地方性标题硬闸(2026-07-30): 「崔伟说养老」是面向全国的号, 拿"湖南两病取消起付线"
#   "上海高龄医保"这种一个省的政策做标题, 外省读者点进来发现跟自己无关 —— 7-29 已经吃过一次。
#   fetch_pension_news 那边已经按发布方给素材标了 local, 但那个标记到不了这里(gen_pension 的
#   输入是 DeepSeek 已成稿的条目, 标签早被剥掉了), 所以这里按【标题里出现省市名 + 它引用的
#   那条原文没有全国性字样】独立再判一次。提示词里也写了这条, 但重要口径不能只靠提示词(7-19 教训)。
from mr_common import title_is_local as _title_is_local

def gen_pension():
    th = next((t for t in data.get("themes", []) if t.get("name") == "养老"), None)
    its = [it for it in (th or {}).get("items", []) if it.get("text")]
    if len(its) < 2:
        print("⚠ 养老档不足 2 条, 跳过养老日报 meta")
        return {}
    src = [{"i": n, "label": str(it.get("label", "")), "t": re.sub(r"<[^>]+>", "", str(it["text"]))}
           for n, it in enumerate(its, 1)]
    user = (
        "下面是今天《财经日报》养老档已经整理好的新闻条目(JSON 数组, i 为编号):\n"
        + json.dumps(src, ensure_ascii=False)
        + "\n\n这些内容今天还要单独发一份《养老日报》到面向中老年读者的公众号。"
          "读者是 50-70 岁、关心自己退休金和养老钱的普通人(不是炒股的)。"
          "请严格输出如下 JSON(不要多余文字、不要 markdown 代码块):\n"
          '{\n'
          '  "title": "公众号标题, 18-28字。挑哪一条做, 判据只有一个:【这条能不能落到一个退休老人自己的账本上】。'
          '✅优先: 存款/大额存单利率变动、养老金退休金调整、房价房租房贷、物价、医保报销、养老服务收费。'
          '❌绝不许做标题: ①行业总规模类数字(理财存续多少万亿、发行多少万只、成交多少亿——这种钱读者看不见摸不着, 毫无代入感, 是最差的标题) '
          '②大盘涨跌与ETF成交 ③公司回购/业绩/融资 ④券商机构的观点和预测 '
          '⑤**只在某一个省/市执行的地方性政策**(本号读者遍布全国, 拿一个省的事做标题, 外省读者点进来发现跟自己无关)——'
          '这类内容可以写进正文, 但标题要挑全国范围的事。'
          '如果上面条目里实在没有一条能落到个人账本, 就挑最贴近生活的那条写个平实标题, 不许硬凑代入感、不许标题党。'
          '口语化、像邻居大姐会转发的话, 可用身份代入(如"手里有定期存款的注意")或设问; '
          '禁止"震惊/速看/必看"式恶俗词; 禁止收益暗示; 不带日期不带标签。⚠标题里每个数字都必须出自上面条目原文, 一个字都不许编",\n'
          '  "lead": "导语 150-220字, 承接标题那条讲透: 先一句把标题的钩子接住, 再把关键数字讲清, 最后一句落到"这跟您的养老钱有什么关系"。'
          '大白话短句, 一句不超30字。⚠只许用上面条目里已有的数字",\n'
          '  "insight": "一段 150-220字的整体解读, 放在正文末尾。⚠⚠只许谈这两类事:①养老金/退休待遇/社保 ②存款利率/大额存单/国债/理财收益。'
          '**绝对不许提楼市房价、黄金、基金、股市、汇率**——这份养老日报的正文里只留了上面那两类内容, '
          '解读里提别的, 读者会发现在聊自己没读到的新闻。如果上面条目里这两类都没什么可说的, 就只就手头有的那条展开, 宁短勿凑。'
          '口吻是跟老朋友唠, 落点是"咱这个岁数, 这笔钱该怎么打算", 中性不荐产品不承诺收益",\n'
          '  "i": 你做标题所依据的那个条目编号\n'
          '}'
    )
    d4 = call(user)
    title = re.sub(r"<[^>]+>", "", str(d4.get("title", ""))).strip()
    lead = str(d4.get("lead", "")).strip()
    pinsight = str(d4.get("insight", "")).strip()
    i = d4.get("i")
    if not title or not lead:
        return {}
    # 溯源: 标题按其引用的那条校验(最严), 导语放宽到整个养老档(允许同主题合并)
    one = src[i - 1]["t"] if isinstance(i, int) and 1 <= i <= len(src) else ""
    bad_t = _unsourced(title, _tokens(one) if one else _tokens(" ".join(s["t"] for s in src)))
    bad_l = _unsourced(lead, _tokens(" ".join(s["t"] for s in src)))
    bad_loc = _title_is_local(title, one)
    if bad_t or bad_l or bad_loc:
        # 重试一次再判废(同 d0 主打的做法): 常见触发是 AI 顺手做了换算(每月20元→一年240元),
        # 闸门分不清"正确换算"和"编造"只能一律拦, 但换个说法往往就不用算术了。
        print(f"⚠ 养老日报溯源不过(标题{sorted(bad_t)} 导语{sorted(bad_l)}"
              f"{f' 地方性「{bad_loc}」' if bad_loc else ''}), 重试一次")
        d4 = call(user + "\n\n⚠上一版出现了原文里没有的数字, 被判废。请重写: "
                         "只用上面条目里出现过的数字原样引用, **不要做任何加减乘除换算**"
                         "(比如别把每月多少元乘12算成一年多少元), 也不要举例推算。"
                 + (f"\n⚠另外, 上一版标题挑的是只在「{bad_loc}」执行的地方性政策, 外省读者点进来跟自己无关。"
                    "请换一条【全国范围】的做标题(部委发布、全国统一执行、或多地同步的); "
                    "实在没有全国性的条目, 就挑一条不带地名、讲普遍现象的写。" if bad_loc else ""))
        title = re.sub(r"<[^>]+>", "", str(d4.get("title", ""))).strip()
        lead = str(d4.get("lead", "")).strip()
        pinsight = str(d4.get("insight", "")).strip()
        i = d4.get("i")
        if not title or not lead:
            return {}
        one = src[i - 1]["t"] if isinstance(i, int) and 1 <= i <= len(src) else ""
        bad_t = _unsourced(title, _tokens(one) if one else _tokens(" ".join(s["t"] for s in src)))
        bad_l = _unsourced(lead, _tokens(" ".join(s["t"] for s in src)))
        bad_loc = _title_is_local(title, one)
        if bad_t or bad_l or bad_loc:
            print(f"⚠ 重试仍不过(标题{sorted(bad_t)} 导语{sorted(bad_l)}"
                  f"{f' 地方性「{bad_loc}」' if bad_loc else ''}), 丢弃标题回退日期版")
            return {}
    # 解读单独校验: 不过关只丢解读, 不牵连标题(渲染端会回退用日报养老档的 insight)
    if pinsight and _unsourced(pinsight, _tokens(" ".join(s["t"] for s in src))):
        print("⚠ 养老日报解读有野数字, 丢弃(回退日报养老档解读)")
        pinsight = ""
    print(f"养老日报标题「{title}」解读{'有' if pinsight else '无'}")
    return {"title": title, "lead": lead, "insight": pinsight}

# ⚠ 2026-07-30 崔伟拍板养老日报停做, 这次 DeepSeek 调用默认不跑。
# ⚠ 2026-08-07 养老日报已恢复, 但走独立管线(llm_pension.py + daily-pension-report.yml, 北京12:00),
#   不再走这里的 gen_pension() —— 独立管线 12:00 跑时, 本脚本的养老档成稿还不存在。
#   PENSION_DAILY 开关与 gen_pension() 保留仅作参考, 别再打开(会跟独立管线出两套标题)。
if os.environ.get("PENSION_DAILY") == "1":
    try:
        data["pension"] = gen_pension()
    except Exception as e:
        print(f"⚠ 养老日报 meta 生成失败(不阻塞日报): {e}")
        data["pension"] = {}
else:
    data["pension"] = {}

cnt = sum(len(t.get("items", [])) for t in data.get("themes", []))
rv = data.get("review", {})
json.dump(data, open(f"{BASE}/sections.json", "w"), ensure_ascii=False, indent=2)
print(f"AI 整理完成 sections.json：头条 {len(data.get('highlights', []))} 条 / "
      f"{len(data.get('themes', []))} 主题 / 正文 {cnt} 条 / 速览 {len(data.get('briefs', []))} 条 / "
      f"纵览 {len(rv.get('paras', []))} 段 / "
      f"健康小课堂 {'有' if data.get('tip') else '⚠无'} / "
      f"主打 {'有' if data.get('lead') else '⚠无'} / 标题「{data.get('wechat_title') or '⚠无(回退日期标题)'}」/ "
      f"养老日报 {'「' + data['pension']['title'] + '」' if data.get('pension', {}).get('title') else ('已停做' if os.environ.get('PENSION_DAILY') != '1' else '⚠无(回退日期标题)')}")
