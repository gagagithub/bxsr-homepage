"""你答应过的联系 —— 2026-09-29 崔伟定：规划师在聊天里说「您先看两天，我周三再联系您」这类话，自动识别出日期，在「AI推荐」页提醒。

随 AI推荐 02:00 一起跑（工作日才跑）：
  窗口 = 上一个工作日 + 其后的休息日（和聊天复盘同口径），逐天 GET /chatReview/export 合并
  → 规划师消息里带时间字眼的客户才交给 Claude（每位规划师一次调用）
  → 只收规划师本人说的、带具体时间的承诺，换算成具体日期；没说时间的不编
  → POST /aiRecommend/promiseUpload 存 t_contact_promise；后台到期那天起看到规划师联系过就自动消掉
未建档客户（没有客户编号）不进。

⛔本仓库 PUBLIC: 只打印条数/耗时, 不打印聊天和原话。
"""
import datetime
import json
import re
from concurrent.futures import ThreadPoolExecutor

import requests

# 规划师消息里有这些字眼才值得让 Claude 看（宽一点，是不是承诺由 Claude 判断）
TIME_WORDS = re.compile(r"明天|明早|明晚|后天|今晚|今天(上午|中午|下午|晚上|晚些)|晚点|周[一二三四五六日天末]|星期|礼拜|下周|下个?月|"
                        r"\d{1,2}[号日]|\d{1,2}[月/.]\d{1,2}|[一二三四五六七八九十]+号|节后|假期|国庆|中秋|元旦|春节|月初|月中|月底|"
                        r"这两天|过两天|两三天|改天|到时候|回头|之后再|再联系|再跟您|再给您|再找您|再约")

WEEK = "一二三四五六日"

PROMPT = """你在帮保险经纪公司「保心上人」的规划师整理「自己在聊天里答应客户的下一次联系时间」，系统会在那天提醒他。今天是 {today}（周{wd}）。

下面是规划师和几位客户的企业微信聊天（「规」= 规划师本人，「客」= 客户；时间格式 MM-dd HH:mm，年份 {year}）。

只找规划师本人明确说出的、带具体时间的下一次联系承诺，例如：
「您先看两天，我周三再联系您」「明天上午给您打电话」「节后我把计划书发您」「下周一我再跟您确认」「晚上我语音跟您说」「10 号前我把测算发您」。

规则：
1. 必须是规划师自己说的、自己要做的事，而且有具体时间（今天几点/今晚/明天/后天/周几/下周几/几号/节后/月初……）。
   「回头联系」「有空再聊」「过几天」「有问题随时找我」「等您消息」这种没有具体时间、或者是等客户来找的，不算。
2. 客户自己说的时间（「我下周再看」「节后再说」）不算；但如果规划师接着答应了（「好的，节后我联系您」），算规划师的承诺。
3. 按规划师说这句话的那天推算出具体日期 due（yyyy-MM-dd）：
   今天/今晚 = 说话当天；明天 = +1；后天 = +2；这两天/过两天 = +2；
   周三 = 说话日之后最近的周三（说话当天就是周三、又说「周三」时指下周三）；下周三 = 说话日所在周的下一周的周三；
   节后/假期后 = 假期结束后的第一个工作日（看下面日历）；月初 = 下个月第一个工作日；几号 = 最近的那个几号。
   算不准就不要这一条。
4. 如果聊天里已经看到规划师在那个时间或之后兑现了（到时间又联系了客户、东西已经发了），这条不要。
   同一位客户说了好几次，只留最后一次还没兑现的。
5. quote 抄规划师那句话的原文（太长可以用…截短，不能改写）；said_at 是这句话的时间（MM-dd HH:mm，照抄聊天里的）；
   what 用 20 字以内写到时候要做什么（例：打电话讲计划书 / 发趸交测算 / 问公示结果）。
6. 不要编造，拿不准就不写。一条都没有就返回空数组。

日历（从聊天窗口第一天到今后 45 天，标出休息日；没标的是工作日）：
{calendar}
"""

SCHEMA = {"type": "object", "properties": {"promises": {"type": "array", "items": {
    "type": "object",
    "properties": {"c": {"type": "string"}, "said_at": {"type": "string"}, "quote": {"type": "string"},
                   "due": {"type": "string"}, "what": {"type": "string"}},
    "required": ["c", "said_at", "quote", "due", "what"], "additionalProperties": False}}},
    "required": ["promises"], "additionalProperties": False}


