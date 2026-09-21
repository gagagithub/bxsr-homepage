"""AI推荐 —— 每天北京 02:00（GitHub schedule）由 Claude 按《客户画像》给每位规划师挑最多 5 户。

生产 GET /aiRecommend/export：候选客户完整时间线（聊天+通话+跟进备注, 全带日期）+ 画像
    已脱敏：没有客户名，只有客户 id；手机号/证件号/银行卡已打码；已按规划师反馈和 7 天冷却剔过人
→ 这里先用关键词粗筛（客户本人说过可能算意向的话），再分批交给 Claude 逐户读
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
ONLY = [s.strip() for s in (os.environ.get("ONLY") or "").split(",") if s.strip()]

MODEL = "claude-opus-5"
TOP_N = 5
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

PICK_PROMPT = """你在为保险经纪公司「保心上人」的规划师挑选「今天最值得联系的客户」。今天是 {today}。

下面先给你《客户画像》，必须严格照它来挑；然后是一批客户的时间线。

【时间线怎么读】
每个客户以「########## 客户ID」开头，下面按时间排：「日期 客户：」是客户本人说的，「日期 规划师：」是规划师说的，
「[群发]」是规划师群发给很多人的模板，「通话」是语音通话，「跟进备注」是规划师在系统里手写的备注。
⚠ 备注和聊天里提到的时间点，一律按**那一行的日期**来理解。例如 2025-05-19 的备注「客户21号到香港」指的是 2025 年 5 月，早已过去。

【要求】
1. 从这批客户里选出**最多 {n} 户**最符合画像的，按画像的信号优先级排序（信号①「客户自己说过的时间点到了/快到了」最优先）。宁缺毋滥，不够就少选，一个都不合适就返回空数组。
2. 每户必须有**客户本人**的原话和日期作为证据，逐字从时间线里摘，不许改写、不许编。
3. 「为什么是今天」要基于今天 {today} 实际推理，时间点要算对。
4. 「开口第一句」用规划师口吻写一句自然的微信开场，接住客户自己说过的话；不许承诺收益、不许说保证、不许编造时间线里没有的产品事实；不引导资金出境的违规做法。
5. 已成交客户只有客户本人表达了新的购买意思才算「加保」；服务类问题（续期缴费、征税担心、理赔）不算。客户明确说没钱的不推。
6. context 给出证据原话前后各 3 条左右的原文行（逐字复制时间线里的行）。
7. 你看不到客户的名字，所有文字里不要写客户名字，开场白里的称呼只能用时间线里出现过的称呼（如「王总」「姐」）。

========== 客户画像 ==========
{persona}
"""

RANK_PROMPT = """你在为保险经纪公司的规划师排「今天最值得联系的客户」。今天是 {today}。
下面是分几批挑出来的候选（每户已写好命中的信号、客户原话、为什么是今天）。请严格按《客户画像》的信号优先级（信号①「客户自己说过的时间点到了/快到了」最优先；同一信号里，更具体、更近、离成交更近的在前），
从中排出最终前 {n} 户，只返回它们的 cid（按优先级从高到低）。

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
    },
    "required": ["cid", "signal", "jiabao", "quotes", "why_today", "first_line", "context", "rank_note"],
    "additionalProperties": False,
}
PICK_SCHEMA = {"type": "object", "properties": {"picks": {"type": "array", "items": CARD}},
               "required": ["picks"], "additionalProperties": False}
RANK_SCHEMA = {"type": "object", "properties": {"order": {"type": "array", "items": {"type": "integer"}}},
               "required": ["order"], "additionalProperties": False}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


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
    if not persona.strip():
        log("服务器上没有画像文件 persona.md, 停止")
        sys.exit(1)
    planners = {p["userId"]: p["name"] for p in data["planners"]}
    custs = data["customers"]
    log(f"{today} 导出 {len(custs)} 户, 剔除 {data.get('dropped')}")

    by_planner = {}
    for c in custs:
        if c["uid"] in planners and (not ONLY or str(c["uid"]) in ONLY) and prefilter(c):
            by_planner.setdefault(c["uid"], []).append(c)
    log("粗筛后: " + ", ".join(f"{uid}:{len(v)}" for uid, v in by_planner.items()))

    jobs = [(uid, b) for uid, cs in by_planner.items() for b in batches(cs)]
    system = PICK_PROMPT.format(today=today, n=TOP_N, persona=persona)

    def pick(job):
        uid, b = job
        t0 = time.time()
        ids = {c["cid"] for c, _ in b}
        try:
            out, u = call_claude(system, "".join(t for _, t in b) + "\n\n请按要求挑选并输出。", PICK_SCHEMA)
            picks = [p for p in out["picks"] if p["cid"] in ids][:TOP_N]
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
                top = [i for i in out["order"] if i in order][:TOP_N]
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

    if not results:
        log("没有推荐结果, 不回传")
        sys.exit(1)
    up = requests.post(f"{BASE_URL}/aiRecommend/upload",
                       data={"token": TOKEN, "payload": json.dumps({"date": today, "planners": results}, ensure_ascii=False)},
                       timeout=180)
    up.raise_for_status()
    body = up.json()
    log(f"回传: code={body.get('code')} saved={body.get('saved')} msg={str(body.get('msg'))[:100]}")
    if body.get("code") != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
