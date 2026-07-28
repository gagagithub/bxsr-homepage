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

# ⚠两步走(实测教训, 见 fetch_health_news.py): 用户消息里塞 JSON schema 会污染联网检索词、
# 直接交白卷; 第一步自由文本搜, 第二步不联网纯整理成 JSON(不引入新信息)。
SEARCH_USER = f"""今天是{TODAY}(北京时间)。请联网搜索最近1-3天中国跟【退休老人的钱】直接相关的消息, 重点是这几类:
1. 养老金/退休金: {YEAR}年基本养老金调整的进展(通知发了没有、各省落地情况)、城乡居民基础养老金、
   养老金资格认证、延迟退休相关政策落地。
2. 存款和理财: 银行存款挂牌利率调整、大额存单发行与利率、国债(尤其储蓄国债)发行、
   银行理财收益变化——要跟普通储户直接相关的。
3. 医保与养老服务: 医保报销政策、门诊住院待遇、长期护理保险、养老服务补贴、老旧小区适老化改造。

对每一条, 必须给出: 内容(保留报道里的具体数字、金额、比例、时间)、是谁发布或是谁说的、日期。
⚠严格要求:
- 只报告真实检索到的内容, 一个字都不许编。搜不到就明说搜不到。
- 必须分清: 哪些是部委/官方媒体正式发布的, 哪些只是财经媒体分析、专家观点或自媒体说法。
- 说不出是谁说的、找不到出处的传言, 不要收录。
- 不要给出你自己的预测或判断。特别是{YEAR}年养老金调不调、几月调、调多少,
  在人社部正式公布前, 只能转述别人怎么说的, 不许自己下结论。
列出 4-6 条。{EXCLUDE}"""

FORMAT_USER = """把下面这份检索结果整理成 JSON(不要多余文字、不要 markdown 代码块):
{"items": [
  {"text": "内容完整一段(100-180字), 只用检索结果里已有的事实和数字, 不做评论",
   "src": "发布单位或说话人(如 人力资源社会保障部 / 某某银行 / 某财经媒体 / 某专家及其单位)",
   "date": "YYYY-MM-DD",
   "official": true 或 false}
]}
official 的判断: 部委、官方媒体、银行等主体【正式发布】的事实 = true;
财经媒体的分析解读、专家观点、自媒体说法、对未公布事项的推测 = false。
要求: 最多6条, 按对退休老人的实际影响排序; 说不清出处的一律不要;
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

def call_once():
    found = call_qwen([
        {"role": "system", "content": "你是新闻检索助手, 只报告真实检索到的内容, 宁缺毋滥, 严禁编造。"},
        {"role": "user", "content": SEARCH_USER},
    ], enable_search=True)
    print(f"—— 第一步联网检索返回 {len(found)} 字")
    content = call_qwen([{"role": "user", "content": FORMAT_USER + found}], enable_search=False)
    content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
    if not content.startswith("{"):
        m = re.search(r"\{.*\}", content, flags=re.S)
        if m:
            content = m.group(0)
    return json.loads(content)

data = None
for k in range(3):
    try:
        data = call_once()
        break
    except Exception as e:
        print(f"!! 千问检索第{k+1}次失败({type(e).__name__}: {str(e)[:80]}), 重试", file=sys.stderr)
        import time as _t; _t.sleep(3)
if data is None:
    bail("千问联网检索3次均失败")

items = []
for it in (data.get("items") or [])[:6]:
    text = str(it.get("text") or "").strip()
    src = str(it.get("src") or "").strip()
    if len(text) < 40 or not src:      # 无出处一律丢弃(红线)
        continue
    items.append({
        "text": text,
        "src": src[:30],
        "date": str(it.get("date") or "").strip()[:10],
        "official": bool(it.get("official")),
    })

json.dump({"items": items}, open(OUT, "w"), ensure_ascii=False, indent=2)
n_off = sum(1 for it in items if it["official"])
print(f"已写 {OUT}：{len(items)} 条(官方 {n_off} / 非官方 {len(items) - n_off})")
for it in items:
    print(f"  [{'官方' if it['official'] else '非官方'}] {it['src']}：{it['text'][:45]}…")
