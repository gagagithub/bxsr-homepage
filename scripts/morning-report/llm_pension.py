# -*- coding: utf-8 -*-
"""养老日报(发「崔伟说养老」)·独立管线 AI 整理: pension_news.json → pension_sections.json。

2026-08-07 崔伟拍板恢复养老日报, 并拆成独立管线每天中午 12:00 跑(财经日报维持 14:00 不动)。
旧做法(gen_pension)复用当天财经日报养老档的成稿 —— 12:00 独立跑时那份数据还不存在,
所以这里直接拿 fetch_pension_news.py 的联网检索素材(7 路千问 + 财新)自己整理:
  ① 条目口语化改写(label + text, 关键数字 <b> 标红), 全部归入「养老」主题
  ② 公众号标题 + 导语 + 整段解读(只谈退休金/存款国债两类)
  ③ 健康小课堂(与财经日报同一主题池按日轮转, 同一天两个号讲同一课; 没新闻时的全篇托底)
输出 pension_sections.json, 结构与 sections.json 同形(themes/tip/pension 三块),
render_pension.py 通过环境变量 PENSION_SECTIONS_JSON 读它, 渲染端零改动。

⚠内容校验只报警不拦截(崔伟 2026-08-05 拍板"你不要加阀门了, 我会自己检查的"):
  只查数字溯源, 只打「请人工核对」日志, 不重试、不丢弃、不回退 ——
  公众号是草稿制, 群发前必经崔伟人手。
⚠标题不设任何闸门(崔伟 2026-08-07 "标题也不要加门阀, 就像崔伟说投资一样"):
  提示词里只有正向的选题优先级和句式, 没有"不许拿XX做标题"的禁令;
  素材标注也不再写「只进正文不做标题」; 非官方/旧闻/地方性三条标题报警一并去掉。
  与 llm_morning(财经日报)完全对等。
2026-08-07 加料(崔伟"内容太少"): 条目 80-160 字→150-250 字, 每条多一句「这对您意味着什么」
  (means 字段, render_pension 渲染成 👉 那一行); 素材源同日由 7 路扩到 11 路。
"""
import os
BASE = os.path.dirname(os.path.abspath(__file__))
import json, re, sys, urllib.request, difflib
import datetime as _dt
from mr_common import (TIP_TOPICS, TIP_GUARD, trusted_src, _tokens, _unsourced)

YML = os.environ.get("BAOXIN_DEV_YML", os.path.join(BASE, "..", "..", "service", "src", "main", "resources", "application-dev.yml"))
def get_key():
    try:
        txt = open(YML, encoding="utf-8", errors="ignore").read()
        m = re.search(r"apiKey:\s*(sk-[A-Za-z0-9_\-]+)", txt)
        return m.group(1) if m else None
    except Exception:
        return None

key = os.environ.get("DEEPSEEK_API_KEY") or get_key()
if not key:
    print("!! 未找到 DeepSeek key", file=sys.stderr); sys.exit(1)

OUT = f"{BASE}/pension_sections.json"
_bj_today = _dt.datetime.utcnow() + _dt.timedelta(hours=8)
TIP_TOPIC = TIP_TOPICS[_bj_today.toordinal() % len(TIP_TOPICS)]
print(f"今日健康小课堂主题：{TIP_TOPIC}")

# ---------- 素材: fetch_pension_news.py 的联网检索结果 ----------
try:
    _pn = json.load(open(f"{BASE}/pension_news.json", encoding="utf-8")).get("items", [])
except Exception:
    _pn = []

# 与 llm_morning 同一套标注前缀(官方/可信媒体/非官方/旧闻/地方), 喂给 AI 时写进正文
indexed, META = [], {}
for it in _pn:
    t = str(it.get("text") or "").strip()
    if len(t) < 40:
        continue
    n = len(indexed) + 1
    src = str(it.get("src") or "").strip() or "网络来源"
    off = bool(it.get("official"))
    trusted = trusted_src(src)
    if off:
        tag = "【官方发布】"
    elif trusted:
        tag = "【媒体报道，可信财经媒体】"
    else:
        tag = "【非官方，仅为他人说法】"
    # ⚠2026-08-07 崔伟: "标题也不要加门阀, 就像崔伟说投资一样" —— 原来这两个标注里带
    # 「只进正文不做标题」的硬指令, 等于给 AI 设了标题闸门(7-29 那次连着几天回退成日期标题就是它);
    # 财经日报那边从来没有这类禁令, 只有正向的选题优先级。这里改成纯事实标注: 只要求写清日期/省市,
    # 挑哪条做标题交给 AI 判断, 出了偏差由崔伟核稿时拿掉。
    stale = bool(it.get("stale"))
    if stale:
        tag += f"【发生于{it.get('stale_when') or '数日前'}，正文里要写清事情是哪天发生的】"
    local = bool(it.get("local"))
    if local:
        tag += "【地方性消息，正文必须写清是哪个省市】"
    indexed.append({"i": n, "t": f"{tag}（据{src}）{t}"[:460]})
    META[n] = {"src": src, "official": off or trusted, "stale": stale, "local": local, "raw": t}
