"""AI推荐 —— 每天北京 02:00（GitHub schedule）由 Claude 按《客户画像》给每位规划师挑最多 5 户。

生产 GET /aiRecommend/export：候选客户完整时间线（聊天+通话+跟进备注, 全带日期）+ 画像
    已脱敏：没有客户名，只有客户 id；手机号/证件号/银行卡已打码；已按规划师反馈和 7 天冷却剔过人
→ 这里先用关键词粗筛（客户本人说过可能算意向的话），关键词刷掉的再让 Jev(TypeSafe) 补漏，再分批交给 Claude 逐户读
    Jev 补漏(9-21 崔伟批准): 关键词不认中文数字「一年十万」、老客户加保等; Jev 不会算「时间点到了」所以只补不删; Jev 挂了就只用关键词
→ 每位规划师：各批挑出的合并，由 Claude 排出最终前 5，其余进候补
→ POST /aiRecommend/upload 存库，规划师在后台「AI推荐」看

⛔本仓库 PUBLIC, Actions 日志人人可看: 只打印条数/耗时/用量, 绝不打印聊天、画像或推荐内容。
"""
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

BASE_URL = os.environ.get("AI_REC_BASE", "https://214club.com.cn")
TOKEN = os.environ.get("CHAT_REVIEW_TOKEN", "")
MODE = (os.environ.get("MODE") or "run").strip()
DRY = os.environ.get("DRY") == "1"      # 只跑不回传(联调用)
ONLY = [s.strip() for s in (os.environ.get("ONLY") or "").split(",") if s.strip()]

MODEL = "claude-opus-5"
TOP_N = 5
PICK_N = 8          # 每批最多挑几户: 多挑的进候补, 规划师点「换一个」时从候补顶上来
BATCH_CHARS = 150_000      # 每批时间线大约多少字（一次调用读完）
WORKERS = 4

TRIVIAL = re.compile(r"^\s*(好的?|好滴|嗯+|哦+|OK|ok|收到|谢谢|感谢|在|在吗|你好|您好|\[[^\]]+\]|[。.！!~，,\s])*\s*$")
# 粗筛：客户本人说过的话里至少沾一条画像信号的边（宽松, 真正判断交给 Claude）
SIGNALS = [
    r"下周|下个月|月底|月初|年底|年初|国庆|中秋|春节|过年|放假|暑假|寒假|几号|\d{1,2}号|\d{1,2}\s*月|\d{1,2}\.\d{1,2}|以后再|之后再|等.{1,12}(再|后)|回来再|到时候|明年|今年底",
    r"\d+\s*[万wW千]|预算|\d{2}\s*岁|\b[2-8]\d\b|年交|趸交|一次性|给(我|孩子|儿子|女儿|老婆|老公|父母|爸|妈|老人)",
    r"怎么买|怎么购买|如何购买|怎么操作|流程|手续|开户|银行卡|通行证|签注|投保人|被保人|需要(什么|哪些)|带什么|去香港|赴港|体检",
    r"[÷×*/=＝]|\d+\s*[%％]|回本|现金价值|领多少|每年领|IRR|怎么算|算下来|实际|分红实现",
]

TYPESAFE_KEY = os.environ.get("TYPESAFE_API_KEY", "")
JEV_MAX_CHARS = 16000      # state 上限 32k token, 超长只留最近的
JEV_SIGNAL = 0.9           # 任一信号概率 ≥ 这个 或 worth ≥ JEV_WORTH 就补进池子(9-21 试点定)
JEV_WORTH = 1.3
JEV_Q = {
    "s1_time": {"type": "noul", "instructions":
        "The CUSTOMER (lines marked 客户：) themselves mentioned a specific future time or condition for deciding/buying "
        "(e.g. after a holiday, after some date, when money arrives, next month), and that time has now arrived or is within about 7 days of TODAY."},
    "s2_money": {"type": "noul", "instructions":
        "The CUSTOMER themselves stated a budget/amount of money, their age, or who the insurance is for (child, spouse, parents)."},
    "s3_howbuy": {"type": "noul", "instructions":
        "The CUSTOMER themselves asked how to buy / the purchase process / account opening / who should be policyholder / what to bring / going to Hong Kong to sign."},
    "s4_calc": {"type": "noul", "instructions":
        "The CUSTOMER themselves did calculations or asked detailed follow-up questions about returns, cash value, payout amounts, or product terms."},
    "s5_return": {"type": "noul", "instructions":
        "After a period of silence, the CUSTOMER proactively came back with a real message (not just 'ok'/'thanks'), and the planner's reply did not really pick it up (short or no follow-up)."},
    "jiabao": {"type": "noul", "instructions":
        "The customer already bought a policy before (成交记录 is not 无) AND the customer themselves expressed a NEW purchase intent "
        "(buy for family, another policy, new money, ask about a different product). Service questions about existing policies do not count."},
    "worth_today": {"type": "score", "instructions":
        "How worthwhile is it for the insurance planner to proactively contact this customer TODAY to push toward a sale, "
        "based on the customer's own words and timing.",
        "criteria": ["Not worth it: no real buying signal from the customer",
                     "Weak: some interest but vague or old",
                     "Good: clear recent buying signal",
                     "Must contact today: a customer-stated time point has arrived or the customer is asking how to buy"]},
}

