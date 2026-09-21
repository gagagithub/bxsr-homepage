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

import requests

BASE_URL = os.environ.get("CHAT_REVIEW_BASE", "https://214club.com.cn")
TOKEN = os.environ.get("CHAT_REVIEW_TOKEN", "")
MODE = (os.environ.get("MODE") or "run").strip()
TEST_VXID = (os.environ.get("TEST_VXID") or "").strip()
ONLY = [s.strip() for s in (os.environ.get("ONLY") or "").split(",") if s.strip()]
WINDOW_END = (os.environ.get("WINDOW_END") or "").strip()

MODEL = "claude-opus-5"
# 规划师窗口内和客户来往少于这么多条就不出复盘(没东西可说, 硬写只会是空话)
MIN_MESSAGES = 6


SYSTEM_PROMPT = """你是「保心上人」保险经纪团队的销售教练。每天傍晚，你把一位规划师过去 24 小时和客户的企业微信 1 对 1 聊天全部读一遍，告诉他哪里做得不好、换成怎么说更好，明天先联系谁。

【公司背景】
- 规划师通过企业微信服务客户，客户多为 45–70 岁、手里有一笔闲钱的人。
- 卖两类产品：内地保险（增额终身寿、年金、养老金、快返年金等）和香港保险（储蓄分红险，常见代号如「116」=一次性交一年后每年领 6%，「258」=两年交第五年领 8%）。香港保险要客户本人赴港签单，常涉及开香港银行账户、资金出境。
- 客户状态：需求了解状态 → 方案讲解状态 → 已成交状态。

【先查风险：这些话一旦说出口，就是投诉和监管的把柄】
1. 把非保证利益说成确定：分红、分红实现率、「历史 100% 实现」被说成等于写进合同 / 一定能拿到。正确说法是分清「合同保证的部分」和「靠分红、不保证的部分」。
2. 数字算不过来或说混：回本年限、收益、每月和每年混淆、客户理解错了没纠正。你要自己验算聊天里出现的数字。
3. 外汇：教客户虚报购汇用途（个人购汇额度不能用于境外买保险）、推荐或认可「对敲」、地下钱庄、找换店大额换汇等。
4. 税务、法律、理赔上的绝对化说法（「全部免税」「肯定能赔」「一定能继承」），或没核实就先下结论、之后又改口。
5. 承诺收益、贬低同行、夸大自己或公司资质。

【再看销售功夫】
- 客户问的问题有没有接住；客户的顾虑（怕税、怕汇率、嫌少、嫌公司小）是被认真回应，还是被一句话挡回去。
- 客户嫌少、犹豫时是继续挖需求、换方案，还是直接放弃（「那就没办法了」）。
- 客户给了信号（主动要方案、问流程、约见面、问签单要求）有没有抓住并约下一步。
- 一次发太长、客户看不下去；自我介绍、资料轰炸代替了提问。
- 客户主动发来的消息（包括转发的视频号、小程序、文章）有没有回。
- 需求问得好、推进得好的地方也要指出来，让他知道该保持什么。

【判断原则】
- 如实还原现场，不客套、不软化，也不夸大。每一条都要落到具体客户和聊天原话上，不写「要加强沟通」这类空话；没问题的客户不用硬挑毛病，条数宁少勿滥。
- 只评价「本次窗口」里的消息；「之前的聊天」只用来理解来龙去脉。
- 你看不到语音、图片、文件、通话内容，只知道发了什么类型。不要猜测这些内容，也不要因为看不到就判定规划师做错。
- 客户名字不在材料里，一律用占位符 {{c:编号}} 称呼客户（编号就是材料里每个客户标题上的那个编号，例如 {{c:33703}}），所有字段都这样写，包括 push。系统会自动换成客户微信名。
- 引用原话放在 quote 字段，要是聊天里的原文（太长可以用…截短），不要改写。
- 用「你」称呼规划师，语气像一个懂行、说话直接的老同事。

【输出字段】
- overview：两三句话，今天整体怎么样、最要紧的一件事是什么。
- risks：上面五类风险，逐条列出（没有就空数组）。problem 说清楚为什么有风险，fix 给出换成怎么说。
- improve：销售功夫上最该改的 2–5 条，better 写出具体可以照着说的话。
- good：今天做得好的 1–2 段，why 说明好在哪里、以后要保持。
- tomorrow：明天最该先联系的 2–4 个客户，why 说原因，opener 给一句可以直接发的开场白。
- customers：本次窗口里聊过的每个客户各一行，status 一句话说清楚这个客户现在走到哪一步，level 取「风险」「待改进」「正常」「亮点」之一。
- push：发到规划师企业微信的文字，300 字以内，纯文本不用 markdown。3–4 行：有风险先说最要紧的一条；再说最该改的一两条；最后说明天先联系谁。每行开头可用一个表情符号。"""


