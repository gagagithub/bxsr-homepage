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
② 养老：利率/降息/LPR、存款、国债、养老金/社保、人口老龄化、A股/基金/股市(养老钱怎么配)、**房地产/楼市(房价、二手房、租金、房贷利率、楼市新政、收储/城市更新、以房养老)——很多读者的养老底子就是手里那套房，楼市新闻优先往养老档放**。关联"养老钱往哪放、锁利率窗口、每月领1万得备多少本金、房子还值多少钱/租金能不能养老"。⚠这一档宽但不是垃圾桶：AI/算力/数据中心/芯片/能源电力/产业数据这类纯科技产业新闻【不要】塞进来——除非能直接落到读者的钱上(如机构明确说往哪类资产配、影响股市基金怎么配)，落不到就丢弃或只进头条；"数据集建了多少个、用电负荷创新高"这种跟养老钱无关的，宁可不要。
③ 传承：汇率、黄金、高端资产价格、财富、税费、家族企业股权传承、境外资产、房产的【过户/继承/赠与/房产税】话题、名人遗产/财富故事 —— 关联"家底保值、离婚隔离、过户vs遗嘱vs保单受益人、想给又不想现在给"。(楼市行情/房价类新闻归养老档，别两头放。)⚠公司资本运作类新闻(IPO/上市/退市/分拆/增发/监管处罚/高管被查/券商业绩/个体工商户政策)【不是】传承：跟"把家底传给孩子"没关系，一律不准进传承档。这一档同样宁缺毋滥：当天贴题的新闻就 2-3 条甚至没有都完全正常，items 少放或留空即可(insight 仍可基于当天汇率/黄金走势正常写)，绝不要拿不贴题的新闻凑数。
⚠同一条新闻全篇只能出现在一个主题里一次，绝不允许同一件事在一个主题里写两条、或两个主题里各写一条。"""

# 「健康小课堂」：健康档新闻天然高大上(药企/审批/政策)，不接地气；每天固定加一段贴身知识——
# 医保实操 / 三高慢病 / 体检就医，主题按日轮转不重样，讲法对标薄世宁式大白话科普。
# 这是全篇唯一"非新闻整理"的板块，医学合规护栏写在 build_user 的 tip 规则里。
TIP_TOPICS = [
    # —— 医保实操 ——
    "医保药品目录：甲类、乙类、丙类到底啥区别，为啥同样住院别人报得多",
    "住院报销到手为啥比想象少：起付线、封顶线、自费项目是怎么扣的",
    "门诊也能报销：门诊统筹是什么、怎么用",
    "异地看病要先备案：异地就医备案怎么办、不备案亏多少",
    "医保个人账户的钱能给家人用：家庭共济怎么开通",
    "医保断缴几个月，影响到底有多大",
    "集采药是不是便宜没好药",
    "大病保险：医保自带的二次报销，很多人不知道自己有",
    "进口药、靶向药为什么医保报不了多少",
    "现在住院天数变短了(DRG付费)，对病人意味着什么",
    "医保卡借给家人用的后果，比想象严重",
    "退休后还要不要缴医保：缴满多少年才能终身享受",
    "商业医疗险和医保怎么衔接才不花冤枉钱",
    "三高、糖尿病人的报销福利：特殊门诊/慢病门诊待遇怎么申请",
    "医保目录一年一调：新药进目录跟咱有什么关系",
    "体检为什么医保不给报",
    "急诊、救护车、住院前的检查费，哪些医保能报",
    "惠民保(城市普惠险)几十块一年，到底值不值得买",
    "同样的病，一级、二级、三级医院报销比例差多少",
    "看牙、配眼镜、体检：哪些医保管、哪些不管",
    # —— 三高·慢病 ——
    "高血压的诊断线是140/90：在家怎么量血压才准",
    "血压高但没感觉，要不要吃药",
    "降压药一旦吃上就停不下来？这个说法错在哪",
    "血脂化验单一堆指标，重点只看一个：低密度脂蛋白LDL-C",
    "他汀伤肝、要吃一辈子？常见误区一次说清",
    "空腹血糖和糖化血红蛋白，哪个更能说明问题",
    "糖尿病前期是可以逆转的，抓住这个窗口",
    "三高的共同源头：先管住腰围和体重",
    "盐不只在盐罐里：藏在酱油、挂面、面包里的隐形盐",
    "无糖饮料、代糖，血糖高的人到底能不能喝",
    "喝酒对血压血糖的真实影响：小酌也不养生",
    "走路是最便宜的降压药：一天走多少、怎么走才有效",
    "熬夜和三高的关系，比想象中直接",
    "脂肪肝不是胖子专利：瘦人也会得，怎么逆转",
    "尿酸高不等于痛风，但放着不管会出事",
    "打呼噜可能是病：睡眠呼吸暂停和高血压的关系",
    "降压药早上吃还是晚上吃",
    "保健品能不能降三高：钱别花错地方",
    "头晕、后脖颈发硬别硬扛：血压急升的危险信号",
    "家用血压计怎么选、多久校准一次",
    # —— 体检·就医 ——
    "45岁以后，第一次肠镜该安排了",
    "筛肺癌，低剂量螺旋CT比拍胸片靠谱在哪",
    "查出幽门螺杆菌：要不要治、全家要不要查",
    "甲状腺结节检出率很高：绝大多数不用切",
    "肺结节报告怎么看：多大的要紧、多久复查",
    "乳腺结节分级(BI-RADS)：几类该警惕",
    "体检报告上的箭头：哪些能观察，哪些必须复查",
    "骨密度检查：50岁后女性的必查项",
    "胃镜没那么可怕：无痛胃镜是怎么回事",
    "体检查出颈动脉斑块，慌不慌",
    "心梗的早期信号，不只是胸口疼",
    "脑梗抢救黄金4.5小时：记住这几个识别动作",
    "阿司匹林不是人人都要吃",
    "50岁上下该做的癌症筛查清单，一次列全",
    # —— 女性健康(公众号读者六成是女性) ——
    "更年期潮热失眠不用硬扛：哪些情况该去看医生",
    "甲状腺功能异常偏爱中年女性：怕冷、乏力、变胖别只当衰老",
    "乳腺癌筛查：钼靶和B超怎么选、多久查一次",
    "宫颈癌筛查做到几岁可以停：TCT和HPV检查怎么安排",
    "绝经后补钙就晚了吗：骨质疏松要从什么时候防",
    "带娃看孙辈累出的腱鞘炎、腰痛：怎么护理、医保怎么报",
    "给老伴管药的学问：几种慢病药同吃，最该注意什么",
    "体检报告上的子宫肌瘤：绝大多数不用手术",
]
import datetime as _dt
_bj_today = _dt.datetime.utcnow() + _dt.timedelta(hours=8)   # Action 在 UTC runner 上跑, 取北京日期轮转
TIP_TOPIC = TIP_TOPICS[_bj_today.toordinal() % len(TIP_TOPICS)]
print(f"今日健康小课堂主题：{TIP_TOPIC}")

SYS = """你是「保心上人」的财经晨报主笔，为保险规划师及其客户编每日《财经晨报》。

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

