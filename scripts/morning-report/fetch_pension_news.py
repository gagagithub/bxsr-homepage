# -*- coding: utf-8 -*-
"""日报·养老民生素材源(AI 联网检索)。

崔伟 2026-07-28: "每天准备日报的时候, 多找找这些方面的材料、文章和内容 —— 养老、退休金、
养老金上涨、存款、大额存单这类老年人关心的。但写明, 非官方。"

为什么要单独一个源: 日报现有新闻源(东财/新浪财经电报)是给炒股的人看的, 出不来这些内容。
实测 2026-07-28 当天养老档 7 条里, 0 条是养老金/退休金/存款利率这类民生话题, 全是
A股大跌、ETF成交、上海土拍、券商观点。跟 7-11 健康档撞的是同一堵墙, 解法也一样:
让 qwen-plus 联网自己去找(DeepSeek API 无联网能力)。

输出 pension_news.json = {"items": [{"text","src","date","official"}]}
→ 由 llm_morning.py 并入新闻池, 养老档优先用这些素材。

⚠非官方标注(崔伟明确要求): official=false 的条目是媒体分析/自媒体说法/专家观点,
  llm_morning 会带【养老民生·非官方】前缀喂给 DeepSeek, 并要求改写时必须写明出处、
  不许当成定论。official=true 只给部委/官方媒体正式发布的内容。
⚠只转述别人说了什么, 绝不生成我们自己的预测; 无出处的说法一律丢弃(防把谣言洗成"据称")。
检索失败/无结果 → 输出空 items, 养老档回退为东财原有内容(不阻塞日报)。
"""
import os, json, re, sys, urllib.request, time as _t, difflib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = f"{BASE}/pension_news.json"

def get_key():
    k = os.environ.get("DASHSCOPE_API_KEY")
    if k:
        return k
    try:  # 本地跑: 复用电台配音的凭证文件
        txt = open(os.path.expanduser("~/.aliyun/nls.env"), encoding="utf-8").read()
        m = re.search(r"DASHSCOPE_API_KEY=(\S+)", txt)
        return m.group(1) if m else None
    except Exception:
        return None

def bail(msg):
    print(f"⚠ {msg} —— 写空 pension_news.json, 养老档回退东财原有内容", file=sys.stderr)
    json.dump({"items": []}, open(OUT, "w"), ensure_ascii=False)
    sys.exit(0)

key = get_key()
if not key:
    bail("未找到 DASHSCOPE_API_KEY")

bj_now = datetime.now(timezone.utc) + timedelta(hours=8)
TODAY = f"{bj_now.year}年{bj_now.month}月{bj_now.day}日"
YEAR = bj_now.year
TODAY_ISO = bj_now.strftime("%Y-%m-%d")

# ⚠2026-08-07 起一天有两条管线跑到这里, 当天已检索过就复用留底、不重新联网检索。
# ⚠2026-08-31 财经晨报改 07:00、养老日报仍 16:00, 两条拉开 9 小时后, 无条件复用会**害了养老号**:
#   养老 16:00 会原样吃早上 7 点那批素材, 整个白天的养老新闻(政策通常上午/下午发)全部丢掉。
#   故复用加时效窗口 REUSE_HOURS: 只有距上次检索够近才算"同一批新闻"。
#   (原注释担心的"后跑那次把先跑的结果当排除清单、素材凭空清零"是 8-27 引入 14 天 history 之前的
#    旧机制; 现在 exclude_for/程序化去重都从 pension_news_history.json 取且已过滤掉当天,
#    重搜不会拿今天早上的排除自己 —— 代价只是多一次千问调用。)
REUSE_HOURS = 4
try:
    _prev_doc = json.load(open(OUT))
except Exception:
    _prev_doc = {}
