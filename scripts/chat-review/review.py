"""规划师聊天复盘 —— 每天 18:00 由生产服务器 workflow_dispatch 触发。

生产导出过去 24 小时的 1 对 1 聊天(已脱敏: 客户只有编号, 手机号/证件号已打码)
→ Claude(走崔伟的 Claude 订阅, CLI + CLAUDE_CODE_OAUTH_TOKEN) 每位规划师出一份复盘 + 一份给崔伟的团队汇总
→ 回传生产 /chatReview/upload, 生产把 {{c:编号}} 换回客户昵称、存完整页、夏梅推送。

⛔本仓库 PUBLIC, Actions 日志人人可看: 这里只打印条数/耗时/token, 绝不打印聊天或分析内容。
"""
import html
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

BASE_URL = os.environ.get("CHAT_REVIEW_BASE", "https://214club.com.cn")
TOKEN = os.environ.get("CHAT_REVIEW_TOKEN", "")
MODE = (os.environ.get("MODE") or "run").strip()
TEST_VXID = (os.environ.get("TEST_VXID") or "").strip()
ONLY = [s.strip() for s in (os.environ.get("ONLY") or "").split(",") if s.strip()]
WINDOW_END = (os.environ.get("WINDOW_END") or "").strip()
FORCE = (os.environ.get("FORCE") or "").strip().lower() == "true"  # 同日补跑: 绕过 sent-<日期> 标记

MODEL = "claude-opus-5"
# 规划师窗口内和客户来往少于这么多条就不出复盘(没东西可说, 硬写只会是空话)
MIN_MESSAGES = 6
BROADCAST_MIN = 20  # 同一句话发给 ≥20 户视为群发


def drop_broadcast_only(p):
    """去掉「只收到群发、没回话」的客户。9-22 林付贤群发 1888 户, 原文 200 万字超上下文, 整人分析失败。
    客户回了话的保留(群发那句是上下文)。"""
    cnt = {}
    for c in p["customers"]:
        for t in {m["text"] for m in c["today"] if m["who"] == "规"}:
            cnt[t] = cnt.get(t, 0) + 1
    bc = {t for t, n in cnt.items() if n >= BROADCAST_MIN}
    if not bc:
        return
    before = len(p["customers"])
    p["customers"] = [c for c in p["customers"]
                      if not all(m["who"] == "规" and m["text"] in bc for m in c["today"])]
    log(f"{p['vxId']}: 识别群发 {len(bc)} 句, 剔除只收到群发的 {before - len(p['customers'])} 户")