PICK_PROMPT = """你在为保险经纪公司「保心上人」的规划师挑选「今天最值得联系的客户」。今天是 {today}。

下面先给你《客户画像》，必须严格照它来挑；然后是一批客户的时间线。

【时间线怎么读】
每个客户以「########## 客户ID」开头，下面按时间排：「日期 客户：」是客户本人说的，「日期 规划师：」是规划师说的，
「[群发]」是规划师群发给很多人的模板，「通话」是语音通话，「跟进备注」是规划师在系统里手写的备注。
⚠ 备注和聊天里提到的时间点，一律按**那一行的日期**来理解。例如 2025-05-19 的备注「客户21号到香港」指的是 2025 年 5 月，早已过去。

【要求】
1. 从这批客户里选出**最多 {n} 户**最符合画像的（前 5 户给规划师，其余当候补，规划师不想跟时顶上），按画像的信号优先级排序（信号①「客户自己说过的时间点到了/快到了」最优先）。宁缺毋滥，不够就少选，一个都不合适就返回空数组。
2. 每户必须有**客户本人**的原话和日期作为证据，逐字从时间线里摘，不许改写、不许编。
3. 「为什么是今天」要基于今天 {today} 实际推理，时间点要算对。
4. 「开口第一句」用规划师口吻写一句自然的微信开场，接住客户自己说过的话；不许承诺收益、不许说保证、不许编造时间线里没有的产品事实；不引导资金出境的违规做法。
5. 已成交客户只有客户本人表达了新的购买意思才算「加保」；服务类问题（续期缴费、征税担心、理赔）不算。客户明确说没钱的不推。
6. context 给出证据原话前后各 3 条左右的原文行（逐字复制时间线里的行）。
7. 你看不到客户的名字，所有文字里不要写客户名字，开场白里的称呼只能用时间线里出现过的称呼（如「王总」「姐」）。
8. 「成交指数」deal_index 0-100：这位客户**离成交有多近**（不是今天该不该联系，那个由排序体现）。只看客户本人的话和行为，参考档位：
   90+ 已在谈投保细节/约好签单/问怎么付款；70-89 明确要买、在比方案或定金额；50-69 给了预算或对象、认真在了解；30-49 有兴趣但模糊、或有明显顾虑没解开；30 以下 只是随口问问。
   index_reason 用一句话（30 字以内）说为什么是这个分，要落到客户说过的具体事，例如「给了预算 10 万、问过去香港怎么签，但还在比两款」。

========== 客户画像 ==========
{persona}
"""

RENEW_N = 10        # 翻新激活每人每天最多几户(9-21 崔伟定)

RENEW_PROMPT = """你在帮保险经纪公司「保心上人」的规划师做「翻新激活」：把私海里很久没单聊的客户（P3 已成交 / P2 方案讲解 / P1 需求了解）一个个重新联系起来。今天是 {today}。
公司昨天（{cdate}）发了几条新作品（视频/文章），给每位挑中的客户配一条最对口的作品，当作重新开口的由头。

【要求】
1. 从候选里挑**最多 {n} 户**，P3/P2/P1 三层混着挑（尽量每层都有）。每层候选已按轮换顺序排好，越靠前越该轮到，同样合适时优先挑靠前的。
2. 只挑能和某条作品真正对上的客户：看第一诉求、成交产品、客户说过的话、跟进备注。对不上的不要硬配，宁缺毋滥，一户都对不上就返回空数组。
   P3 已成交客户：配能引出加保、给家人配置、新一笔钱的作品，或和他已买产品相关的新动态；别配会让他觉得自己买亏了的作品。
3. match_reason 一句话（40 字以内）说为什么这条作品配这位客户，要落到客户的诉求、说过的话或成交记录上。
4. first_line 用规划师口吻写一句微信开场，自然带出这条作品（比如「昨天我们发了一条……想到你之前……」），能接住客户以前说过的话更好。
   很久没联系了，语气别像推销，别一上来就问买不买；不许承诺收益、不许说保证、不许编作品里没有的产品事实；
   称呼只能用资料里出现过的称呼（如「王总」「姐」），没有就不带称呼。
5. 你看不到客户名字，所有文字里都不要写客户名字。
"""

