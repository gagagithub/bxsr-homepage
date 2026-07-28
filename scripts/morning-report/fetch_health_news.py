# -*- coding: utf-8 -*-
"""财经晨报·健康档民生新闻(AI 联网检索)。

东财财经电报里几乎没有"老百姓看病报销"向的健康新闻(只有药企业绩/FDA审批这类上市公司视角),
所以健康档新闻改成: 把崔伟的需求(读者画像+医保/三高民生内容)告诉通义千问, 让它联网自己去找。
qwen-plus 的 enable_search 自带联网搜索; DeepSeek API 无联网能力故不能用它来找。

输出 health_news.json = {"items":[{"text","src","date"}]}; 检索失败/无结果时输出空 items,
llm_morning.py 里健康档自动回退为"东财里偶尔有的民生新闻 + 每日健康小课堂"(不阻塞晨报)。
"""
import os, json, re, sys, urllib.request
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = f"{BASE}/health_news.json"

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
    print(f"⚠ {msg} —— 写空 health_news.json, 健康档回退东财+小课堂", file=sys.stderr)
    json.dump({"items": []}, open(OUT, "w"), ensure_ascii=False)
    sys.exit(0)

key = get_key()
if not key:
    bail("未找到 DASHSCOPE_API_KEY")

bj_now = datetime.now(timezone.utc) + timedelta(hours=8)
TODAY = f"{bj_now.year}年{bj_now.month}月{bj_now.day}日"

# 昨天的检索结果留底(CI里随仓库checkout带回来), 作为排除清单防止同一事件天天重复上晨报
prev = []
try:
    prev = [it["text"][:60] for it in json.load(open(OUT)).get("items", [])]
except Exception:
    pass
EXCLUDE = ""
if prev:
    EXCLUDE = "\n\n以下是昨天晨报已经报道过的内容, 不要重复收录(除非今天有重要新进展):\n" + \
              "\n".join(f"- {p}…" for p in prev)

# ⚠两步走的原因(实测): 联网搜索的检索词由用户消息生成, 一旦消息里塞满 JSON schema,
# 检索质量崩掉直接交白卷 {"items":[]}; 同一问题用自由文本问就能搜到。故:
# 第一步自由文本联网搜 → 第二步不联网, 纯粹把第一步的文本整理成 JSON(无新增信息, 不引入编造)。
SEARCH_USER = f"""今天是{TODAY}(北京时间)。请联网搜索最近1-3天中国的民生健康新闻:
医保报销政策、医保目录/谈判药、药品和耗材集采降价、门诊住院报销、商保创新药、
三高等慢病防治政策、体检筛查、生育/护理保险待遇等——只要跟老百姓看病花钱直接相关的。
不要:药企业绩/股价/融资、公司研发进展、海外FDA审批这类上市公司视角的消息。
列出3-5条, 每条给出: 新闻内容(保留报道里的具体数字、金额、比例)、信息来源(媒体名)、报道日期。
只要真实搜到的, 搜不到就明说, 严禁编造。同一事件的多篇报道合并成一条。{EXCLUDE}"""

FORMAT_USER = """把下面这份检索结果整理成 JSON(不要多余文字、不要 markdown 代码块):
{"items": [
  {"text": "新闻内容完整一段(120-200字), 只用检索结果里已有的事实和数字, 不做评论",
   "src": "报道媒体名", "date": "报道日期 YYYY-MM-DD"}
]}
要求: 最多5条, 按对普通家庭的实际影响排序; 检索结果说没搜到新闻的话就输出 {"items": []};
绝不添加检索结果里没有的信息。

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
        {"role": "system", "content": "你是新闻检索助手, 只报告真实检索到的内容, 宁缺毋滥。"},
        {"role": "user", "content": SEARCH_USER},
    ], enable_search=True)
    print(f"—— 第一步联网检索返回 {len(found)} 字")
    content = call_qwen([
        {"role": "user", "content": FORMAT_USER + found},
    ], enable_search=False)
    content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
    # 万一模型在 JSON 外包了说明文字, 抠出最外层 {...}
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
for it in (data.get("items") or [])[:5]:
    text = str(it.get("text") or "").strip()
    if len(text) < 40:   # 太短的丢弃, 防空壳条目
        continue
    items.append({"text": text,
                  "src": str(it.get("src") or "联网检索").strip()[:20],
                  "date": str(it.get("date") or "").strip()[:10]})

json.dump({"generated_utc": datetime.now(timezone.utc).isoformat(), "items": items},
          open(OUT, "w"), ensure_ascii=False, indent=2)
print(f"联网检索到民生健康新闻 {len(items)} 条 → health_news.json")
for it in items:
    print(f"  - [{it['src']} {it['date']}] {it['text'][:50]}")