SYSTEM_PROMPT = """你是「保心上人」保险经纪团队里一位成交经验很丰富的老规划师，也是大家的成交教练。每天傍晚，你把一位规划师过去 24 小时和客户的企业微信 1 对 1 聊天全部读一遍，只从「怎么把单子往成交推」的角度，告诉他哪里可以做得更好、换成怎么说，明天先联系谁。

【公司背景】
- 规划师通过企业微信服务客户，客户多为 45–70 岁、手里有一笔闲钱的人。
- 卖两类产品：内地保险（增额终身寿、年金、养老金、快返年金等）和香港保险（储蓄分红险，常见代号如「116」=一次性交一年后每年领 6%，「258」=两年交第五年领 8%）。香港保险要客户本人赴港签单，常涉及港澳通行证、香港银行账户、资金过去的方式。
- 客户状态：需求了解状态 → 方案讲解状态 → 已成交状态。

【你要看的：一切为了成交】
- 成交信号有没有抓住：客户说「你帮我选一款」「给我出个方案」「哪天过来」、问签单流程和要求，这些是最该抓的时刻；规划师有没有当场把下一步定死（出什么方案、什么时候给、要客户准备什么、约哪天见）。
- 关键信息有没有问到：年龄、预算、这笔钱的用途（自己领钱还是留给孩子）、港澳通行证、香港账户、谁做投保人和被保人。缺了这些方案做不出来，单子就停住。
- 客户的顾虑有没有接住：怕税、怕汇率、嫌领得少、嫌公司小、犹豫时，是认真一条条解答、把顾虑变成推进的理由，还是一句话挡回去、或者直接放弃、或者顾虑没解决就逼单。
- 专业意见给得对不对：投保架构（投保人、被保人、受益人怎么安排才贴合客户目的）、产品和需求是否匹配、客户的预期和能买到的差太远时有没有先把预期拉回来、客户对产品的理解有偏差时有没有顺势讲清楚。
- 沟通节奏：一次发太多、连问好几个问题让客户不知道先答哪个；客户回了一句就没下文；客户主动发来的消息没回。
- 做得好的地方也要指出来，让他知道哪些该保持。

【不要碰的】
- 不点评合规、违规、监管、法律责任，不提《保险法》、投诉、监管处罚这类角度。
- 不点评返佣、个人头衔、荣誉、自我介绍里的资质说法。
- 不单独挑「分红保证不保证的说法」「收益怎么表述」这类措辞毛病；只有当客户明显理解错了、影响他做决定时，才从帮客户弄明白、推进成交的角度提。

【判断原则】
- 每一条都要落到具体客户和聊天原话上，不写「要加强沟通」这类空话；没问题的客户不用硬挑毛病，条数宁少勿滥。
- 只评价「本次窗口」里的消息；「之前的聊天」只用来理解来龙去脉。
- 你看不到语音、图片、文件、通话内容，只知道发了什么类型。不要猜测这些内容，也不要因为看不到就判定规划师做错。
- 客户名字不在材料里，一律用占位符 {{c:编号}} 称呼客户（编号就是材料里每个客户标题上的那个编号，例如 {{c:33703}}），所有字段都这样写，包括 push。系统会自动换成客户微信名。
- 引用原话放在 quote 字段，要是聊天里的原文（太长可以用…截短），不要改写。
- 你写的「换成这样」和开场白，规划师会照着发给客户，所以里面不能出现你自己编的事实：公司的赴港行程和日期、名额、报销、优惠活动，产品的分红实现率、历史数据、具体利益数字，利率下调、产品停售之类的政策和市场说法。聊天材料里客户或规划师说过的可以用；材料里没有的，一律写成【待填：赴港日期】【待填：该产品分红实现率】这样的空位，让规划师自己核实后再填。
- 用「你」称呼规划师，语气像一个懂行、说话直接的老同事，是帮他多成交，不是挑他的错。

【输出字段】
- overview：两三句话，今天整体怎么样、离成交最近的是谁、最要紧的一件事是什么。
- improve：离成交最有帮助的 2–6 条改进，problem 说清楚错过了什么，better 写出具体可以照着说的话。
- good：今天做得好的 1–2 段，why 说明好在哪里、以后要保持。
- tomorrow：明天最该先联系的 2–4 个客户，why 说原因，opener 给一句可以直接发的开场白。
- customers：本次窗口里聊过的每个客户各一行，status 一句话说清楚这个客户现在走到哪一步、下一步是什么，level 取「快成交」「推进中」「卡住了」「一般」之一。
- push：发到规划师企业微信的文字，300 字以内，纯文本不用 markdown。3–4 行：离成交最近的客户和该做的动作；最该改的一两条；明天先联系谁。每行开头可用一个表情符号。"""


TEAM_PROMPT = """你是「保心上人」规划师团队的成交教练。下面是今天每位规划师聊天复盘的结构化结果（每人一份）。请写一段发给老板崔伟的团队汇总，要求：
- 纯文本，不用 markdown，600 字以内。只从成交的角度写，不谈合规、违规、返佣、个人头衔。
- 第一段：今天全队离成交最近的几个客户（谁的客户、卡在哪一步、需要什么推一把）。
- 然后每位规划师一行：姓名 + 今天最该改的一件事 + 今天做得最好的一段。不排名次，不评价勤奋与否。
- 最后一行：今天全队最普遍的一个问题，一句话。
- 客户一律沿用 {{c:编号}} 占位符，不要改写。"""


PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "improve": {"type": "array", "items": {
            "type": "object",
            "properties": {"c": {"type": "string"}, "quote": {"type": "string"},
                           "problem": {"type": "string"}, "better": {"type": "string"}},
            "required": ["c", "quote", "problem", "better"], "additionalProperties": False}},
        "good": {"type": "array", "items": {
            "type": "object",
            "properties": {"c": {"type": "string"}, "quote": {"type": "string"}, "why": {"type": "string"}},
            "required": ["c", "quote", "why"], "additionalProperties": False}},
        "tomorrow": {"type": "array", "items": {
            "type": "object",
            "properties": {"c": {"type": "string"}, "why": {"type": "string"}, "opener": {"type": "string"}},
            "required": ["c", "why", "opener"], "additionalProperties": False}},
        "customers": {"type": "array", "items": {
            "type": "object",
            "properties": {"c": {"type": "string"}, "status": {"type": "string"},
                           "level": {"type": "string", "enum": ["快成交", "推进中", "卡住了", "一般"]}},
            "required": ["c", "status", "level"], "additionalProperties": False}},
        "push": {"type": "string"},
    },
    "required": ["overview", "improve", "good", "tomorrow", "customers", "push"],
    "additionalProperties": False,
}


def name_unarchived(s):
    """系统里没建档的客户(编号 x1、x2…)生产端查不到昵称, 这里直接写成「未建档客户1」, 规划师靠引用原话认人。"""
    return re.sub(r"\{\{c:x(\d+)\}\}", r"未建档客户\1", s or "")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def call_claude(system, user, schema=None):
    """一次分析调用, 走崔伟的 Claude 订阅(Claude Code CLI + CLAUDE_CODE_OAUTH_TOKEN), 不走按量付费的 API。
    返回 (文本, 用量信息 dict)。有 schema 时文本是保证合法的 JSON。"""
    cmd = ["claude", "-p", "--model", MODEL, "--effort", "high", "--system-prompt", system,
           "--tools", "", "--output-format", "json", "--no-session-persistence"]
    if schema:
        cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    proc = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=1500)
    try:
        d = json.loads(proc.stdout)
    except Exception:
        # 只打退出码和错误输出开头, 不打任何聊天内容
        raise RuntimeError(f"CLI 退出码 {proc.returncode}: {proc.stderr[:300]}")
    if d.get("is_error"):
        raise RuntimeError(f"CLI 报错 subtype={d.get('subtype')}: {str(d.get('result'))[:300]}")
    if schema:
        if d.get("structured_output") is None:
            raise RuntimeError("没有拿到结构化输出")
        text = json.dumps(d["structured_output"], ensure_ascii=False)
    else:
        text = (d.get("result") or "").strip()
    return text, d


def usage_line(d):
    parts = []
    for m, u in (d.get("modelUsage") or {}).items():
        parts.append(f"{m}: in={u.get('inputTokens')} cache_read={u.get('cacheReadInputTokens')} "
                     f"cache_write={u.get('cacheCreationInputTokens')} out={u.get('outputTokens')}")
    return "; ".join(parts) or "无用量信息"


def cost_usd(d):
    # 按 API 牌价折算的等值金额(走订阅不实际扣费, 只用来观察用量)
    return d.get("total_cost_usd") or 0.0