print(f"喂给 AI 素材 {len(indexed)} 条"
      f"(官方/可信媒体 {sum(1 for v in META.values() if v['official'])} / "
      f"非官方 {sum(1 for v in META.values() if not v['official'])})")

# ---------- 标题的跨天记忆(2026-08-27 崔伟: "养老题材的, 并且每日标题最好能不一样") ----------
# ⚠原来 llm_pension **从不读昨天的标题**, DeepSeek 每天从零开始挑, 根本不知道自己昨天写过什么;
#   而提示词里那句「选题优先级(这几类永远优先做主打): ①存款 ②养老金…」是**固定排序**,
#   与财经日报同一句写法 —— 但投资号排前面的①②④⑤(汇率/楼市/名人)天天有新料,
#   养老号的①②(存款利率/养老金调整)月度级才动一次, 于是"永远优先"变成同一件事连讲一周:
#   7-30~8-26 这 20 篇里, 城乡居民养老金 163 元讲了 11 次、大额存单讲了 5 次, 只剩 4 篇是别的。
# 改法(正向引导, **不拦截不重试不回退**, 与 8-05/8-07 定的"不加门阀"口径一致):
#   ① 把最近 21 天的标题连同题材分类喂进提示词, 让它知道自己最近写过什么;
#   ② 把固定排序换成**题材轮转** —— 程序算出"最近没做过的养老题材", 提示词里明写优先从这些里挑;
#   ③ 判据第一条仍是"必须是养老题材"(崔伟原则), 题材池整个都是退休老人的事, 轮转不会跑题;
#   ④ 生成后若仍与近期标题高度相似, **只打日志报警**供崔伟核稿, 不改不丢。
THIST = f"{BASE}/pension_title_history.json"
THIST_DAYS = 21

# 题材池 —— **整池都是养老题材**, 所以"轮转"只会换角度, 不会跑到非养老的题材上去。
# 顺序即分类优先级(强信号在前): 先判防骗和社保基金, 免得"养老诈骗""社保基金收益"被吞进养老金调整。
TITLE_CLASSES = [
    ("防骗提醒",   r"诈骗|骗局|被骗|骗取|非法集资|集资|冒充|传销|套路贷|高息返利"),
    ("社保基金",   r"社保基金|基金结余|累计结余|投资收益|收益率"),
    ("存款利率",   r"存单|存款|定存|挂牌利率|存款利率|通知存款"),
    ("国债理财",   r"国债|理财|货币基金|余额宝|LPR|预定利率"),
    ("个人养老金", r"个人养老金|抵税|税优|惠民保|专属商业养老"),
    # ⚠更具体的排在更宽的前面: "异地就医备案"里带"医保"二字, 放在医保报销后面会被它先吃掉
    ("异地就医",   r"异地就医|异地|备案|跨省|转移接续"),
    ("医保报销",   r"医保|报销|起付线|门诊|住院|集采|药价|药品目录|缴费标准"),
    ("常用药体检", r"用药|处方|体检|筛查|疫苗|接种|慢病|高血压|糖尿病|家庭医生"),
    ("养老服务",   r"长护|护理|养老院|助餐|老年食堂|适老化|加装电梯|床位|居家养老"),
    ("补贴优待",   r"高龄津贴|津贴|补贴|敬老卡|老年证|免费乘车|优待|免费体检"),
    ("遗属继承",   r"遗属|丧葬|抚恤|继承|遗嘱|遗产|过户|赡养|公证"),
    ("物价开销",   r"CPI|物价|菜价|肉价|水电|燃气|供暖|票价|涨价|资费"),
    ("退休生活",   r"老年大学|返聘|超龄|再就业|旅居|银发|老年旅游"),
    ("手机办事",   r"电子社保卡|医保码|医保电子凭证|一网通办|大字版|智能手机|线上办"),
    ("养老金调整", r"养老金|退休金|基础养老金|待遇调整|补发|资格认证|延迟退休|工龄|缴费年限"),
]