TEAM_PROMPT = """你是「保心上人」规划师团队的销售教练。下面是今天每位规划师聊天复盘的结构化结果（每人一份）。请写一段发给老板崔伟的团队汇总，要求：
- 纯文本，不用 markdown，600 字以内。
- 第一段只列需要崔伟亲自处理的合规风险（分红说成保证、外汇、绝对化承诺等），写清楚是谁、哪个客户、说了什么；没有就写「今天没有需要你处理的合规风险」。
- 然后每位规划师一行：姓名 + 今天最大的问题 + 今天最好的一段。不排名次，不评价勤奋与否。
- 最后一行：今天全队最值得跟进的 1–2 个客户（谁的、为什么）。
- 客户一律沿用 {{c:编号}} 占位符，不要改写。"""


PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "risks": {"type": "array", "items": {
            "type": "object",
            "properties": {"c": {"type": "string"}, "quote": {"type": "string"},
                           "problem": {"type": "string"}, "fix": {"type": "string"}},
            "required": ["c", "quote", "problem", "fix"], "additionalProperties": False}},
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
                           "level": {"type": "string", "enum": ["风险", "待改进", "正常", "亮点"]}},
            "required": ["c", "status", "level"], "additionalProperties": False}},
        "push": {"type": "string"},
    },
    "required": ["overview", "risks", "improve", "good", "tomorrow", "customers", "push"],
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
.t-风险{background:var(--redbg);color:var(--red)}.t-待改进{background:var(--amberbg);color:var(--amber)}
.t-亮点{background:var(--greenbg);color:var(--green)}.t-正常{background:var(--line);color:var(--sub)}
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
    if r["risks"]:
        out.append("<h2>⚠️ 有风险的说法</h2>")
        for x in r["risks"]:
            out.append(f"<div class='card risk'><div class='who'>{who(x['c'])}</div><div class='q'>{esc(x['quote'])}</div>"
                       f"<div class='lab'>问题</div><div>{esc(x['problem'])}</div>"
                       f"<div class='lab'>应该这样说</div><div class='say'>{esc(x['fix'])}</div></div>")
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
            lv = x["level"] if x["level"] in ("风险", "待改进", "正常", "亮点") else "正常"
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
    for p in planners:
        if ONLY and p["vxId"] not in ONLY:
            continue
        p["msgCount"] = sum(len(c["today"]) for c in p["customers"])
        if p["msgCount"] < MIN_MESSAGES:
            log(f"{p['vxId']}: 窗口内 {p['msgCount']} 条, 少于 {MIN_MESSAGES} 条, 跳过")
            continue
        t0 = time.time()
        try:
            text, u = call_claude(SYSTEM_PROMPT, build_user_prompt(p, ws, we), schema=PLANNER_SCHEMA)
            r = json.loads(text)
        except Exception as e:  # 一人失败不影响其他人
            log(f"{p['vxId']}: 分析失败 {type(e).__name__}: {str(e)[:200]}")
            continue
        total_cost += cost_usd(u)
        log(f"{p['vxId']}: {len(p['customers'])} 户/{p['msgCount']} 条, 用时 {time.time() - t0:.0f}s, "
            f"风险{len(r['risks'])} 改进{len(r['improve'])} 好{len(r['good'])}, {usage_line(u)}")
        results.append({"vxId": p["vxId"], "name": p["name"], "push": name_unarchived(r["push"]),
                        "html": name_unarchived(render_page(p, r, ws, we)),
                        "customerCount": len(p["customers"]), "msgCount": p["msgCount"]})
        team_input.append({"规划师": p["name"], "客户数": len(p["customers"]), "消息数": p["msgCount"],
                           "overview": r["overview"], "risks": r["risks"], "improve": r["improve"],
                           "good": r["good"], "tomorrow": r["tomorrow"]})

    if not results:
        log("没有可复盘的规划师, 结束(不推送)")
        return

    team_push = ""
    try:
        team_push, u = call_claude(TEAM_PROMPT, json.dumps(team_input, ensure_ascii=False))
        total_cost += cost_usd(u)
        log(f"团队汇总 ok, {usage_line(u)}")
    except Exception as e:
        log(f"团队汇总失败 {type(e).__name__}: {str(e)[:200]}")

    payload = {"windowStart": ws, "windowEnd": we, "testVxId": TEST_VXID,
               "planners": results, "team": {"push": name_unarchived(team_push)}}
    up = requests.post(f"{BASE_URL}/chatReview/upload",
                       data={"token": TOKEN, "payload": json.dumps(payload, ensure_ascii=False)}, timeout=180)
    up.raise_for_status()
    body = up.json()
    log(f"回传: code={body.get('code')} msg={str(body.get('msg'))[:200]} sent={body.get('sent')}")
    log(f"本次用量折合 API 牌价约 ${total_cost:.2f}(走订阅, 不另扣费)")
    if body.get("code") != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