RENEW_SCHEMA = {"type": "object", "properties": {"picks": {"type": "array", "items": {
    "type": "object", "properties": {
        "cid": {"type": "integer"}, "creation_id": {"type": "integer"},
        "match_reason": {"type": "string"}, "first_line": {"type": "string"}},
    "required": ["cid", "creation_id", "match_reason", "first_line"], "additionalProperties": False}}},
    "required": ["picks"], "additionalProperties": False}

LAYER_NAME = {"P3": "P3 已成交", "P2": "P2 方案讲解", "P1": "P1 需求了解"}

RANK_PROMPT = """你在为保险经纪公司的规划师排「今天最值得联系的客户」。今天是 {today}。
下面是分几批挑出来的候选（每户已写好命中的信号、客户原话、为什么是今天）。请严格按《客户画像》的信号优先级（信号①「客户自己说过的时间点到了/快到了」最优先；同一信号里，更具体、更近、离成交更近的在前），
把全部候选按优先级从高到低排好，返回全部 cid（前 {n} 户给规划师，其余当候补按这个顺序顶上）。

========== 客户画像 ==========
{persona}
"""

CARD = {
    "type": "object",
    "properties": {
        "cid": {"type": "integer"},
        "signal": {"type": "string", "enum": ["1", "2", "3", "4", "5"]},
        "jiabao": {"type": "boolean"},
        "quotes": {"type": "array", "items": {
            "type": "object", "properties": {"date": {"type": "string"}, "text": {"type": "string"}},
            "required": ["date", "text"], "additionalProperties": False}},
        "why_today": {"type": "string"},
        "first_line": {"type": "string"},
        "context": {"type": "array", "items": {"type": "string"}},
        "rank_note": {"type": "string"},
        "deal_index": {"type": "integer"},
        "index_reason": {"type": "string"},
    },
    "required": ["cid", "signal", "jiabao", "quotes", "why_today", "first_line", "context", "rank_note",
                 "deal_index", "index_reason"],
    "additionalProperties": False,
}
PICK_SCHEMA = {"type": "object", "properties": {"picks": {"type": "array", "items": CARD}},
               "required": ["picks"], "additionalProperties": False}
RANK_SCHEMA = {"type": "object", "properties": {"order": {"type": "array", "items": {"type": "integer"}}},
               "required": ["order"], "additionalProperties": False}


# 运行报告(9-21 崔伟: 每天跑完夏梅发他一条, 成功失败都发)。只放条数/原因, 不放任何客户内容。
STATUS = {"warnings": [], "last": ""}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    STATUS["last"] = msg
    if "失败" in msg:
        STATUS["warnings"].append(msg)


def report(ok, today, error=""):
    if MODE != "run" or DRY:
        return
    body = {"date": today or time.strftime("%Y-%m-%d"), "ok": ok, "error": error, "warnings": STATUS["warnings"][:5]}
    for k in ("jevChecked", "jevAdded", "renewDate", "renewWorks", "renewSkip"):
        if k in STATUS:
            body[k] = STATUS[k]
    try:
        r = requests.post(f"{BASE_URL}/aiRecommend/report",
                          data={"token": TOKEN, "payload": json.dumps(body, ensure_ascii=False)}, timeout=60)
        print(f"运行报告: HTTP {r.status_code} {r.text[:80]}", flush=True)
    except Exception as e:
        print(f"运行报告发送失败 {type(e).__name__}", flush=True)


