"""「这条不用回」点错抽查 —— 跟在聊天复盘后面跑(同一个 workflow), 但每天都跑、只看前一天(节假日也要回客户)。

崔伟 9-30: 没回提醒里规划师可以点「这条不用回」; 他要一个汇总, 但不要原话、不要全部,
只要 Claude 觉得点错了(其实该回)的; 都正常就不发。

生产 /replyRemind/skipExport 拉窗口内所有「不用回」+ 前后聊天(脱敏, 客户不带名字)
→ Claude 判断 → /replyRemind/skipReport 只回传判为该回的, 生产换回名字发崔伟。

⛔本仓库 PUBLIC: 只打印条数, 不打印聊天或判断内容。
"""
import datetime
import json
import sys

import requests

from review import BASE_URL, BJ, TEST_VXID, TOKEN, WINDOW_END, call_claude, log, usage_line

BATCH = 30

SYSTEM_PROMPT = """你在帮保险经纪公司老板抽查规划师的一个操作。

背景：客户在企业微信给规划师发了消息，规划师 30 分钟没回，系统会提醒他；提醒里有个「这条不用回」按钮，
点了这一轮就不再提醒，也不会抄送老板。你要判断：规划师点「不用回」是不是点错了——也就是这条客户消息其实应该回。

判断只从成交和客户关系的角度看（老板明确说过不要点评合规、措辞、头衔之类）。

算「点错了，应该回」的情况（举例，不限于此）：
- 客户在问问题、要资料、要方案、问价格/收益/条款/流程/理赔/缴费，还没得到答复
- 客户表达了顾虑、异议、犹豫、比较别家，或者流露出购买意向、加保意向
- 客户在等规划师兑现之前说过的事（发资料、约时间、给答复）
- 客户说了家里的变化（生病、住院、孩子、换工作、要用钱等）、情绪明显低落或不满，不接一句会伤关系
- 客户主动约时间、问什么时候方便

不算点错（不用报）：
- 客户在道歉、客气、收尾（好的/谢谢/辛苦了/抱歉打扰），对话已经自然结束
- 客户转发的内容、群发式消息、广告、和规划师无关的自言自语
- 规划师点完之后很快又回了客户、或打了电话（聊天里能看到）——结果上没丢
- 看不出客户在等什么、证据不够的——拿不准就不报

要求：
- 只输出你认为点错的那几条；都正常就输出空列表。宁缺毋滥，不要为了有内容而报。
- reason 用中文一到两句话，说清楚客户当时在等什么、为什么该回、之后怎样了（例如「规划师到现在也没回」「客户第二天又追问了一次」）。
- ⛔不要引用客户原话，用自己的话概括；不要出现手机号等个人信息；不要编聊天里没有的事实。
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "flagged": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["id", "reason"],
            },
        }
    },
    "required": ["flagged"],
}


def main():
    if not TOKEN:
        log("缺 CHAT_REVIEW_TOKEN")
        sys.exit(1)
    # 9-30 崔伟: 节假日规划师也要正常回客户 → 抽查每天都跑(不跟复盘的工作日口径), 只看前一天
    report_day = WINDOW_END[:10] if WINDOW_END else datetime.datetime.now(BJ).date().isoformat()
    days = [(datetime.date.fromisoformat(report_day) - datetime.timedelta(days=1)).isoformat()]
    resp = requests.get(f"{BASE_URL}/replyRemind/skipExport",
                        params={"token": TOKEN, "start": days[0], "end": report_day}, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        log(f"导出失败: {data.get('msg')}")
        sys.exit(1)
    items = data.get("items") or []
    log(f"窗口 {days[0]} ~ {report_day}: 「不用回」共 {len(items)} 条")
    if not items:
        return

    ids = {it["id"] for it in items}
    flagged = []
    for i in range(0, len(items), BATCH):
        batch = items[i:i + BATCH]
        user = ("下面是规划师点了「不用回」的记录，每条有 id、规划师、客户开始等的时间、点的时间，"
                "以及前后聊天（中间有一行标出点「不用回」的时刻）。逐条判断，只输出点错的。\n\n"
                + json.dumps(batch, ensure_ascii=False, indent=1))
        text, u = call_claude(SYSTEM_PROMPT, user, schema=SCHEMA)
        got = [f for f in json.loads(text)["flagged"] if f.get("id") in ids and f.get("reason")]
        flagged += got
        log(f"第 {i // BATCH + 1} 批 {len(batch)} 条, 判为点错 {len(got)} 条, {usage_line(u)}")

    if not flagged:
        log("都正常, 不发")
        return
    up = requests.post(f"{BASE_URL}/replyRemind/skipReport",
                       data={"token": TOKEN, "payload": json.dumps({"flagged": flagged, "testVxId": TEST_VXID},
                                                                    ensure_ascii=False)}, timeout=180)
    up.raise_for_status()
    body = up.json()
    log(f"回传: code={body.get('code')} count={body.get('count')} sent={body.get('sent')}")
    if body.get("code") != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