_prev_at = _prev_doc.get("fetched_at")
_reuse_age = None
if _prev_doc.get("fetched_date") == TODAY_ISO and _prev_doc.get("items") and _prev_at:
    try:
        _reuse_age = (bj_now.replace(tzinfo=None)
                      - datetime.strptime(_prev_at, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
    except Exception:
        _reuse_age = None
if _reuse_age is not None and 0 <= _reuse_age < REUSE_HOURS:
    print(f"ⓘ 今天 {_prev_at} 已检索过({len(_prev_doc['items'])} 条, 另一条管线留底, "
          f"{_reuse_age:.1f} 小时前), 直接复用不重搜")
    sys.exit(0)
if _prev_doc.get("fetched_date") == TODAY_ISO:
    print(f"ⓘ 当天留底已过时(上次 {_prev_at or '无时刻'}, 超过 {REUSE_HOURS} 小时), 重新检索")

# ---------- 跨天历史(2026-08-27 崔伟"变成宽素材") ----------
# ⚠原做法: prev_by_topic 直接读 pension_news.json 当排除清单 —— 但那个文件**每天被整个覆盖**,
#   所以历史窗口永远只有 1 天。8-21 报过"四大行重启五年大额存单", 8-22/8-23 一覆盖,
#   8-24 这一路的排除清单里就没它了, 于是原样搜回来又做了一次标题。8-09 连日撞题同一个机制。
#   (实测跨天相似度: 国债理财 0.79→0.88→0.93→0.96, 财新那条从 7-30 到 8-24 相似度恒为 1.00。)
# 改成把每天的结果追加进 pension_news_history.json(保留 14 天, 随 CI 一起 commit)。分两个用途:
#   ① **程序化跨天去重**(不进提示词, 因此不稀释检索词): 与 14 天内任一条相似度 ≥SIM_DUP 且
#      **没有新数字**的直接丢。提示词里那句"不要重复"是软的, 换个措辞就漏; 这一道是硬的。
#   ② 每路的排除清单仍只塞 6 条进提示词(2026-07-30 定的口径, 多了会压过检索意图),
#      但这 6 条改从 14 天里挑**互不相同**的, 而不是昨天那一批。
HIST = f"{BASE}/pension_news_history.json"
HIST_DAYS = 14
SIM_DUP = 0.62      # 实测: 同一件事换个措辞落在 0.64~1.00; 阈值再低会误伤同一路的不同政策

def _norm(t):
    """归一化到只剩汉字和数字 —— 措辞、标点变了但说的是同一件事, 归一化后相似度仍然很高。"""
    return re.sub(r"[^\u4e00-\u9fa5\d%]", "", str(t))[:80]

def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()

# ⚠去重必须放过"变化点": "五年期大额存单利率 1.60%" 和 "…降到 1.55%" 措辞几乎一样,
#   但后者正是本号最该报的事。规则: 剔掉日期后, 新文本里出现了历史文本没有的数字 → 判为新进展, 保留。
_DATE_RE = re.compile(r"\d{4}年|\d{1,2}月\d{1,2}日|\d{1,2}月|\d{1,2}日")

def _new_numbers(a, b):
    na = set(re.findall(r"\d+(?:\.\d+)?", _DATE_RE.sub("", a)))
    nb = set(re.findall(r"\d+(?:\.\d+)?", _DATE_RE.sub("", b)))
    return na - nb

try:
    _hist_days = json.load(open(HIST)).get("days", [])
except Exception:
    _hist_days = []
# 同日重跑(或两条管线同天各跑一次)不算历史, 否则会拿自己排除自己
_hist_days = [d for d in _hist_days if d.get("date") != TODAY_ISO][-HIST_DAYS:]
HIST_ITEMS = [(d.get("date", ""), it.get("topic", ""), str(it.get("text") or ""),
               _norm(it.get("text"))) for d in _hist_days for it in d.get("items", [])]
print(f"跨天历史: {len(_hist_days)} 天 / {len(HIST_ITEMS)} 条"
      f"{'(首次运行, 历史为空)' if not HIST_ITEMS else ''}")

def exclude_for(tname):
    """从 14 天历史里给这一路挑最多 6 条**互不相同**的做排除清单(新的排在前)。"""
    picked = []
    for _d, tp, txt, nm in reversed(HIST_ITEMS):
        if tp != tname:
            continue
        if any(_sim(nm, pn) >= SIM_DUP for _t2, pn in picked):
            continue          # 同一件事在历史里连着好几天, 只占排除清单一个名额
        picked.append((txt, nm))
        if len(picked) >= 6:
            break
    if not picked:
        return ""
    return ("\n\n以下是最近两周这一类已经报过的内容, **今天不要再报同一件事**"
            "(除非出了实质性新进展: 通知正式下发、利率再次变动、标准又调了):\n"
            + "\n".join(f"- {t[:60]}…" for t, _ in picked))


# ---------- 多路专项检索(崔伟 7-28: "扩大线索源") ----------
# 原来一次问 5 大类, qwen 每类只摊得到一两条; 拆成各问各的, 每路 2-4 条, 总量翻几倍,
# 且都限定"今天或最近1-2天" —— 日报就该是当天的, 靠放宽天数凑数会变成旧闻。
#
# ⚠2026-07-30 由 4 路扩到 7 路。起因: 7-29 旧闻硬闸一开, 过闸后只剩 5 条、存款国债档整块空,
#   暴露的是**线索源本身不够宽**, 不是闸门太严。扩的办法仍是 7-28 验证过的那一招 ——
#   **一路摊不到就拆路**(每路无论问多大, qwen 都只回 2-4 条, 所以"问得窄"直接等于"总量多"):
#   · 原「存款理财」一路要同时覆盖 存款利率/大额存单/国债/理财 → 拆成【银行存款】+【国债理财】
#   · 原「医保养老服务」一路要同时覆盖 医保待遇/长护险/养老服务 → 拆成【医保待遇】+【养老服务】
#   · 新增【个人养老金】—— 个人养老金账户/税优/惠民保/专属商业养老保险, 是本号业务正对口的题材,
#     财经电报和上面几路都覆盖不到。
#   新增路捞回的内容必须归得进档才有意义, 故 render_pension.py 的 _TUIXIU_RE 同步补了养老服务类词。
#
# ⚠2026-08-07 由 7 路扩到 11 路(崔伟"养老日报内容太少")。当天实测 7 路只回 8 条, 其中
#   【防骗提醒】【养老服务】【个人养老金】三路交白卷 —— 民生新闻本来就不是天天有, 唯一的解仍是
#   **把问题问得更窄**。新增四路都是电报和原 7 路覆盖不到、却是退休家庭天天要花钱的事:
#   · 【物价开销】菜价/水电燃气/供暖/公交票价 —— 养老金涨没涨是收入端, 这是支出端, 一样是账本
#   · 【异地就医】备案怎么办/能报多少/跨省结算 —— 随子女养老的老人最常问
#   · 【老年优待】高龄津贴/敬老卡/免费乘车/免费体检 —— "不去问就没人告诉你"的钱
#   · 【遗属待遇】丧葬费/抚恤金/个人账户余额继承 —— 遇到才知道去问, 讲清楚就是一条能存下来的文章
#   并发 4→6(11 路串行会拖长 CI)。归档口径同步补(见 render_pension.py 的 _TUIXIU_RE/_WUJIA_RE),
#   否则跟 7-30 那次一样"捞得再多也白捞"。
# ⚠2026-08-27 崔伟"我需要变成宽素材" —— 加宽分两处做, 都在**取数侧**, 不加任何拦内容的阀门:
#
# (A) **子问句按天轮转**。原来每路把 ①②③④ 四个子问题一次全问出去, 但 7-28/7-30 两次扩路
#     已经验证过: **qwen 无论问多大, 每路都只回 2-4 条** —— 于是"一次问四个"的真实效果是
#     「永远只回答其中最热的那一个」。所以【养老金】那一路天天答"今年调整进展",
#     【国债理财】天天答同一批国债(实测跨天相似度 0.79→0.88→0.93→0.94→0.96, 等于每天抄自己)。
#     改成: 核心问句每天都问(大事发生的当天不能漏), 另外从子问句池里按天轮 2 个上来,
#     让检索意图**每天真的不一样**。子问句池越长, 这一路的题材面越宽 —— 加宽写在这里, 不用加路。
#     各路轮转相位按路序错开(ti*3), 免得所有路同一天集体转到"标准/现状"这类问题上。
#
# (B) **由 11 路扩到 15 路**。原 11 路全是"钱和政策", 但退休家庭的账本不止这些。新增四路是
#     电报和原 11 路都覆盖不到、却天天有新闻的题材:
#     · 【老年健康】慢病用药/集采药落地/疫苗/体检 —— 原【医保待遇】只管报销和缴费, 用药是另一件事
#     · 【退休生活】老年大学/旅居养老/退休返聘 —— 有钱之后"日子怎么过", 分享率高
#     · 【继承赡养】遗嘱/房产过户/赡养纠纷/老年人财产权益 —— 遇到才知道去问, 讲清就是能存的文章
#     · 【数字适老】电子社保卡/医保码/手机银行适老版 —— "不会用"直接等于"领不到、报不了"
#     ⚠新增路捞回的内容必须归得进档才有意义(7-30 教训: 捞得再多也白捞),
#     render_pension.py 的 _TUIXIU_RE 已同步补词, 老年健康那一路走既有的 spill_health→健康档。
#     并发 6→8(15 路串行会拖长 CI)。
TOPICS = [
    ("养老金",
     "{year}年基本养老金调整的最新进展(通知发布了没有、各省落地到哪一步、有没有官方辟谣)。",
     ["城乡居民基础养老金最低标准的调整、补发与各省加发。",
      "养老金资格认证怎么办(线上怎么认、什么时候截止、不认证会不会停发)。",
      "延迟退休与弹性退休的落地情况(怎么申请、早退晚退差多少)。",
      "养老保险缴费年限、断缴、补缴、一次性趸缴的新规定。",
      "工龄、视同缴费年限、特殊工种提前退休的认定与争议。",
      "养老保险关系跨省转移接续怎么办、在哪退休更划算。",
      "企业职工与机关事业单位退休待遇并轨的进展与差距数据。",
      "全国社保基金的收支、结余、投资收益与可持续性官方数据。"]),
    ("银行存款",
     "银行存款挂牌利率调整(哪些银行、降了多少个基点、几年期), 优先国有大行和主要股份行。",
     ["大额存单的发行、重启、停售、额度与利率变化。",
      "特色存款/智能存款/通知存款/协定存款的新规或下架。",
      "存款保险制度、银行倒闭赔付上限、存款安全的官方说明。",
      "定期存款提前支取、自动转存、靠档计息规则的变化。",
      "中小银行、村镇银行的存款利率与风险处置情况。",
      "居民存款搬家、存款利率进入'1时代'的官方与权威媒体数据。"]),
    ("国债理财",
     "储蓄国债(电子式/凭证式)的发行安排、票面利率、什么时候能买、买不买得到。",
     ["银行理财产品的收益变化、破净、提前终止与业绩比较基准下调。",
      "货币基金/余额宝类收益率跌破多少的变化。",
      "LPR 与市场利率变动对老百姓存钱的影响。",
      "国债逆回购、同业存单指数基金这类稳健品种的收益变化。",
      "存款替代类产品(增额终身寿、年金险)预定利率的官方调整。",
      "债券基金的回撤与风险提示, 稳健理财到底还稳不稳。"]),
    ("医保待遇",
     "医保报销政策变化、门诊和住院待遇调整、报销比例与起付线。",
     ["医保个人账户、家庭共济、账户划入金额的新规。",
      "居民医保缴费标准、参保政策与断缴后果。",
      "职工医保退休后不缴费的年限要求与各地差异。",
      "大病保险、医疗救助、二次报销的待遇标准。",
      "医保基金监管、骗保典型案例与个人参保信用。",
      "生育、门诊慢特病、日间手术等特殊待遇的调整。"]),
    ("老年健康",
     "药品集采落地与常用药、进口药、创新药的价格与医保准入变化。",
     ["高血压、糖尿病、高血脂等慢病用药的报销与用药新规。",
      "老年人免费体检、癌症筛查、骨密度与认知障碍筛查的公共服务。",
      "流感、带状疱疹、肺炎等老年人疫苗的接种政策与补贴。",
      "国家卫健委关于老年人健康管理、家庭医生签约的新举措。",
      "常见保健品、医疗器械的虚假宣传查处与官方提示。",
      "老年人跌倒、失能、认知障碍的防治指南与官方数据。"]),
    ("养老服务",
     "长期护理保险试点进展、扩围与待遇标准。",
     ["养老服务补贴、高龄津贴、养老金以外的老年补助。",
      "社区助餐、老年食堂、居家养老上门服务的价格与覆盖。",
      "适老化改造、老旧小区加装电梯的补贴与进度。",
      "养老院床位、收费标准与公办养老机构轮候情况。",
      "养老机构预付费监管、暴雷跑路的处置与官方提示。",
      "农村养老、互助养老、村级幸福院的建设情况。"]),
    ("个人养老金",
     "个人养老金制度的最新政策(账户开立、缴存上限、税收优惠、领取规则)。",
     ["个人养老金可投产品的变化(储蓄、理财、基金、商业养老保险)。",
      "个人养老金开户人数、缴存率、'开户不缴存'的官方数据与原因。",
      "专属商业养老保险、税优健康险的进展与结算利率。",
      "各地惠民保的参保、报销、调整与理赔数据。",
      "个人养老金提前领取、特殊情形支取的新规定。"]),
    ("防骗提醒",
     "针对老年人的养老诈骗、非法集资、理财骗局的官方提示或已查处的典型案例"
     "(要有办案机关或官方媒体出处, 讲清套路和涉案金额)。",
     ["以'养老服务''养老公寓''以房养老'为名的集资案件判决与追赃。",
      "保健品、收藏品、纪念币骗局的查处通报。",
      "冒充公检法、冒充社保医保工作人员的电信诈骗新套路。",
      "AI 换脸、AI 拟声诈骗针对老年人的官方预警。",
      "养老领域非法集资的行政处置、清退与受害人登记进展。"]),
    ("物价开销",
     "统计局公布的 CPI 与食品价格(菜价、肉价、蛋价、粮油)变化。",
     ["水、电、燃气、供暖、自来水的价格或收费标准调整。",
      "公交地铁票价、话费宽带资费的调整。",
      "米面粮油等生活必需品的价格波动与保供措施。",
      "药品、日用品、殡葬服务等价格的监管与降价。",
      "物业费、停车费的收费规范与调整。"]),
    ("异地就医",
     "异地就医备案与直接结算的新规(备案怎么办、能报多少、哪些医院能刷)。",
     ["门诊统筹与门诊慢特病跨省结算的进展。",
      "随子女异地养老、长期驻外人员的医保待遇。",
      "医保关系转移接续、跨省通办的新举措。",
      "港澳台及境外就医的医保报销政策。",
      "异地安置退休人员备案后的报销比例差异。"]),
    ("老年优待",
     "高龄津贴、老年人补贴的发放标准与调整。",
     ["敬老卡/老年证的办理与免费乘车、景区门票优待。",
      "老年人交通、通信、水电燃气等方面的优惠政策。",
      "面向老年人的免费体检、免费接种等公共服务。",
      "老年人乘坐火车、飞机、长途客运的优待与便利措施。",
      "银行、医院、政务大厅为老年人保留的线下人工窗口要求。"]),
    ("遗属待遇",
     "遗属待遇(丧葬补助金、抚恤金)的标准、发放与新规。",
     ["供养亲属抚恤金的计发办法与申领条件。",
      "退休人员去世后养老金个人账户余额继承的规定与办理流程。",
      "丧葬费抚恤金的跨省差异与'全国统一'进展。",
      "去世后医保个人账户余额、住房公积金的继承与提取。"]),
    ("继承赡养",
     "老年人财产权益保护的官方规定与法院典型案例(要有法院或官方媒体出处)。",
     ["遗嘱怎么立才有效(自书、代书、公证、遗嘱库)与继承新规。",
      "房产继承、过户的流程、税费与'公证难'的简化措施。",
      "赡养纠纷、'常回家看看'的司法实践与典型判决。",
      "老年人再婚、婚前财产、意定监护的法律安排。",
      "老年人被诱导签合同、抵押房产的撤销与救济案例。"]),
    ("退休生活",
     "国家关于发展老年教育、老年大学、银发经济的政策与数据。",
     ["退休返聘、超龄劳动者的权益保护与工伤认定新规。",
      "旅居养老、候鸟式养老、老年旅游的规范与消费提示。",
      "老年人再就业的岗位、收入与官方统计。",
      "老年大学扩容、社区课堂、老年兴趣班的供给情况。",
      "银发经济、适老产品与老年消费市场的官方数据。"]),
    ("数字适老",
     "电子社保卡、医保码在看病买药、领待遇上的新功能与推广。",
     ["手机银行、支付软件适老化改造与大字版、关怀模式。",
      "政务服务'一网通办'里老年人高频事项的线上办理。",
      "老年人防沉迷、防诱导消费、个人信息保护的新规。",
      "智能手机培训、数字反诈教育等面向老年人的公共服务。",
      "保留线下渠道、不得强制使用智能设备的官方要求。"]),
]

# 每路当天真正问出去的问题 = 核心问句 + 当天轮到的 2 个子问句(见上面 (A))
_ROT = bj_now.toordinal()

def daily_q(ti, core, subs):
    # ⚠{year} 必须在这里替掉: SEARCH_TMPL.format(topic_q=...) **不会**对插进去的值再 format 一次,
    #   所以原来【养老金】那一路一直在拿字面量"{year}年基本养老金调整"去搜(顺修, 2026-08-27)。
    core = core.replace("{year}", str(YEAR))
    subs = [s.replace("{year}", str(YEAR)) for s in subs]
    if not subs:
        return core
    k = len(subs)
    pick = [subs[(_ROT + ti * 3 + j) % k] for j in range(min(2, k))]
    return "①" + core + "\n" + "\n".join(f"{c}{q}" for c, q in zip("②③", pick))

TOPIC_Q = [(name, daily_q(i, core, subs)) for i, (name, core, subs) in enumerate(TOPICS)]
print("今日各路检索角度:")
for _n, _q in TOPIC_Q:
    print(f"  【{_n}】" + " / ".join(x.strip("①②③") for x in _q.split("\n")[1:]))

# ⚠两步走(实测教训, 见 fetch_health_news.py): 用户消息里塞 JSON schema 会污染联网检索词、
# 直接交白卷; 第一步自由文本搜, 第二步不联网纯整理成 JSON(不引入新信息)。
SEARCH_TMPL = """今天是{today}(北京时间)。请联网搜索**今天或最近1-2天**中国跟【退休老人的钱】直接相关的消息:
{topic_q}

对每一条, 必须给出: 内容(保留报道里的具体数字、金额、比例、时间)、是谁发布或是谁说的、日期。
⚠严格要求:
- 只报告真实检索到的内容, 一个字都不许编。搜不到就明说搜不到。
- 必须分清: 哪些是部委/官方媒体正式发布的, 哪些只是财经媒体分析、专家观点或自媒体说法。
- 说不出是谁说的、找不到出处的传言, 不要收录。
- 不要给出你自己的预测或判断。特别是{year}年养老金调不调、几月调、调多少,
  在人社部正式公布前, 只能转述别人怎么说的, 不许自己下结论。
- ⚠**优先全国范围的消息**(部委发布、全国统一执行、多省同步): 读者遍布全国, 某个市、某个区
  的经办通知(如"某区第一批失能评估名单公示")对绝大多数人没用。地方消息只有在
  【省级及以上】或【全国首个/试点扩围/多地跟进】时才值得报, 报的时候要说清是哪里。
- ⚠同一个细分话题最多 2 条, 宁可覆盖面广一点, 别一口气全是同一件事的不同地方版本。
每条必须真实、有出处, 搜不到就明说。{exclude}"""

FORMAT_USER = """把下面这份检索结果整理成 JSON(不要多余文字、不要 markdown 代码块):
{"items": [
  {"text": "内容完整一段(100-180字), 只用检索结果里已有的事实和数字, 不做评论",
   "src": "⚠只填【发布方的名称】, 8字以内最好, 如 人社部 / 中国银行 / 国家医保局 / 江苏省检察院 / 财新。"
          "绝不要把文章标题填进来(如《2026年7月中国大额存单最新调整…》这种一长条); "
          "⚠**要填最先报道的那家媒体, 不要填转载的地方门户**(如实际是21世纪经济报道/新浪财经首发、"
          "被某某新闻网转载的, src 填 21世纪经济报道, 不填某某新闻网); "
          "如果只查到文章、说不清发布方, 就填 网络文章",
   "date": "YYYY-MM-DD",
   "official": true 或 false}
]}
official 的判断: 部委、官方媒体、银行等主体【正式发布】的事实 = true;
财经媒体的分析解读、专家观点、自媒体说法、对未公布事项的推测 = false。
要求: 最多4条, 按对退休老人的实际影响排序; 说不清出处的一律不要;
⚠**全国性的排在前面**, 区县级的经办通知/名单公示一律不要(读者遍布全国, 用不上);
⚠**同一细分话题最多保留 2 条**(例: 4 条都是不同城市的长护险通知 → 只留最有代表性的 2 条);
⚠**优先保留"变化点"而不是"现状"**: 同一家银行/机构的消息, "重启发售5年期大额存单、利率1.60%"
这种【新出现、首次、恢复、上调下调】的事, 永远比"某产品现在利率是多少"更值得写 ——
读者要的是"又出新东西了/又变了", 不是一张常年不动的价目表。
一条消息里既有变化点又有现状时, 把变化点写在最前面, 别只留现状。
⚠**绝对不要收录具体个人的故事和案例**(如"河南商丘的张阿姨这个月到账238元""李大爷多领了多少") ——
这类内容多出自自媒体, 人物和金额都可能是编的, 无法核实。只要政策、标准、数据本身。
检索结果说没搜到就输出 {"items": []}; 绝不添加检索结果里没有的信息。

检索结果如下:
"""

def call_qwen(messages, enable_search):
    payload = json.dumps({
        "model": "qwen-plus",
        "input": {"messages": messages},
        "parameters": {"enable_search": enable_search, "result_format": "message"},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read().decode())
    return resp["output"]["choices"][0]["message"]["content"].strip()

def call_once(topic_q, exclude):
    found = call_qwen([
        {"role": "system", "content": "你是新闻检索助手, 只报告真实检索到的内容, 宁缺毋滥, 严禁编造。"},
        {"role": "user", "content": SEARCH_TMPL.format(today=TODAY, year=YEAR,
                                                       topic_q=topic_q, exclude=exclude)},
    ], enable_search=True)
    content = call_qwen([{"role": "user", "content": FORMAT_USER + found}], enable_search=False)
    content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
    if not content.startswith("{"):
        m = re.search(r"\{.*\}", content, flags=re.S)
        if m:
            content = m.group(0)
    return json.loads(content), len(found)

# ---------- 逐路检索: 并发跑, 按 TOPICS 顺序合并 ----------
# 每路 2 次 qwen 调用(联网搜 + 不联网整理), 串行跑 7 路要 5-8 分钟, 会把 16:05 那条 CI 拖长;
# 并发 4 路后总时长基本回到原来 4 路串行的水平。日志在各路内部先攒着, 跑完按顺序打印,
# 免得几路的输出交叉在一起没法读。
# 合并顺序固定按 TOPICS 排(不按谁先返回), 保证跨路去重的结果每天可复现。
def run_topic(t):
    tname, tq = t
    logs = []
    # 重试 3 次 + 退避: DashScope 偶发 `URLError: EOF occurred in violation of protocol`,
    # 7-30 首跑就有一路(医保待遇)连吃两次直接整路交白卷。这是纯网络抖动, 退避重试就能救回来。
    for k in range(3):
        try:
            d, n_found = call_once(tq, exclude_for(tname))
            logs.append(f"   第一步联网检索返回 {n_found} 字" + (f"(第{k+1}次尝试)" if k else ""))
            return tname, d, logs
        except Exception as e:
            logs.append(f"!! 【{tname}】第{k+1}次失败({type(e).__name__}: {str(e)[:60]})")
            _t.sleep(2 + 3 * k)
    return tname, None, logs

with ThreadPoolExecutor(max_workers=8) as _ex:
    results = list(_ex.map(run_topic, TOPIC_Q))

raw_items, seen_head = [], set()
for tname, d, logs in results:
    print(f"—— 检索【{tname}】")
    for line in logs:
        print(line if not line.startswith("!!") else line, file=sys.stderr if line.startswith("!!") else sys.stdout)
    if not d:
        continue
    got = 0
    for it in (d.get("items") or []):
        txt = str(it.get("text") or "")
        if not txt:
            continue
        # ⚠跨路去重原来按 text[:24] 前缀比 —— 措辞一换就漏(8-09 那次【养老金】和【养老服务】
        #   两路讲的是同一件事, 前缀不同全都放行了)。改成跟跨天去重同一套相似度口径。
        nm = _norm(txt)
        if any(_sim(nm, s) >= SIM_DUP for s in seen_head):
            continue
        seen_head.add(nm); it["topic"] = tname; raw_items.append(it); got += 1
    print(f"   【{tname}】收 {got} 条")
data = {"items": raw_items}
if not raw_items:
    bail(f"{len(TOPICS)}路联网检索均无结果")

# ⚠个人案例硬闸(程序化, 提示词不可靠 —— 7-28 首跑就搜回"河南商丘的张阿姨,62岁,社保卡到账238元",
# 被判成 official=true 进了正文, 下一轮 AI 甚至把"张阿姨"写进了标题)。
# 这类带人名的故事多是自媒体编的, 人物金额都无法核实, 我们转述等于替它背书 —— 整条丢弃。
_PERSON_RE = re.compile(r"[一-龥]{1,2}(阿姨|大爷|大妈|大叔|叔叔|婶|老太太|老爷子|老伯|奶奶|爷爷)")

# ⚠旧闻硬闸(崔伟 2026-07-29: "中行大额存单已经是老新闻了" —— 7月1日发售的产品,
# 7月29日被当成日报主打发出去)。
# **千问返回的 date 字段不可信**: 上面那三条大额存单(中行7月1日/建行7月10日/农行7月8日)
# 它全部标成了 2026-07-29(检索当天), 光看 date 字段一条都拦不住。
# 真正的时点信号在正文里 —— "2026年7月1日起""7月10日起"。规则: 把正文提到的月日全抽出来,
# 若**全部**都比今天早 STALE_DAYS 天以上 → 判旧闻整条丢弃; 只要有一个是近几天的
# 或者是未来的(如"8月1日起施行") → 保留。正文里一个日期都没有的(政策现状类)不拦。
# 宁可某一档当天没料整块不出, 也不拿上个月的事冒充今天 —— 这是日报, 不是资料库。
STALE_DAYS = 3
_MD_RE = re.compile(r"(\d{1,2})月(\d{1,2})日")
# ⚠"截至X日"这种参照日期必须先剔掉再判(2026-07-30 实测漏网): 中行那条正文是
#   「中国银行于2026年7月1日重启…农业银行7月8日、建设银行7月10日跟进…其余国有大行**截至7月29日**仍未上架」
#   —— 三个事件日期全是旧的, 但末尾那个"截至7月29日"是近三天内的, all() 一票否决,
#   整条没被标 stale → 当天的主打和爆款标题又落回崔伟已经两次否掉的"大额存单1.60%"。
#   "截至/截止/至今/迄今+日期"说的是统计时点, 不是事情发生的时点, 判旧闻时不作数。
_ASOF_RE = re.compile(r"(?:截至|截止|至今|迄今|统计至|数据截至)\s*(?:\d{4}年)?\d{1,2}月\d{1,2}日")

def stale_dates(text):
    """返回 (是否旧闻, 正文里最早的那个日期字符串) —— 便于日志说清楚为什么丢。"""
    found = []
    today = datetime(bj_now.year, bj_now.month, bj_now.day)
    text = _ASOF_RE.sub("", text)      # 参照日期不算事件日期(见上)
    for m in _MD_RE.finditer(text):
        try:
            d = datetime(bj_now.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        found.append(d)
    if not found:
        return False, ""
    if all((today - d).days > STALE_DAYS for d in found):
        return True, min(found).strftime("%-m月%-d日")
    return False, ""

# ⚠地方性硬闸(2026-07-30 扩到 7 路后首跑就撞上): 新增的【养老服务】一路收回 4 条, 全部是
#   贵港市 / 西藏 / 重庆长寿区 / 南通如东 的长护险经办通知 —— 「崔伟说养老」是面向全国的号,
#   区县级的"第一批失能评估名单公示"对绝大多数读者一个字都用不上, 而且 7-29 刚吃过一次亏
#   (标题挑成「湖南两病取消起付线」, 地方性内容当全国号标题)。
#   分三级: 区县级/经办分中心 → 整条丢弃(价值确实为零); 省市级 → 保留但标 local, 只进正文
#   不做主打和标题(可以写成"多地已在推"); 部委发布或正文点明全国范围的 → 不受限。
_NATIONAL_RE = re.compile(
    r"人社部|财政部|国家医保局|国家卫健委|民政部|国务院|中国人民银行|央行|金融监管总局|"
    r"国家发展改革委|国家发改委|国家税务总局|银保监|证监会|中办|国办|中共中央|"
    r"全国|各省|31个省|多地|全国统一")
_COUNTY_RE = re.compile(
    r"分中心|事务中心|经办中心|服务中心|[一-龥]{2,4}(?<!自治)区(?!域|间)|[一-龥]{1,3}县")
# ⚠光靠"省/自治区/市"三个字判不出来: 实测发布方写成「广西政府」「湖南医保局」时一个字都不含,
#   直接被当成全国性放行了。必须再按省名/主要城市名兜一道。
_PLACE_RE = re.compile(
    r"北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|"
    r"湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|内蒙古|广西|西藏|宁夏|新疆|"
    r"深圳|广州|杭州|南京|成都|武汉|西安|苏州|青岛|长沙|郑州|合肥|济南|福州|厦门|宁波")
_PROVCITY_RE = re.compile(r"省|自治区|[一-龥]{2,4}市")

def scope_of(src, text):
    """返回 ('全国'|'地方'|None, 地名) —— None 表示区县级, 整条丢。"""
    head = src + "｜" + text[:60]
    if _NATIONAL_RE.search(head):
        return "全国", ""
    m = _COUNTY_RE.search(src) or _COUNTY_RE.search(text[:40])
    if m:
        return None, m.group(0)
    m = _PROVCITY_RE.search(src) or _PLACE_RE.search(src)
    if m:
        return "地方", src
    return "全国", ""       # 判不出来的不拦(宁可放行, 后面还有溯源闸门)

items, n_stale, n_local, n_county, n_dupday = [], 0, 0, 0, 0
# 上限跟着路数走(7 路 × 每路最多 4 条 = 28); 原来写死 14, 扩路后会把后面几路整路砍掉
for it in (data.get("items") or [])[:len(TOPICS) * 4]:
    text = str(it.get("text") or "").strip()
    src = str(it.get("src") or "").strip()
    if len(text) < 40 or not src:      # 无出处一律丢弃(红线)
        continue
    # ⚠2026-07-29 崔伟纠偏: 旧闻不再整条丢弃 —— "事情什么时候发生"旧, 不等于"什么时候成为新闻"旧
    # (三家大行 5 年期大额存单发行于 7/1、7/8、7/10, 但 7-28/7-29 才被新浪财经/21世纪经济报道
    #  集中报道成热点, 正好是本号爆款命门题材)。改为标 stale: 可以进正文(必须写明发生日期),
    # 但不许占主打和标题位 —— 那才是 7-28 第1期被否的真正原因。
    # ⚠跨天去重(2026-08-27): 与最近 14 天报过的同一件事整条丢。
    #   但**必须放过变化点** —— "五年期大额存单利率1.60%"和"…降到1.55%"措辞几乎一样,
    #   后者恰恰是本号最该报的。剔掉日期后只要出现历史里没有的数字, 就判为新进展保留。
    _nm = _norm(text)
    _dup = None
    for _hd, _htp, _htxt, _hn in HIST_ITEMS:
        if _sim(_nm, _hn) >= SIM_DUP and not _new_numbers(text, _htxt):
            _dup = _hd
            break
    if _dup:
        print(f"⚠ 跨天重复丢弃(与 {_dup} 报过的是同一件事, 且没有新数字): {text[:40]}…",
              file=sys.stderr)
        n_dupday += 1
        continue
    old, when = stale_dates(text)
    if old:
        print(f"ⓘ 旧闻标记(正文里最早日期 {when}, 已过 {STALE_DAYS} 天, 只进正文不做主打): {text[:40]}…",
              file=sys.stderr)
        n_stale += 1
    m = _PERSON_RE.search(text)
    if m:
        # 只切掉含人名的那一句, 保住同条里的政策与数据(整条丢会连"基础养老金涨到163元"一起损失)
        sents = [x for x in re.split(r"(?<=[。！？；;])", text) if x.strip()]
        keep = [x for x in sents if not _PERSON_RE.search(x)]
        cut = "".join(keep).strip()
        print(f"⚠ 剔除个人案例句(疑似自媒体虚构人物「{m.group(0)}」)，保留其余政策内容", file=sys.stderr)
        if len(cut) < 40:      # 删完剩不下什么, 整条放弃
            continue
        text = cut
    # src 要的是"谁发布的", 不是文章标题 —— 千问偶尔把整篇标题塞进来
    # (如《2026年7月，中国大额存单最新调整：全新存款利率利息》), 渲染出来一长条很难看
    src = re.sub(r"[《》]", "", src)
    src = re.split(r"[:：]", src)[0].strip(" ，,、")
    if len(src) > 16:
        src = src[:16] + "…"
    scope, where = scope_of(src, text)
    if scope is None:
        print(f"⚠ 丢弃区县级消息(「{where}」, 全国号读者用不上): {text[:40]}…", file=sys.stderr)
        n_county += 1
        continue
    is_local = (scope == "地方")
    if is_local:
        print(f"ⓘ 地方性标记(据{src}, 只进正文不做主打): {text[:40]}…", file=sys.stderr)
        n_local += 1
    items.append({
        "local": is_local,           # 省市级消息: 可进正文(写成"多地已在推"), 不做主打/标题
        "local_where": src if is_local else "",
        "text": text,
        "src": src or "网络来源",
        "date": str(it.get("date") or "").strip()[:10],
        "official": bool(it.get("official")),
        "stale": bool(old),          # 事情发生在 STALE_DAYS 之前 → 只进正文, 不做主打/标题
        "stale_when": when,
        "topic": it.get("topic") or "",   # 供下一期按路做排除清单(exclude_for)
    })

# ---------- 补充源: 财新主要新闻(akshare, 免费) ----------
# 联网检索之外再挂一个真实信源。命中率不高(实测 2/100), 但捞到的往往是别处没有的,
# 如 7-28 那条"以快乐养老为名非法集资244亿、36万人受损"——正是老年读者最该看的。
# 关键词跟着检索路一起扩(2026-07-30): 原来只认养老/存款那几个词, 财新里的个人养老金、惠民保、
# 适老化、养老服务补贴这类照样落在两档里, 白白漏掉。
_CX_KW = re.compile(r"养老|退休|社保|医保|老年|长护|存款利率|大额存单|储蓄国债|"
                    r"非法集资|养老诈骗|理财骗局|保健品|"
                    r"个人养老金|惠民保|税优健康险|适老化|助餐|老年食堂|高龄津贴|加装电梯|"
                    r"挂牌利率|银行理财|集采|药品目录|报销比例")
try:
    import akshare as ak
    cx = ak.stock_news_main_cx()
    got = 0
    for _, r in cx.iterrows():
        txt = str(r.get("summary") or "").strip()
        if len(txt) < 30 or not _CX_KW.search(txt):
            continue
        nm = _norm(txt)
        if any(_sim(nm, s) >= SIM_DUP for s in seen_head):
            continue
        # ⚠财新这一条从 7-30 到 8-24 连续 25 天返回的是同一条(跨天相似度恒为 1.00):
        #   seen_head 只在单次运行内有效, 而 stock_news_main_cx() 里那条老新闻一直排在前面,
        #   于是每天都被重新捞回来白占一个名额。跨天历史一并去重。
        if any(_sim(nm, hn) >= SIM_DUP for _hd, _htp, _htxt, hn in HIST_ITEMS):
            print(f"ⓘ 财新跨天重复跳过: {txt[:40]}…")
            continue
        seen_head.add(nm)
        items.append({"text": txt, "src": "财新", "date": "", "official": False,
                      "topic": "财新"})
        got += 1
        if got >= 3:
            break
    print(f"财新补充 {got} 条")
except Exception as e:
    print(f"⚠ 财新源取数失败(不阻塞): {type(e).__name__}: {str(e)[:60]}", file=sys.stderr)

json.dump({"fetched_date": TODAY_ISO, "fetched_at": bj_now.strftime("%Y-%m-%d %H:%M:%S"),
           "items": items}, open(OUT, "w"), ensure_ascii=False, indent=2)

# 追加进 14 天历史(供次日起做程序化去重和每路排除清单)。⚠重新读一次盘, 不用内存里那份 ——
# 同一天两条管线各跑一次时, 后跑的这次要能看见先跑那次已经写进去的内容。
try:
    _all = json.load(open(HIST)).get("days", [])
except Exception:
    _all = []
_all = [d for d in _all if d.get("date") != TODAY_ISO]
_all.append({"date": TODAY_ISO,
             "items": [{"topic": it.get("topic", ""), "text": it["text"][:200]} for it in items]})
_all = _all[-HIST_DAYS:]
json.dump({"days": _all}, open(HIST, "w"), ensure_ascii=False, indent=2)
print(f"已写 {HIST}：{len(_all)} 天 / {sum(len(d['items']) for d in _all)} 条")

n_off = sum(1 for it in items if it["official"])
print(f"已写 {OUT}：{len(items)} 条(官方 {n_off} / 非官方 {len(items) - n_off}"
      f"{f', 旧闻标记 {n_stale}' if n_stale else ''}"
      f"{f', 地方性标记 {n_local}' if n_local else ''}"
      f"{f', 已丢区县级 {n_county}' if n_county else ''}"
      f"{f', 已丢跨天重复 {n_dupday}' if n_dupday else ''})")
for it in items:
    print(f"  [{'官方' if it['official'] else '非官方'}"
          f"{'·旧闻' + it.get('stale_when', '') if it.get('stale') else ''}"
          f"{'·地方' if it.get('local') else ''}] "
          f"{it['src']}：{it['text'][:45]}…")