请整理成《财经晨报》的【部分内容】。严格输出如下 JSON（不要多余文字、不要 markdown 代码块）：
{{""")
    parts = []
    if want_meta:
        parts.append('''  "wechat_title": "公众号文章标题，18-28字，这是全篇最重要的一个字段，决定有没有人点开。第一步：从【今天给定的真实新闻】里挑对读者钱包冲击最大的一条(就是下面 lead 要写的那条)。第二步：套这几个句式之一做成标题(方括号处必须填今天那条新闻里的真实内容，句式只是壳、内容全部来自原文)：①金额/利率场景+身份代入『手里有[金额]存款的注意，[什么]又变了』②悬念设问『[机构]刚宣布[动作]，咱的钱该挪窝吗?』③政策+切身利害『[政策变化]，以后[看病/领钱]能[具体变化]』④名人故事+启示『[人名][事件]，给咱提了个醒』。硬要求：标题里的每一个数字、机构名、事件都必须出自你挑的那条原文，一个字都不许从句式示例里搬、更不许自己编；『身份代入』部分必须跟这条新闻真实相关(新闻讲汇率就写'要换外汇/有日元资产的'，讲医保就写'常吃药的'，不许跟内容无关地硬套'有存款的注意')；口语化像邻居大姐转发时会说的话；禁止'震惊/速看/必看'式恶俗词；禁止'收益翻倍/高好几倍'式收益暗示；纯文本不带任何标签、不带日期"''')
        parts.append('''  "lead": {"label":"机构/主体(2-8字)", "title":"≤20字小标题，和 wechat_title 说的是同一件事，进文章第一眼看到它", "text":"把 wechat_title 对应的那条新闻当【今日主打】写透：3-5句、180-260字，先一句点破为什么这事跟读者的钱直接相关，再把来龙去脉和全部关键数字讲清楚，口语化，关键数据<b>标红</b>。⚠只许用 id 指向的那条原文里已有的数字，不许自己换算举例、不许从别条新闻拼数字", "relate":"80-140字，单独一段说透这条新闻对这群45-65岁读者【具体该怎么办/怎么看】，往兴趣罗盘上靠，给得出场景就给场景(如'手里正好有笔定期到期的，这几天可以…')，中性不荐品、禁止拿任何产品类别做收益对比", "id": 原文id}''')
        parts.append('''  "hook": {"big":"≤14字大字钩子(必须是对象不是字符串)。和 wechat_title 同一件事的压缩版，抓眼，落到读者的钱/养老/健康/传承上，可用设问或点破利害(如'利率又降了，养老钱往哪放?')，别恶俗、别承诺收益", "sub":"≤28字副标题，承接大字，说清今天到底发生了啥、跟他们的钱有啥关系"}''')
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
    "title":"晨报纵览小标题(当天最大主线，20字内)",
    "paras":["3段、每段80-140字，像跟客户面对面聊天一样把当天最重要的几条新闻串成一条线讲明白，只用给定事实，最后一段务必落到'对40-60岁、一二线城市、操心健康养老传承的读者'该怎么看待今天这些消息，中性不荐品"]
  }''')
    schema = ",\n".join(parts)
    reqs = ["要求（务必做满）：",
            "- 每条新闻都必须是「label(机构/主体) + 完整一段(含全部数字)」，**不允许只有一句话、丢失数字的干瘪条目**。",
            "- 通篇口语化：短句、说人话、零新闻腔；但数字和细节一个不丢。"]
    if want_meta:
        reqs.append("- wechat_title、lead、hook 三者必须围绕【同一条新闻】(当天对读者钱包冲击最大的那条)，标题把人骗进来、lead 第一眼就兑现标题，不许货不对板。")
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
  * 口吻像一位靠谱的医生朋友早上跟你聊天：不吓人、不夸大、说人话，讲完让人觉得"今天学到一个真有用的"。""")
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

# ---- 数字溯源校验(防 AI 把标题示例句式里的数字/事件当成真新闻写进标题和主打) ----
def _tokens(s):
    """提取文本里的数字 token(含小数)。"""
    return set(re.findall(r"\d+(?:\.\d+)?", re.sub(r"<[^>]+>", "", str(s or ""))))

ALL_NEWS_TOKENS = _tokens(" ".join(it["text"] for it in items))

def lead_provenance_ok(d):
    """lead/wechat_title 里的每个数字必须能在其引用的那条原文里找到；relate 的数字放宽到全部给定新闻。
    校验不过=AI 编数字/引错原文, 一律判废。没有 lead 视为不通过(交给重试/回退)。"""
    lead = d.get("lead") or {}
    if isinstance(lead, str) or not lead.get("text"):
        return False, "无lead"
    i = lead.get("id")
    oi = idmap.get(i) if isinstance(i, int) else None
    if oi is None or not (0 <= oi < len(items)):
        return False, "lead引用了不存在的原文id"
    orig_tokens = _tokens(items[oi]["text"])
    # 标题(含lead小标题)最严: 数字必须出自其引用的那条原文(防"标题讲A事、正文引B文"的货不对板+编造)
    wt = d.get("wechat_title")
    wt = (wt.get("title") or wt.get("text") or "") if isinstance(wt, dict) else (wt or "")
    if not _tokens(wt) <= orig_tokens:
        return False, f"标题「{re.sub(chr(60)+'[^'+chr(62)+']+'+chr(62), '', str(wt))[:30]}」数字不在所引原文里: {sorted(_tokens(wt) - orig_tokens)[:5]}"
    if not (_tokens(lead.get("title")) <= orig_tokens):
        return False, f"lead小标题数字不在所引原文里: {sorted(_tokens(lead.get('title')) - orig_tokens)[:5]}"
    # 正文/relate 放宽到全部给定新闻(晨报允许同主题多条合并), 仍拦纯编造的数字
    if not _tokens(lead.get("text")) <= ALL_NEWS_TOKENS:
        return False, f"lead正文数字不在任何给定新闻里: {sorted(_tokens(lead.get('text')) - ALL_NEWS_TOKENS)[:5]}"
    if not _tokens(lead.get("relate")) <= ALL_NEWS_TOKENS:
        return False, f"relate数字不在任何给定新闻里: {sorted(_tokens(lead.get('relate')) - ALL_NEWS_TOKENS)[:5]}"
    return True, "ok"

# 三次调用：①看点(hook/风向/头条/朋友圈) ②养老+健康主题+健康小课堂 ③传承主题+纵览(各自都在 8K 输出上限内，保证 JSON 完整)
d0 = call(build_user(want_meta=True))                              # hook + trend + moment + highlights
_ok, _why = lead_provenance_ok(d0)
if not _ok:
    print(f"⚠ 主打/标题溯源校验不过({_why})，重试一次 d0", file=sys.stderr)
    d0_retry = call(build_user(want_meta=True))
    _ok2, _why2 = lead_provenance_ok(d0_retry)
    if _ok2:
        d0 = d0_retry
    else:
        # 两次都不干净: 丢弃 lead+爆款标题(渲染端自动回退无主打+日期标题), 其余字段保留
        print(f"⚠ 重试仍不过({_why2})，今天放弃主打/爆款标题，回退日期版", file=sys.stderr)
        d0.pop("lead", None)
        d0.pop("wechat_title", None)
# hook 大字也做数字校验(只对全部新闻放宽校验, 不过就丢, 封面回退通用版)
_hk = d0.get("hook")
_hk_txt = (_hk.get("big", "") + " " + _hk.get("sub", "")) if isinstance(_hk, dict) else str(_hk or "")
if not _tokens(_hk_txt) <= ALL_NEWS_TOKENS:
    print("⚠ hook 含无溯源数字，丢弃(封面回退通用版)", file=sys.stderr)
    d0.pop("hook", None)
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
    else:
        obj["link"] = ""; obj["src"] = ""
    obj.pop("id", None)
    return obj

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

cnt = sum(len(t.get("items", [])) for t in data.get("themes", []))
rv = data.get("review", {})
json.dump(data, open(f"{BASE}/sections.json", "w"), ensure_ascii=False, indent=2)
print(f"AI 整理完成 sections.json：头条 {len(data.get('highlights', []))} 条 / "
      f"{len(data.get('themes', []))} 主题 / 正文 {cnt} 条 / 纵览 {len(rv.get('paras', []))} 段 / "
      f"健康小课堂 {'有' if data.get('tip') else '⚠无'} / "
      f"主打 {'有' if data.get('lead') else '⚠无'} / 标题「{data.get('wechat_title') or '⚠无(回退日期标题)'}」")