def classify_title(t):
    for name, pat in TITLE_CLASSES:
        if re.search(pat, t or ""):
            return name
    return "其他"

try:
    _th = json.load(open(THIST, encoding="utf-8")).get("days", [])
except Exception:
    _th = []
_today_iso = _bj_today.strftime("%Y-%m-%d")
_th = [d for d in _th if d.get("date") != _today_iso][-THIST_DAYS:]

_used = {}                       # 题材 → 最近一次用它做标题的日期
for _d in _th:
    _used[_d.get("cls") or "其他"] = _d.get("date", "")
_cold = [n for n, _ in TITLE_CLASSES if n not in _used]
_recent_lines = "\n".join(
    f"- {d.get('date','')[5:]}【{d.get('cls','其他')}】{str(d.get('title','')).replace(chr(34), chr(8220))}"
    for d in reversed(_th[-14:]))

if _th:
    ROT_HINT = (
        "\n\n【最近两周本号已经做过的标题(新→旧), 今天不要再做同一件事】\n" + _recent_lines
        + "\n\n【今天优先从这些「最近没做过」的养老题材里挑】" + ("、".join(_cold) if _cold else
          "(最近两周把题材池轮了一遍, 那就挑其中间隔最久、且今天素材里确实有新料的那一类)")
        + "\n⚠这是**优先**不是**只能**: 如果今天某件事确实出了实质性新进展(通知正式下发、"
          "利率又变了、标准又调了), 哪怕最近做过, 照样可以做 —— 但标题必须说清「新」在哪, "
          "不许把上次那条换个措辞再发一遍。")
    print(f"标题跨天记忆: {len(_th)} 天; 最近做过 {sorted(_used)}; 冷题材 {_cold}")
else:
    ROT_HINT = ""
    print("标题跨天记忆: 无历史(首次运行)")

SYS = """你是「崔伟说养老」公众号的主笔，每天中午编一份《养老日报》。
读者是 50-70 岁、关心自己退休金和养老钱的普通人(不是炒股的)，六成多是女性，遍布全国。
他们最关心的只有几件事：养老金/退休金调没调、存款和大额存单利率又降没降、国债还买不买得到、
医保能报多少、养老服务多少钱、买菜交水电又贵了没有、有哪些补贴和优待自己没去领、
以及别让骗子把养老钱骗走。讲的都是"自己账本上的钱"。

【文风铁律：说人话，别播新闻联播】
- 像邻居里懂行的大姐/老哥跟你唠，通篇口语化短句，一句话尽量不超过30字。
- 禁用新闻通稿腔："据悉/日前/获悉/表示/指出/此举旨在"一律改成"说/提到/打算"或直接陈述。
- 术语顺手用大白话解释(如"挂牌利率，就是银行柜台公示的存款利率")。
- 数字是干货，一个都不能丢，但要放进顺口的句子里。

【铁律，违反作废】
1. 只用给定素材，绝对禁止新增素材里没有的数字、政策、机构表态；数字必须原样保留。
   ⚠**专有名词是重灾区**：药品名、产品名、银行名、机构名、平台名，素材里没写的一个都不许添
   （2026-08-07 实测：素材只给了两个药名，AI 自己又补了第三个；也自己列出了"工农中建"）。
   要举例子就用素材里出现过的那几个，凑不满就少写几个，别补。
   素材里已有的数字做简单加总或折算（如 135亿+165亿=300亿、每月20元×6个月=120元）是允许的，
   但必须让读者看得出这笔账是怎么来的。
   (唯一例外：健康小课堂字段允许用公认医学/医保常识，另有护栏。)
2. 标了【非官方】的，正文必须写明是谁说的，绝不能写成板上钉钉的结论；
   今年养老金调不调、几月调、调多少，人社部正式公布前一律只能转述，不许下结论。
3. 标了【发生于X日】的旧闻，正文必须写清事情发生的日期。
4. 标了【地方性消息】的，正文必须写清是哪个省市，别让外省读者误以为是全国政策。
5. 输出必须是合法 JSON，不要 markdown 代码块，不要多余文字。"""

