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
import os, json, re, sys, urllib.request, time as _t
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

# ⚠2026-08-07 养老日报恢复后一天会有两条管线跑到这里(养老日报 12:00 / 财经日报 14:00)。
# 若当天已经检索过(12:00 那次会 commit 留底), 后一次直接复用, 不再重新联网检索 ——
# 否则 14:00 那次会把 12:00 的结果当"上一期已用过"的排除清单, 财经日报养老档当天素材凭空清零
# (与 7-29「跑了没发要清空排除清单」是同一族坑)。两条管线谁先跑到都成立, 顺序无所谓。
try:
    _prev_doc = json.load(open(OUT))
except Exception:
    _prev_doc = {}
if _prev_doc.get("fetched_date") == TODAY_ISO and _prev_doc.get("items"):
    print(f"ⓘ 今天({TODAY_ISO})已检索过({len(_prev_doc['items'])} 条, 另一条管线留底), 直接复用不重搜")
    sys.exit(0)

# 上一期的检索结果当排除清单, 防止同一件事天天重复上报。
# ⚠**按路隔离**(2026-07-30 扩到 7 路时改): 原来把全部历史条目一股脑塞进每一路的检索提示词,
#   路数一多就是二十几条摘要压在检索意图前面, 会稀释检索词(与"塞 JSON schema 污染检索"同一个坑);
#   而且"存款利率"的历史条目对"防骗提醒"那一路毫无意义。改成只给同一路自己的历史, 每路最多 6 条。
prev_by_topic = {}
try:
    for _it in json.load(open(OUT)).get("items", []):
        prev_by_topic.setdefault(_it.get("topic") or "", []).append(str(_it.get("text") or "")[:60])
except Exception:
    pass

def exclude_for(tname):
    ps = prev_by_topic.get(tname) or []
    if not ps:
        return ""
    return "\n\n以下是上一期日报这一类里已经用过的内容, 不要重复(除非今天有新进展):\n" + \
           "\n".join(f"- {p}…" for p in ps[:6])


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
TOPICS = [
    ("养老金", "①{year}年基本养老金调整的最新进展(通知发布了没有、各省落地到哪一步、有没有官方辟谣);\n"
             "②城乡居民基础养老金标准调整; ③养老金资格认证; ④延迟退休政策落地情况。"),
    ("银行存款", "①银行存款挂牌利率调整(哪些银行、降了多少个基点、几年期);\n"
               "②大额存单的发行、重启、停售与利率变化; ③特色存款/智能存款/通知存款的新规或下架。\n"
               "都要跟普通储户手里的钱直接相关, 优先国有大行和主要股份行。"),
    ("国债理财", "①储蓄国债(电子式/凭证式)的发行安排、票面利率、什么时候能买;\n"
               "②银行理财产品的收益变化、破净或提前终止; ③货币基金/余额宝类收益率变化;\n"
               "④LPR 与市场利率变动对老百姓存钱的影响。"),
    ("医保待遇", "①医保报销政策变化、门诊和住院待遇调整、报销比例与起付线;\n"
               "②医保个人账户/家庭共济/异地就医结算的新规; ③药品集采落地、进口药与创新药进医保、药价变化;\n"
               "④居民医保缴费标准与参保政策。"),
    ("养老服务", "①长期护理保险试点进展与待遇标准; ②养老服务补贴、高龄津贴、养老金以外的老年补助;\n"
               "③社区助餐/老年食堂/居家养老上门服务; ④适老化改造、老旧小区加装电梯;\n"
               "⑤养老院床位与收费、公办养老机构轮候。"),
    ("个人养老金", "①个人养老金制度的最新政策(账户开立、缴存上限、税收优惠、领取规则);\n"
                "②个人养老金可投产品的变化(储蓄、理财、基金、商业养老保险);\n"
                "③专属商业养老保险、税优健康险的进展; ④各地惠民保的参保、报销与调整。"),
    ("防骗提醒", "针对老年人的养老诈骗、非法集资、理财骗局的官方提示或已查处的典型案例"
              "(要有办案机关或官方媒体出处, 讲清套路和涉案金额)。"),
]

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

with ThreadPoolExecutor(max_workers=4) as _ex:
    results = list(_ex.map(run_topic, TOPICS))

raw_items, seen_head = [], set()
for tname, d, logs in results:
    print(f"—— 检索【{tname}】")
    for line in logs:
        print(line if not line.startswith("!!") else line, file=sys.stderr if line.startswith("!!") else sys.stdout)
    if not d:
        continue
    got = 0
    for it in (d.get("items") or []):
        head = str(it.get("text") or "")[:24]
        if head and head not in seen_head:      # 跨主题去重(同一件事可能被两路都搜到)
            seen_head.add(head); it["topic"] = tname; raw_items.append(it); got += 1
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

items, n_stale, n_local, n_county = [], 0, 0, 0
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
        if txt[:24] in seen_head:
            continue
        seen_head.add(txt[:24])
        items.append({"text": txt, "src": "财新", "date": "", "official": False})
        got += 1
        if got >= 3:
            break
    print(f"财新补充 {got} 条")
except Exception as e:
    print(f"⚠ 财新源取数失败(不阻塞): {type(e).__name__}: {str(e)[:60]}", file=sys.stderr)

json.dump({"fetched_date": TODAY_ISO, "items": items}, open(OUT, "w"), ensure_ascii=False, indent=2)
n_off = sum(1 for it in items if it["official"])
print(f"已写 {OUT}：{len(items)} 条(官方 {n_off} / 非官方 {len(items) - n_off}"
      f"{f', 旧闻标记 {n_stale}' if n_stale else ''}"
      f"{f', 地方性标记 {n_local}' if n_local else ''}"
      f"{f', 已丢区县级 {n_county}' if n_county else ''})")
for it in items:
    print(f"  [{'官方' if it['official'] else '非官方'}"
          f"{'·旧闻' + it.get('stale_when', '') if it.get('stale') else ''}"
          f"{'·地方' if it.get('local') else ''}] "
          f"{it['src']}：{it['text'][:45]}…")