def call_claude(system, user, schema):
    """走崔伟的 Claude 订阅(Claude Code CLI + CLAUDE_CODE_OAUTH_TOKEN), 返回 (dict, 用量)。"""
    cmd = ["claude", "-p", "--model", MODEL, "--effort", "high", "--system-prompt", system,
           "--tools", "", "--output-format", "json", "--no-session-persistence",
           "--json-schema", json.dumps(schema, ensure_ascii=False)]
    proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=1800)
    try:
        d = json.loads(proc.stdout)
    except Exception:
        raise RuntimeError(f"CLI 退出码 {proc.returncode}: {proc.stderr[:300]}")
    if d.get("is_error"):
        raise RuntimeError(f"CLI 报错 subtype={d.get('subtype')}")
    if d.get("structured_output") is None:
        raise RuntimeError("没有拿到结构化输出")
    return d["structured_output"], d


def usage_line(d):
    parts = []
    for m, u in (d.get("modelUsage") or {}).items():
        parts.append(f"in={u.get('inputTokens')} cache_read={u.get('cacheReadInputTokens')} out={u.get('outputTokens')}")
    return "; ".join(parts) or "-"


def customer_said(c):
    return [l.split("客户：", 1)[1] for l in c["lines"] if " 客户：" in l[:16]]


def prefilter(c):
    said = [t for t in customer_said(c) if not TRIVIAL.match(t)]
    if not said:
        return False
    txt = " ".join(said)
    if any(re.search(p, txt) for p in SIGNALS):
        return True
    # ⑤ 沉默 ≥7 天后客户主动冒头(只看日期粒度)
    last = None
    for l in c["lines"]:
        d = l[:10]
        if " 客户：" in l[:16] and last and d > last and _days(last, d) >= 7:
            return True
        if re.match(r"\d{4}-\d{2}-\d{2}", d):
            last = d
    return False


def _days(a, b):
    ta = time.mktime(time.strptime(a, "%Y-%m-%d"))
    tb = time.mktime(time.strptime(b, "%Y-%m-%d"))
    return (tb - ta) / 86400


def jev_state(c, today):
    head = (f"TODAY = {today}\n客户ID {c['cid']} | 状态：{c['state']} | 第一诉求：{c['appeal']} | "
            f"成交记录：{c.get('deals') or '无'}\n")
    keep, size = [], len(head)
    for l in reversed(c["lines"]):
        if size + len(l) + 1 > JEV_MAX_CHARS:
            break
        keep.append(l)
        size += len(l) + 1
    return head + "\n".join(reversed(keep))


def jev_hit(c, today):
    """True=Jev 判有信号; None=调用失败(当没命中处理)。"""
    for attempt in range(4):
        try:
            r = requests.post("https://api.typesafe.ai/v1/systemone",
                              headers={"Authorization": f"Bearer {TYPESAFE_KEY}"},
                              json={"model": "jev-latest", "state": jev_state(c, today), "questions": JEV_Q},
                              timeout=120)
            if r.status_code in (429, 500, 502, 503, 529):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            a = r.json()["answers"]
            sig = max(v["noul"] for k, v in a.items() if v.get("type") == "noul")
            return sig >= JEV_SIGNAL or a["worth_today"]["score"] >= JEV_WORTH
        except Exception:
            time.sleep(2 ** attempt)
    return None


def jev_rescue(custs, today):
    """关键词刷掉的客户交给 Jev 补漏, 返回补进来的 cid 集合。任何异常都退回空集(只用关键词)。"""
    if not TYPESAFE_KEY or not custs:
        return set()
    try:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=12) as pool:
            hits = list(pool.map(lambda c: jev_hit(c, today), custs))
        fail = sum(h is None for h in hits)
        got = {c["cid"] for c, h in zip(custs, hits) if h}
        log(f"Jev 补漏: 查 {len(custs)} 户, 补进 {len(got)} 户, 调用出错 {fail} 户, {time.time() - t0:.0f}s")
        STATUS.update(jevChecked=len(custs), jevAdded=len(got))
        return got
    except Exception as e:
        log(f"Jev 补漏整体失败, 只用关键词 {type(e).__name__}")
        return set()


def dossier(c):
    head = (f"\n########## 客户ID {c['cid']} | 状态：{c['state']} | 第一诉求：{c['appeal']} | "
            f"成交记录：{c.get('deals') or '无'} | {c['sea']}")
    return head + "\n" + "\n".join(c["lines"])


def batches(custs):
    out, cur, size = [], [], 0
    for c in custs:
        t = dossier(c)
        if cur and size + len(t) > BATCH_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append((c, t))
        size += len(t)
    if cur:
        out.append(cur)
    return out