def build_user_prompt(p, window_start, window_end):
    lines = [f"规划师：{p['name']}", f"本次窗口：{window_start} 至 {window_end}（北京时间）", ""]
    for c in p["customers"]:
        meta = [c.get("state") or "", c.get("appeal") or ""]
        if c.get("dealState"):
            meta.append(f"成交情况:{c['dealState']}")
        if c.get("addDate"):
            meta.append(f"加好友{c['addDate']}")
        if c.get("silentDays") is not None:
            meta.append(f"本次之前已 {c['silentDays']} 天没聊过")
        elif not c.get("history"):
            meta.append("之前没有聊天记录")
        lines.append(f"===== 客户 {c['key']}（{' / '.join(m for m in meta if m)}）=====")
        if c.get("history"):
            lines.append("—— 之前的聊天（仅供理解来龙去脉）——")
            lines += [f"{m['t']} {m['who']}：{m['text']}" for m in c["history"]]
            lines.append("—— 本次窗口 ——")
        lines += [f"{m['t']} {m['who']}：{m['text']}" for m in c["today"]]
        lines.append("")
    lines.append("「规」= 规划师本人，「客」= 客户。请按要求输出。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 完整页 HTML
PAGE_CSS = """
:root{--bg:#f6f1e7;--card:#fffdf8;--ink:#2a241c;--sub:#6f6556;--line:#e6dccb;--gold:#a87a2c;
--red:#b3261e;--redbg:#fbecea;--amber:#8a5a00;--amberbg:#fdf3dc;--green:#2f6b3a;--greenbg:#e9f3ea}
@media (prefers-color-scheme:dark){:root{--bg:#1b1812;--card:#24201a;--ink:#efe7d8;--sub:#b3a893;--line:#3a3328;
--gold:#d6aa5c;--red:#f08a80;--redbg:#3a1f1c;--amber:#e8c070;--amberbg:#352a15;--green:#8fd19b;--greenbg:#1d2e20}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.7 -apple-system,"PingFang SC","Noto Sans SC","Microsoft YaHei",sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:0 0 4px}.meta{color:var(--sub);font-size:13px;margin-bottom:16px}
.ov{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--gold);border-radius:10px;padding:14px 16px;margin-bottom:22px}
h2{font-size:18px;margin:26px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.card.risk{border-color:var(--red);background:var(--redbg)}
.who{font-weight:700;margin-bottom:4px}.q{color:var(--sub);border-left:3px solid var(--line);padding:2px 10px;margin:6px 0;white-space:pre-wrap}
.lab{font-size:13px;font-weight:700;color:var(--gold);margin-top:6px}.risk .lab{color:var(--red)}
.say{background:var(--bg);border-radius:8px;padding:8px 10px;margin-top:4px;white-space:pre-wrap}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
td:first-child{white-space:nowrap;font-weight:600}
.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;white-space:nowrap}
.t-快成交{background:var(--greenbg);color:var(--green)}.t-推进中{background:var(--amberbg);color:var(--amber)}
.t-卡住了{background:var(--redbg);color:var(--red)}.t-一般{background:var(--line);color:var(--sub)}
.foot{color:var(--sub);font-size:12px;margin-top:30px}
"""


def esc(s):
    """HTML 转义, 但保留 {{c:xxx}} 占位符原样(生产端替换成已转义的昵称)。"""
    return html.escape(s or "", quote=True)


def who(c):
    key = (c or "").replace("{", "").replace("}", "").replace("c:", "").strip()
    return f"{{{{c:{esc(key)}}}}}"


def render_page(p, r, window_start, window_end):
    out = [f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>聊天复盘 · {esc(p['name'])}</title><style>{PAGE_CSS}</style></head><body><div class='wrap'>",
           f"<h1>{esc(p['name'])} · 聊天复盘</h1>",
           f"<div class='meta'>{esc(window_start)} – {esc(window_end)} · {len(p['customers'])} 位客户 · {p['msgCount']} 条消息 · Claude 分析</div>",
           f"<div class='ov'>{esc(r['overview'])}</div>"]
    if r["improve"]:
        out.append("<h2>🔻 可以做得更好</h2>")
        for x in r["improve"]:
            out.append(f"<div class='card'><div class='who'>{who(x['c'])}</div><div class='q'>{esc(x['quote'])}</div>"
                       f"<div class='lab'>问题</div><div>{esc(x['problem'])}</div>"
                       f"<div class='lab'>换成这样</div><div class='say'>{esc(x['better'])}</div></div>")
    if r["good"]:
        out.append("<h2>✅ 做得好的</h2>")
        for x in r["good"]:
            out.append(f"<div class='card'><div class='who'>{who(x['c'])}</div><div class='q'>{esc(x['quote'])}</div>"
                       f"<div>{esc(x['why'])}</div></div>")
    if r["tomorrow"]:
        out.append("<h2>📌 明天先联系</h2>")
        for x in r["tomorrow"]:
            out.append(f"<div class='card'><div class='who'>{who(x['c'])}</div><div>{esc(x['why'])}</div>"
                       f"<div class='lab'>开场白</div><div class='say'>{esc(x['opener'])}</div></div>")
    if r["customers"]:
        out.append("<h2>今天聊过的客户</h2><table>")
        for x in r["customers"]:
            lv = x["level"] if x["level"] in ("快成交", "推进中", "卡住了", "一般") else "一般"
            out.append(f"<tr><td>{who(x['c'])}</td><td>{esc(x['status'])}</td>"
                       f"<td><span class='tag t-{lv}'>{lv}</span></td></tr>")
        out.append("</table>")
    out.append("<div class='foot'>语音、图片、文件和通话内容 AI 看不到，只看文字。这份复盘只发给你本人。</div>")
    out.append("</div></body></html>")
    return "".join(out)


# ---------------------------------------------------------------- 主流程
def ping():
    text, u = call_claude("你是一个测试助手。", "只回复两个字：正常")
    log(f"ping ok, model={MODEL}, 回复长度={len(text)}, {usage_line(u)}")


def main():
    if MODE == "ping":
        ping()
        return
    if not TOKEN:
        log("缺 CHAT_REVIEW_TOKEN")
        sys.exit(1)

    params = {"token": TOKEN}
    if WINDOW_END:
        params["end"] = WINDOW_END
    resp = requests.get(f"{BASE_URL}/chatReview/export", params=params, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        log(f"导出失败: {data.get('msg')}")
        sys.exit(1)
    ws, we = data["windowStart"], data["windowEnd"]
    planners = data["planners"]
    log(f"窗口 {ws} ~ {we}, 规划师 {len(planners)} 位")

    results, team_input, total_cost = [], [], 0.0
    todo = []
    for p in planners:
        if ONLY and p["vxId"] not in ONLY:
            continue
        drop_broadcast_only(p)
        p["msgCount"] = sum(len(c["today"]) for c in p["customers"])
        if p["msgCount"] < MIN_MESSAGES:
            log(f"{p['vxId']}: 窗口内 {p['msgCount']} 条, 少于 {MIN_MESSAGES} 条, 跳过")
            continue
        todo.append(p)

    def analyze(p):
        t0 = time.time()
        try:
            text, u = call_claude(SYSTEM_PROMPT, build_user_prompt(p, ws, we), schema=PLANNER_SCHEMA)
            return p, json.loads(text), u, time.time() - t0
        except Exception as e:  # 一人失败不影响其他人
            log(f"{p['vxId']}: 分析失败 {type(e).__name__}: {str(e)[:200]}")
            return p, None, None, time.time() - t0

    # 每人一次调用互不依赖, 并行跑(串行时一人约 7 分钟, 4 人 23 分钟)
    with ThreadPoolExecutor(max_workers=4) as pool:
        done = list(pool.map(analyze, todo))

    for p, r, u, secs in done:
        if r is None:
            continue
        total_cost += cost_usd(u)
        log(f"{p['vxId']}: {len(p['customers'])} 户/{p['msgCount']} 条, 用时 {secs:.0f}s, 轮次 {u.get('num_turns')}, "
            f"改进{len(r['improve'])} 好{len(r['good'])}, {usage_line(u)}")
        results.append({"vxId": p["vxId"], "name": p["name"], "push": name_unarchived(r["push"]),
                        "html": name_unarchived(render_page(p, r, ws, we)),
                        "customerCount": len(p["customers"]), "msgCount": p["msgCount"]})
        team_input.append({"规划师": p["name"], "客户数": len(p["customers"]), "消息数": p["msgCount"],
                           "overview": r["overview"], "improve": r["improve"],
                           "good": r["good"], "tomorrow": r["tomorrow"]})

    if not results:
        log("没有可复盘的规划师, 结束(不推送)")
        return

    # 崔伟 9-21: 他只要一条「所有人完整复盘链接」, 不要团队汇总 → 不再调团队汇总
    team_push = ""

    payload = {"windowStart": ws, "windowEnd": we, "testVxId": TEST_VXID,
               "planners": results, "team": {"push": name_unarchived(team_push)}}
    up = requests.post(f"{BASE_URL}/chatReview/upload",
                       data={"token": TOKEN, "force": str(FORCE).lower(), "payload": json.dumps(payload, ensure_ascii=False)}, timeout=180)
    up.raise_for_status()
    body = up.json()
    log(f"回传: code={body.get('code')} msg={str(body.get('msg'))[:200]} sent={body.get('sent')}")
    log(f"本次用量折合 API 牌价约 ${total_cost:.2f}(走订阅, 不另扣费)")
    if body.get("code") != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
