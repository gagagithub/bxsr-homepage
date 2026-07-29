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
import os, json, re, sys, urllib.request
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

# 昨天的检索结果当排除清单, 防止同一件事天天重复上报
prev = []
try:
    prev = [it["text"][:60] for it in json.load(open(OUT)).get("items", [])]
except Exception:
    pass
EXCLUDE = ""
if prev:
    EXCLUDE = "\n\n以下是昨天日报已经用过的内容, 不要重复(除非今天有新进展):\n" + \
              "\n".join(f"- {p}…" for p in prev)


# ---------- 多路专项检索(崔伟 7-28: "扩大线索源") ----------
# 原来一次问 5 大类, qwen 每类只摊得到一两条; 拆成各问各的, 每路 2-4 条, 总量翻几倍,
# 且都限定"今天或最近1-2天" —— 日报就该是当天的, 靠放宽天数凑数会变成旧闻。
TOPICS = [
    ("养老金", "①{year}年基本养老金调整的最新进展(通知发布了没有、各省落地到哪一步、有没有官方辟谣);\n"
             "②城乡居民基础养老金标准调整; ③养老金资格认证; ④延迟退休政策落地情况。"),
    ("存款理财", "①银行存款挂牌利率调整(哪些银行、降了多少个基点); ②大额存单发行与利率变化;\n"
               "③储蓄国债发行安排与票面利率; ④银行理财收益变化 —— 都要跟普通储户直接相关的。"),
    ("医保养老服务", "①医保报销政策变化、门诊住院待遇调整; ②医保个人账户/家庭共济/异地就医新规;\n"
                 "③长期护理保险试点进展; ④养老服务补贴、助餐、居家养老、适老化改造。"),
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
每条必须真实、有出处, 搜不到就明说。{exclude}"""

FORMAT_USER = """把下面这份检索结果整理成 JSON(不要多余文字、不要 markdown 代码块):
{"items": [
  {"text": "内容完整一段(100-180字), 只用检索结果里已有的事实和数字, 不做评论",
   "src": "⚠只填【发布方的名称】, 8字以内最好, 如 人社部 / 中国银行 / 国家医保局 / 江苏省检察院 / 财新。"
          "绝不要把文章标题填进来(如《2026年7月中国大额存单最新调整…》这种一长条); "
          "如果只查到文章、说不清发布方, 就填 网络文章",
   "date": "YYYY-MM-DD",
   "official": true 或 false}
]}
official 的判断: 部委、官方媒体、银行等主体【正式发布】的事实 = true;
财经媒体的分析解读、专家观点、自媒体说法、对未公布事项的推测 = false。
要求: 最多4条, 按对退休老人的实际影响排序; 说不清出处的一律不要;
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

def call_once(topic_q):
    found = call_qwen([
        {"role": "system", "content": "你是新闻检索助手, 只报告真实检索到的内容, 宁缺毋滥, 严禁编造。"},
        {"role": "user", "content": SEARCH_TMPL.format(today=TODAY, year=YEAR,
                                                       topic_q=topic_q, exclude=EXCLUDE)},
    ], enable_search=True)
    print(f"   第一步联网检索返回 {len(found)} 字")
    content = call_qwen([{"role": "user", "content": FORMAT_USER + found}], enable_search=False)
    content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
    if not content.startswith("{"):
        m = re.search(r"\{.*\}", content, flags=re.S)
        if m:
            content = m.group(0)
    return json.loads(content)

# 逐主题检索(某一路失败不影响其他路, 全空才 bail)
raw_items, seen_head = [], set()
for tname, tq in TOPICS:
    print(f"—— 检索【{tname}】")
    d = None
    for k in range(2):
        try:
            d = call_once(tq); break
        except Exception as e:
            print(f"!! 【{tname}】第{k+1}次失败({type(e).__name__}: {str(e)[:60]})", file=sys.stderr)
            import time as _t; _t.sleep(2)
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
    bail("四路联网检索均无结果")

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

def stale_dates(text):
    """返回 (是否旧闻, 正文里最早的那个日期字符串) —— 便于日志说清楚为什么丢。"""
    found = []
    today = datetime(bj_now.year, bj_now.month, bj_now.day)
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

items, n_stale = [], 0
for it in (data.get("items") or [])[:14]:
    text = str(it.get("text") or "").strip()
    src = str(it.get("src") or "").strip()
    if len(text) < 40 or not src:      # 无出处一律丢弃(红线)
        continue
    old, when = stale_dates(text)
    if old:
        print(f"⚠ 旧闻丢弃(正文里最早日期 {when}, 已过 {STALE_DAYS} 天): {text[:40]}…", file=sys.stderr)
        n_stale += 1
        continue
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
    items.append({
        "text": text,
        "src": src or "网络来源",
        "date": str(it.get("date") or "").strip()[:10],
        "official": bool(it.get("official")),
    })

# ---------- 补充源: 财新主要新闻(akshare, 免费) ----------
# 联网检索之外再挂一个真实信源。命中率不高(实测 2/100), 但捞到的往往是别处没有的,
# 如 7-28 那条"以快乐养老为名非法集资244亿、36万人受损"——正是老年读者最该看的。
_CX_KW = re.compile(r"养老|退休|社保|医保|老年|长护|存款利率|大额存单|储蓄国债|"
                    r"非法集资|养老诈骗|理财骗局|保健品")
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

json.dump({"items": items}, open(OUT, "w"), ensure_ascii=False, indent=2)
n_off = sum(1 for it in items if it["official"])
print(f"已写 {OUT}：{len(items)} 条(官方 {n_off} / 非官方 {len(items) - n_off}"
      f"{f', 旧闻丢弃 {n_stale} 条' if n_stale else ''})")
for it in items:
    print(f"  [{'官方' if it['official'] else '非官方'}] {it['src']}：{it['text'][:45]}…")