def renew_all(today, planners, exclude):
    """翻新激活: 每位规划师从私海 P3/P2/P1 轮换队列里配昨天的作品挑 ≤RENEW_N 户。返回 {uid: [卡片]}。失败返回 {}。"""
    try:
        r = requests.get(f"{BASE_URL}/aiRecommend/renewExport", params={"token": TOKEN}, timeout=300)
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        log(f"翻新: 导出失败 {type(e).__name__}")
        return {}
    if d.get("code") != 0:
        log(f"翻新: 导出失败 {str(d.get('msg'))[:80]}")
        return {}
    creations = d.get("creations") or []
    if not creations:
        log(f"翻新: {d.get('creationDate')} 没有作品, 今天不做翻新")
        STATUS["renewSkip"] = f"{str(d.get('creationDate'))[5:]} 没有作品，今天不做"
        return {}
    STATUS.update(renewDate=d.get("creationDate"), renewWorks=len(creations))
    cmap = {c["id"]: c for c in creations}
    works = "\n\n".join(
        f"【作品 {c['id']}】{c['title']}\n分类：{c['category']} | 账号：{c['account']} | 推荐产品：{c['product']} | 形式：{c['type']}\n脚本：{c['script']}"
        for c in creations)
    by = {}
    for c in d.get("candidates") or []:
        if c["uid"] in planners and (not ONLY or str(c["uid"]) in ONLY) and c["cid"] not in exclude.get(c["uid"], set()):
            by.setdefault(c["uid"], []).append(c)
    log(f"翻新: 作品 {len(creations)} 条, 候选 " + ", ".join(f"{u}:{len(v)}" for u, v in by.items()))
    system = RENEW_PROMPT.format(today=today, cdate=d.get("creationDate"), n=RENEW_N)

    def one(item):
        uid, cs = item
        parts = [f"========== 昨天的作品 ==========\n{works}\n\n========== 候选客户 =========="]
        for layer in ("P3", "P2", "P1"):
            rows = [c for c in cs if c["layer"] == layer]
            if not rows:
                continue
            parts.append(f"\n## {LAYER_NAME[layer]}（按轮换顺序，越前越该轮到）")
            for c in rows:
                said = "；".join(c.get("said") or []) or "无"
                notes = "；".join(c.get("notes") or []) or "无"
                parts.append(f"客户ID {c['cid']} | 第一诉求：{c['appeal'] or '未填'} | 成交：{c['deals']} | "
                             f"上次单聊：{c['lastSingle']} | 加微：{c['adddate']} | 客户说过：{said} | 跟进备注：{notes}")
        t0 = time.time()
        try:
            out, u = call_claude(system, "\n".join(parts) + "\n\n请按要求挑选并输出。", RENEW_SCHEMA)
        except Exception as e:
            log(f"翻新 {uid}: 失败 {type(e).__name__}: {str(e)[:150]}")
            return uid, []
        meta = {c["cid"]: c for c in cs}
        cards, seen = [], set()
        for p in out["picks"]:
            if p["cid"] not in meta or p["creation_id"] not in cmap or p["cid"] in seen:
                continue
            seen.add(p["cid"])
            m, w = meta[p["cid"]], cmap[p["creation_id"]]
            acct = w["account"] if w["account"] and w["account"] != "待定" else (w["category"] + "号" if w["category"] else "")
            cards.append({"cid": p["cid"], "signal": "", "jiabao": False, "quotes": [], "context": [], "rank_note": "",
                          "why_today": p["match_reason"], "first_line": p["first_line"],
                          "creation_id": w["id"], "creation_title": w["title"], "creation_url": w["url"],
                          "creation_account": acct, "state": m["state"], "appeal": m["appeal"],
                          "deals": m["deals"], "sea": "私海"})
        cards = cards[:RENEW_N]
        log(f"翻新 {uid}: 候选 {len(cs)} → {len(cards)} 户, {time.time() - t0:.0f}s, {usage_line(u)}")
        return uid, cards

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return {uid: cards for uid, cards in pool.map(one, by.items()) if cards}