def _call_once(user):
    payload = json.dumps({
        "model": "deepseek-v4-pro",
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
        # 2026-08-07 条目由 80-160 字放宽到 150-250 字并多了 means 字段, 12 条能顶到 6000+ token,
        # 8000 会把最后几条截在半截(虽有 JSON 修补兜底, 但那是丢内容不是省钱) → 提到 12000。
        "temperature": 0.3, "max_tokens": 12000,
        "response_format": {"type": "json_object"},
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
    last = None
    for k in range(tries):
        try:
            return _call_once(user)
        except Exception as e:
            last = e
            print(f"!! DeepSeek 第{k+1}次失败({type(e).__name__}: {str(e)[:80]})，重试", file=sys.stderr)
            import time as _t; _t.sleep(2)
    raise last

# ---------- 调用①: 条目改写 + 标题/导语/解读 ----------
data = {"items": [], "title": "", "lead": "", "insight": "", "ti": None}
if indexed:
    user = (
        "下面是今天为《养老日报》联网检索到的素材(JSON 数组, i 为编号):\n"
        + json.dumps(indexed, ensure_ascii=False)
        + "\n\n请整理成今天的养老日报，严格输出如下 JSON：\n"
          '{\n'
          '  "items": [{"i": 素材编号, "label": "机构/主体(2-8字, 如 人社部/工商银行/国家医保局)", '
          '"text": "大白话把这条讲清楚, 3-5句、150-250字, 素材里的关键数字/比例/时间全部保留, '
          '关键数字用<b>包起来</b>(前端标红)。别只复述一句就完了 —— 把是什么、多少钱、从什么时候算、'
          '谁能摊上, 一次讲全。带【非官方】【发生于X日】【地方性】标注的, 按系统铁律写明出处/日期/省市", '
          '"means": "另起一句 30-60字的「这对您意味着什么」, 单独放在正文后面, 直接对读者说话。'
          '要落到一个具体动作或一句踏实话: 该去哪办、该现在办还是不用急、该留意什么、'
          '或者明确说一句这事跟大多数人没关系不用管。'
          '⚠⚠ 绝对禁止催促购买和制造稀缺: 不许写「早买/抓紧买/能买就买/抢/额度有限/晚了就没了/'
          '去晚了卖完了/售罄/名额不多/值得配置/可以考虑买」这类话, 国债、存款、大额存单、保险一律不例外 '
          '—— 我们是讲清楚, 不是带货。也不许用「保本保息/稳赚/一分不少」这类承诺性说法给任何产品背书。'
          '涉及买什么的, 只写清楚"在哪买、怎么买、有什么限制", 买不买是读者自己的事。'
          '⚠尤其不许对惠民保、商业保险、理财、基金这类第三方产品写「可以了解一下/保费不贵/'
          '值得看看/建议配置」—— 我们是持牌机构的号, 这等于在推销别人的产品, 一个字都不能沾。'
          '也不许写「有闲钱就存银行/就买国债」这种替读者做资金安排的话。'
          '实在没什么可说的就留空字符串"}],\n'
          '  "title": "公众号标题, 18-28字, 这是全篇最重要的一个字段, 决定有没有人点开。'
          '第一步: 从今天的素材里挑一条做主打。判据只有两条, 缺一不可: '
          '⚠**第一条·必须是养老题材**: 这条要能落到一个退休老人自己的账本或日子上 —— '
          '退休金和社保待遇、看病吃药能省多少、存的那点钱和国债理财、每月买菜交费的开销、'
          '能领却没人告诉你的补贴优待、身后事和继承赡养、退休后的日子怎么过、手机上怎么办事、'
          '以及别让骗子把养老钱骗走。落不到具体某个老人身上的东西(行业总规模数字、公司资本运作、'
          '股市大盘涨跌)一律不做标题。'
          '⚠**第二条·今天的标题不要和最近两周重样**: 见下面给出的近期标题清单。'
          '同一件事换个措辞再发一遍, 对天天看的读者就是没有新内容 —— 这是本号最需要改掉的毛病。'
          '第二步: 套这几个句式之一做成标题(方括号处必须填今天素材里的真实内容, 句式只是壳): '
          '①身份代入『手里有[金额/什么]的注意, [什么]变了』②悬念设问『[机构]刚[动作], 咱的养老钱该[怎么办]吗?』'
          '③政策+切身利害『[政策变化], 以后[领钱/看病]能[具体变化]』④提醒『[骗局/事件], 给咱提了个醒』。'
          '硬要求: 标题里每一个数字、机构名、事件都必须出自你挑的那条素材原文, 一个字都不许编; '
          '身份代入部分必须跟这条内容真实相关(讲国债就写"想买国债的", 讲医保就写"常吃药的", 别硬套); '
          '口语化像邻居大姐转发时会说的话; 禁止"震惊/速看/必看"式恶俗词; 禁止收益暗示; 不带日期",\n'
          '  "ti": 你做标题所依据的那条素材编号(整数),\n'
          '  "lead": "导语 150-220字, 承接标题那条讲透: 先一句把标题的钩子接住, 再把关键数字讲清, '
          '最后一句落到「这跟您的养老钱有什么关系」。大白话短句, 关键数字用<b>标出</b>。⚠只许用素材里已有的数字",\n'
          '  "insight": "一段 180-260字的整体解读, 放在正文末尾。⚠铁律: 只许谈你上面 items 里真的写了的事, '
          '一件正文里没有的都不许提 —— 读者会发现在聊自己没读到的新闻。可谈的范围就是今天素材里有的那几类: '
          '养老金/退休待遇/社保、存款利率/大额存单/国债、医保报销、物价水电、能领的补贴、防骗、'
          '常用药与体检、遗嘱继承与赡养、退休后的日子(老年大学/返聘/旅居)、手机办事(医保码/电子社保卡)。'
          '绝对不许提楼市房价、黄金、基金、股市、汇率。素材少就只就手头有的展开, 宁短勿凑。'
          '口吻是跟老朋友唠, 落点是「咱这个岁数, 这笔钱该怎么打算」, 中性不荐产品不承诺收益"\n'
          '}\n\n'
          "要求:\n"
          "- items 按对读者账本的重要性排序, 逐条改写, 贴题的都要(最多 16 条); "
          "确实跟养老钱/看病钱无关的素材可以丢弃。\n"
          "- 做标题那条素材必须同时出现在 items 里且尽量排最前。\n"
          "- 同一件事只写一条, 不许拆成几条。"
          # ⚠轮转清单放在整段提示词的**最末尾**(JSON schema 之外): 它是多行块, 塞进
          #   "title" 字段的描述里会让模型分不清 schema 到哪儿结束, 影响输出合法 JSON。
          + ROT_HINT
    )
    try:
        d = call(user)
        data["items"] = [x for x in (d.get("items") or [])
                         if isinstance(x, dict) and str(x.get("text", "")).strip()]
        data["title"] = re.sub(r"<[^>]+>", "", str(d.get("title", ""))).strip()
        data["lead"] = str(d.get("lead", "")).strip()
        data["insight"] = str(d.get("insight", "")).strip()
        data["ti"] = d.get("ti")
    except Exception as e:
        print(f"⚠ 条目/标题生成失败: {e}", file=sys.stderr)
else:
    print("⚠ 今天没有养老素材(检索失败或全被过滤), 正文将只有健康小课堂托底", file=sys.stderr)

# ---------- 标题重样自检 + 写跨天记忆(⚠只报警不拦截) ----------
# 崔伟"每日标题最好能不一样"是**尽量**, 不是硬指标: 真出了新进展就该再讲一次。
# 所以这里只把判断结果打进日志供核稿, 绝不改标题、不重试、不回退
# (与 [[feedback_no_auto_content_gates]] 8-05 口径一致)。
if data["title"]:
    _cls = classify_title(data["title"])
    _nt = re.sub(r"[^\u4e00-\u9fa5\d%]", "", data["title"])
    _near = []
    for _d in _th:
        _r = difflib.SequenceMatcher(
            None, _nt, re.sub(r"[^\u4e00-\u9fa5\d%]", "", str(_d.get("title") or ""))).ratio()
        if _r >= 0.55:
            _near.append((_r, _d.get("date", ""), _d.get("title", "")))
    _near.sort(reverse=True)
    if _near:
        print(f"⚠ [仅报警不拦截] 今天的标题与 {_near[0][1]} 那篇相似度 {_near[0][0]:.2f}, "
              f"发布前请人工确认是不是同一件事:\n"
              f"    今天: {data['title']}\n"
              f"    {_near[0][1]}: {_near[0][2]}", file=sys.stderr)
    elif _cls in _used:
        print(f"ⓘ 今天标题题材【{_cls}】上次用于 {_used[_cls]}(措辞不重样, 供参考)")
    else:
        print(f"✓ 今天标题题材【{_cls}】最近 {len(_th)} 天没做过")
    _th.append({"date": _today_iso, "title": data["title"], "cls": _cls})
    json.dump({"days": _th[-THIST_DAYS:]}, open(THIST, "w"), ensure_ascii=False, indent=2)
    print(f"已写 {THIST}：{len(_th[-THIST_DAYS:])} 天")

# ---------- 校验(⚠只报警不拦截, 崔伟 2026-08-05 拍板; 发布前人工核对) ----------
ALL_TOKENS = _tokens(" ".join(v["raw"] for v in META.values()))
def _warn(field, text, tokens=ALL_TOKENS):
    bad = _unsourced(text, tokens)
    if bad:
        print(f"⚠[仅报警不拦截] {field}疑似无源数字 {sorted(bad)[:5]}, 发布前请人工核对: "
              f"「{re.sub(r'<[^>]+>', '', str(text))[:50]}」")

# ⚠2026-08-07 崔伟"标题也不要加门阀, 就像崔伟说投资一样": 原来这里还按 非官方/旧闻/地方性
# 三个维度对标题各报一次警, 与财经日报不对等(那边只报无源数字)。三条一并去掉, 只留数字溯源这一条 ——
# 数字编错是事实性错误, 挑哪条做标题是编辑判断, 后者归崔伟。
if data["title"]:
    ti = data.get("ti")
    m = META.get(ti) if isinstance(ti, int) else None
    one = m["raw"] if m else " ".join(v["raw"] for v in META.values())
    _warn("标题", data["title"], _tokens(one))
_warn("导语", data["lead"])
_warn("解读", data["insight"])

# ---------- 调用②: 健康小课堂(唯一允许用素材之外知识的字段; 没新闻时的托底) ----------
tip = {}
try:
    d = call("请单独产出今天的「健康小课堂」，严格输出如下 JSON：\n"
             '{"tip": {"title": "≤18字的大白话标题(可以设问、可以点破一个常见误区，别标题党)", '
             '"body": "250-330字，围绕今天指定的主题把【一个知识点】讲透：先点破一个大家普遍搞错或忽视的地方，'
             '再用大白话(可以打生活比方)讲清楚道理，最后给一条今天就能照着做的具体建议。分成3-5句，'
             '关键结论/公认标准数字用<b>标出</b>"}}\n\n'
             f"今天的主题是【{TIP_TOPIC}】。" + TIP_GUARD)
    tip = d.get("tip", {}) or {}
    if isinstance(tip, str):
        tip = {"body": tip}
    tip["topic"] = TIP_TOPIC
except Exception as e:
    print(f"⚠ 健康小课堂生成失败(不阻塞): {e}", file=sys.stderr)
    tip = {}

# ---------- 映射成 sections.json 同形结构, render_pension.py 零改动直接吃 ----------
items_out = []
for x in data["items"]:
    i = x.get("i")
    m = META.get(i) if isinstance(i, int) else None
    src = (m["src"] + ("" if m["official"] else "，非官方")) if m else ""
    items_out.append({"label": re.sub(r"<[^>]+>", "", str(x.get("label", ""))).strip(),
                      "text": str(x.get("text", "")).strip(),
                      "means": str(x.get("means") or "").strip(),
                      "src": src})

out = {
    "themes": [
        # 全部素材进「养老」档, render_pension 自己按 退休金/存款国债 分档、医保类溢出到健康小课堂
        {"name": "养老", "icon": "🌅", "items": items_out, "insight": ""},
        {"name": "健康", "icon": "🏥", "items": [], "insight": ""},
    ],
    "tip": tip if tip.get("body") else {},
    "pension": {"title": data["title"], "lead": data["lead"], "insight": data["insight"]},
}
json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=2)
print(f"AI 整理完成 {os.path.basename(OUT)}：条目 {len(items_out)} 条 / "
      f"健康小课堂 {'有' if out['tip'] else '⚠无'} / "
      f"标题「{data['title'] or '⚠无(回退日期标题)'}」")