def window_days(today, workday_status):
    """今天 → 上一个工作日 + 其后的休息日（不含今天）。"""
    d = datetime.date.fromisoformat(today)
    days = []
    for _ in range(20):
        d -= datetime.timedelta(days=1)
        days.insert(0, d.isoformat())
        if workday_status(d.isoformat())[0]:
            break
    return days


def calendar(first, today, workday_status):
    d, end = datetime.date.fromisoformat(first), datetime.date.fromisoformat(today) + datetime.timedelta(days=45)
    lines = []
    while d <= end:
        ok, why = workday_status(d.isoformat())
        mark = "" if ok and why == "工作日" else f"  {why}"
        lines.append(f"{d.isoformat()} 周{WEEK[d.weekday()]}{mark}" + ("  ← 今天" if d.isoformat() == today else ""))
        d += datetime.timedelta(days=1)
    return "\n".join(lines)


def export_window(base_url, token, days, log):
    """逐天导出聊天复盘同款原文，按 规划师名 → 客户编号 合并（未建档 x 号丢掉）。"""
    planners = {}
    for day in days:
        end = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat() + " 00:00"
        r = requests.get(f"{base_url}/chatReview/export", params={"token": token, "end": end}, timeout=180)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"导出 {day} 失败")
        for p in data["planners"]:
            P = planners.setdefault(p["name"], {})
            for c in p["customers"]:
                if c["key"].startswith("x"):
                    continue
                old = P.get(c["key"])
                if old is None:
                    P[c["key"]] = {"key": c["key"], "today": list(c["today"])}
                else:
                    old["today"] += c["today"]
    log(f"承诺: 导出 {len(days)} 天, 规划师 {len(planners)} 位")
    return planners


def run(today, planners_by_id, base_url, token, call_claude, workday_status, log, only=None):
    """返回要上传的承诺列表 [{uid,cid,saidAt,quote,what,due}]。"""
    days = window_days(today, workday_status)
    chats = export_window(base_url, token, days, log)
    cal = calendar(days[0], today, workday_status)
    year = today[:4]
    wd = WEEK[datetime.date.fromisoformat(today).weekday()]
    system = PROMPT.format(today=today, wd=wd, year=year, calendar=cal)
    name_to_uid = {v: k for k, v in planners_by_id.items()}

    jobs = []
    for name, custs in chats.items():
        uid = name_to_uid.get(name)
        if uid is None or (only and str(uid) not in only):
            continue
        cand = [c for c in custs.values()
                if any(m["who"] == "规" and TIME_WORDS.search(m["text"]) for m in c["today"])]
        if cand:
            jobs.append((uid, name, cand))

    def one(job):
        uid, name, cand = job
        text = "\n".join(
            f"===== 客户 {c['key']} =====\n" + "\n".join(f"{m['t']} {m['who']}：{m['text']}" for m in c["today"])
            for c in cand)
        try:
            out, _ = call_claude(system, f"规划师：{name}\n\n{text}\n\n请按要求输出。", SCHEMA)
        except Exception as e:
            log(f"承诺 {uid}: 失败 {type(e).__name__}: {str(e)[:150]}")
            return []
        byc = {c["key"]: c for c in cand}
        res, bad = [], 0
        for p in out["promises"]:
            c = byc.get(str(p["c"]).replace("{", "").replace("}", "").replace("c:", "").strip())
            if not c or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p["due"]) or not re.fullmatch(r"\d{2}-\d{2} \d{2}:\d{2}", p["said_at"]):
                bad += 1
                continue
            said = f"{year}-{p['said_at']}"
            if said[:10] > today:   # 跨年窗口(12 月说、1 月跑)
                said = f"{int(year) - 1}-{p['said_at']}"
            q = p["quote"].replace("…", "").replace("...", "")
            # 原话核对只报警不丢(见 feedback_no_auto_content_gates)
            if not any(m["who"] == "规" and q[:12] in m["text"] for m in c["today"]):
                log(f"承诺 {uid}: 有 1 条原话在聊天里没对上")
            res.append({"uid": uid, "cid": int(c["key"]), "saidAt": said + ":00", "quote": p["quote"],
                        "what": p["what"], "due": p["due"]})
        log(f"承诺 {uid}: 候选 {len(cand)} 户 → {len(res)} 条" + (f", 格式不对丢 {bad}" if bad else ""))
        return res

    with ThreadPoolExecutor(max_workers=4) as pool:
        got = [x for r in pool.map(one, jobs) for x in r]
    return got


def upload(base_url, token, promises):
    r = requests.post(f"{base_url}/aiRecommend/promiseUpload",
                      data={"token": token, "payload": json.dumps({"promises": promises}, ensure_ascii=False)}, timeout=120)
    r.raise_for_status()
    return r.json()