def main():
    if not TOKEN:
        log("缺 CHAT_REVIEW_TOKEN")
        sys.exit(1)
    if MODE == "ping":
        _, u = call_claude("你是测试助手。", "回复 ok", RANK_SCHEMA)
        log(f"ping ok {usage_line(u)}")
        return

    r = requests.get(f"{BASE_URL}/aiRecommend/export", params={"token": TOKEN}, timeout=300)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        log(f"导出失败: {data.get('msg')}")
        sys.exit(1)
    today, persona = data["today"], data.get("persona") or ""
    STATUS["today"] = today
    if not persona.strip():
        log("服务器上没有画像文件 persona.md, 停止")
        sys.exit(1)
    planners = {p["userId"]: p["name"] for p in data["planners"]}
    custs = data["customers"]
    log(f"{today} 导出 {len(custs)} 户, 剔除 {data.get('dropped')}")

    mine = [c for c in custs if c["uid"] in planners and (not ONLY or str(c["uid"]) in ONLY)]
    passed = {c["cid"] for c in mine if prefilter(c)}
    log(f"关键词粗筛: {len(passed)}/{len(mine)} 户")
    passed |= jev_rescue([c for c in mine if c["cid"] not in passed], today)

    by_planner = {}
    for c in mine:
        if c["cid"] in passed:
            by_planner.setdefault(c["uid"], []).append(c)
    log("粗筛后: " + ", ".join(f"{uid}:{len(v)}" for uid, v in by_planner.items()))

    jobs = [(uid, b) for uid, cs in by_planner.items() for b in batches(cs)]
    system = PICK_PROMPT.format(today=today, n=PICK_N, persona=persona)

    def pick(job):
        uid, b = job
        t0 = time.time()
        ids = {c["cid"] for c, _ in b}
        try:
            out, u = call_claude(system, "".join(t for _, t in b) + "\n\n请按要求挑选并输出。", PICK_SCHEMA)
            picks = [p for p in out["picks"] if p["cid"] in ids][:PICK_N]
            log(f"{uid}: 批 {len(b)} 户 → {len(picks)} 户, {time.time() - t0:.0f}s, {usage_line(u)}")
            return uid, picks, b
        except Exception as e:
            log(f"{uid}: 批 {len(b)} 户失败 {type(e).__name__}: {str(e)[:200]}")
            return uid, [], b

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        done = list(pool.map(pick, jobs))

    meta = {c["cid"]: c for c in custs}
    merged = {}
    for uid, picks, _ in done:
        merged.setdefault(uid, []).extend(picks)

    results = []
    for uid, cards in merged.items():
        order = [c["cid"] for c in cards]
        if len(cards) > TOP_N:
            brief = [{k: c[k] for k in ("cid", "signal", "jiabao", "quotes", "why_today", "rank_note")} for c in cards]
            try:
                out, u = call_claude(RANK_PROMPT.format(today=today, n=TOP_N, persona=persona),
                                     json.dumps(brief, ensure_ascii=False), RANK_SCHEMA)
                top = [i for i in out["order"] if i in order]
                order = top + [i for i in order if i not in top]
                log(f"{uid}: 合并 {len(cards)} 户排序 ok, {usage_line(u)}")
            except Exception as e:
                log(f"{uid}: 合并排序失败, 按批次顺序 {type(e).__name__}")
        byid = {c["cid"]: c for c in cards}
        full = []
        for i in order:
            c = dict(byid[i])
            m = meta.get(i, {})
            c.update({"state": m.get("state"), "appeal": m.get("appeal"), "deals": m.get("deals"), "sea": m.get("sea")})
            full.append(c)
        results.append({"userId": uid, "top": full[:TOP_N], "bench": full[TOP_N:]})
        log(f"{uid}: 前 {len(full[:TOP_N])} 户, 候补 {len(full[TOP_N:])} 户")

    exclude = {r["userId"]: {c["cid"] for c in r["top"] + r["bench"]} for r in results}
    renew = renew_all(today, planners, exclude)
    for r in results:
        r["renew"] = renew.pop(r["userId"], [])
    for uid, cards in renew.items():
        results.append({"userId": uid, "top": [], "bench": [], "renew": cards})

    if not results:
        log("没有推荐结果, 不回传")
        sys.exit(1)
    if DRY:
        log("DRY=1, 不回传")
        return
    up = requests.post(f"{BASE_URL}/aiRecommend/upload",
                       data={"token": TOKEN, "payload": json.dumps({"date": today, "planners": results}, ensure_ascii=False)},
                       timeout=180)
    up.raise_for_status()
    body = up.json()
    log(f"回传: code={body.get('code')} saved={body.get('saved')} msg={str(body.get('msg'))[:100]}")
    if body.get("code") != 0:
        sys.exit(1)
    report(True, today)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        if e.code not in (0, None):
            report(False, STATUS.get("today"), STATUS["last"])
        raise
    except Exception as e:
        report(False, STATUS.get("today"), f"{type(e).__name__}: {str(e)[:150]}")
        raise
