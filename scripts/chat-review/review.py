"""规划师聊天复盘 —— 每天北京 12:00 由生产服务器 workflow_dispatch 触发。

⭐9-28 崔伟定: 按工作日切, 不再按「前 24 小时」。工作日中午出一份, 范围=上一个工作日 0 点 → 今天 0 点
(即上一个工作日整天 + 后面连着的休息日; 例: 9-28 周一 = 9-24 + 9-25~27 中秋, 10-08 = 9-30 + 国庆 7 天)。
休息日不出(挪到节后第一个工作日), 调休上班照出; 工作日口径与 AI 推荐同一份 holiday-cn。
生产 /chatReview/export 只会导 24 小时 → 这里按天逐天拉(end=次日 0 点), 在 Python 端合并, 服务器不用换包。

生产导出的 1 对 1 聊天(已脱敏: 客户只有编号, 手机号/证件号已打码)
→ DeepSeek(V4.1-Flash API, 按量付费) 每位规划师出一份复盘(只从成交角度), 发本人; 崔伟只收一条全员完整复盘链接
→ 回传生产 /chatReview/upload, 生产把 {{c:编号}} 换回客户昵称、存完整页、夏梅推送。

⭐2026-10-11 崔伟定: 从 Claude(claude-opus-5, Claude Code CLI+订阅) 迁到 DeepSeek V4.1-Flash(API), 不再依赖 Claude Code。
- 结构化输出: Claude CLI 的 --json-schema 换成 schema 写进系统提示 + json_object 模式, 拿回来自行校验、不合格重试。
- 图片: 原来 Claude 用 Read 工具读存下来的图片文件; 现在把图片 base64 直接附在消息里(多模态), [图片#n] = 第 n 张。

⭐10-10 崔伟(林付贤一对一后)定:
- 复盘要看图片: export 给已存图片编号 → /chatReview/media 取原图(缩到 1600px)存临时目录, 附在消息里给模型看。
- export 带「企微已拉黑」、每户手填跟进记录(多为电话)、只打了电话没在企微聊的客户、当天 AI 推荐/到期约定联系的覆盖情况。
- 不凑数: 只挑深入沟通的; 同类问题合并一条; 深聊少就查空余时间用在哪(coverage)。

⛔本仓库 PUBLIC, Actions 日志人人可看: 这里只打印条数/耗时/token, 绝不打印聊天或分析内容, 图片只存 runner 临时目录不传 artifact。
"""
import base64
import datetime
import html
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import requests

BASE_URL = os.environ.get("CHAT_REVIEW_BASE", "https://214club.com.cn")
TOKEN = os.environ.get("CHAT_REVIEW_TOKEN", "")
MODE = (os.environ.get("MODE") or "run").strip()
TEST_VXID = (os.environ.get("TEST_VXID") or "").strip()
ONLY = [s.strip() for s in (os.environ.get("ONLY") or "").split(",") if s.strip()]
# 补跑用: 填「出复盘的那一天」yyyy-MM-dd(兼容旧格式 yyyy-MM-dd HH:mm, 只取日期), 留空=今天(北京)
WINDOW_END = (os.environ.get("WINDOW_END") or "").strip()
FORCE = (os.environ.get("FORCE") or "").strip().lower() == "true"  # 同日补跑: 绕过 sent-<日期> 标记

MODEL = "deepseek-flash"   # 2026-10-11: Claude Opus 5 → DeepSeek V4.1-Flash(带思考, 支持看图)
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MAX_TOKENS = 64000         # ⚠推理模型: 思考(reasoning_tokens)和正文共用这个额度。实测单次可输出 4.6 万 token(finish=stop);
                           #    10-11 首跑大批次顶到 32000 上限, 调成 64k 留足余量
# 规划师窗口内和客户来往少于这么多条就不出复盘(没东西可说, 硬写只会是空话)
MIN_MESSAGES = 6
BROADCAST_MIN = 20  # 同一句话发给 ≥20 户视为群发
# 一人材料超过这么多字就从最早一天开始丢(9-22 林付贤 200 万字直接报 Prompt is too long; 5 万字正常)
# 实测 DeepSeek V4.1 上下文 ≥30 万 token(约 45 万字), 300k 字这个闸原样保留; analyze() 里另有超限重试兜底
MAX_CHARS = 300_000
BJ = datetime.timezone(datetime.timedelta(hours=8))
MAX_IMAGES = 40   # 每位规划师最多给模型看这么多张图(窗口内的优先)
IMG_MAX_SIDE = 1600


def get_key():
    """本机跑时从 baoxin 的 application-dev.yml 取 DeepSeek key(和财经脚本同款); Actions 里用 DEEPSEEK_API_KEY。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "..", "..", "service", "src", "main", "resources", "application-dev.yml"),
              os.path.expanduser("~/cuiwei_ai/baoxin/service/src/main/resources/application-dev.yml")):
        try:
            m = re.search(r"apiKey:\s*(sk-[A-Za-z0-9_\-]+)", open(p, encoding="utf-8", errors="ignore").read())
            if m:
                return m.group(1)
        except Exception:
            pass
    return None


DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY") or get_key()

_HOLIDAYS = {}


def workday_status(day):
    """(是否工作日, 说明)。与 ai-recommend/recommend.py 同口径: 国务院放假安排(holiday-cn),
    法定假日休、调休上班算工作日、其余周六日休; 拉不到表就只按周末判。"""
    year = day[:4]
    if year not in _HOLIDAYS:
        _HOLIDAYS[year] = None
        for url in (f"https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json",
                    f"https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json"):
            try:
                _HOLIDAYS[year] = {d["date"]: d for d in requests.get(url, timeout=20).json()["days"]}
                break
            except Exception as e:
                log(f"节假日表拉取失败 {url.split('/')[2]} {type(e).__name__}")
    d = (_HOLIDAYS[year] or {}).get(day)
    if d:
        return (not d["isOffDay"], f"{d['name']}{'调休上班' if not d['isOffDay'] else '休息'}")
    wd = datetime.date.fromisoformat(day).weekday()
    if wd >= 5:
        return (False, "周六" if wd == 5 else "周日")
    return (True, "工作日")


def window_days(report_day):
    """出复盘那天 → 要分析的日期列表(上一个工作日 + 其后的休息日, 不含当天)。最多往回找 20 天。"""
    d = datetime.date.fromisoformat(report_day)
    days = []
    for _ in range(20):
        d -= datetime.timedelta(days=1)
        days.insert(0, d.isoformat())
        if workday_status(d.isoformat())[0]:
            return days
    return days


def merge_exports(exports, days=None):
    """多天导出合并: 同一规划师同一客户(已建档, 编号=客户 id)拼成一户; history 取最早那天的(真正的「之前」)。
    未建档客户 x 编号每天各自从 1 数, 跨天对不上 → 统一重新编号, 同一人跨天会算两户(罕见, 可接受)。"""
    planners, xseq = {}, 0
    for data in exports:
        for p in data["planners"]:
            P = planners.setdefault(p["vxId"], {"vxId": p["vxId"], "name": p["name"], "customers": [], "_idx": {},
                                                "phoneOnly": [], "coverage": []})
            if p.get("coverage") is not None:
                P["coverage"].append(dict(p["coverage"], day=data.get("_day", "")))
            for c in p.get("phoneOnly") or []:
                if not any(x["key"] == c["key"] for x in P["phoneOnly"]):
                    P["phoneOnly"].append(c)
            for c in p["customers"]:
                if c["key"].startswith("x"):
                    xseq += 1
                    c["key"] = f"x{xseq}"
                old = P["_idx"].get(c["key"])
                if old is None:
                    P["_idx"][c["key"]] = c
                    P["customers"].append(c)
                else:
                    old["today"] += c["today"]
                    for k in ("state", "appeal", "dealState", "addDate", "blacklisted", "connects"):
                        if c.get(k):
                            old[k] = c[k]
    for P in planners.values():
        P.pop("_idx")
        chat_keys = {c["key"] for c in P["customers"]}
        P["phoneOnly"] = [c for c in P["phoneOnly"] if c["key"] not in chat_keys]
    return list(planners.values())


def trim_to_fit(p, limit=None):
    """材料太长时从最早一天开始丢本次窗口的消息, 直到放得下(整份出不来比少看两天更糟)。
    消息时间 t 形如 MM-dd HH:mm。limit 默认 MAX_CHARS; 超上下文重试时传更小的值。"""
    limit = limit or MAX_CHARS
    def size():
        return sum(len(m["text"]) + 16 for c in p["customers"] for m in c["today"] + c.get("history", []))
    dropped = []
    while size() > limit:
        days = sorted({m["t"][:5] for c in p["customers"] for m in c["today"]})
        if len(days) <= 1:
            break
        dropped.append(days[0])
        for c in p["customers"]:
            c["today"] = [m for m in c["today"] if m["t"][:5] != days[0]]
        p["customers"] = [c for c in p["customers"] if c["today"]]
    if dropped:
        p["trimmed"] = dropped
        log(f"{p['vxId']}: 材料超长, 丢掉最早的 {', '.join(dropped)}")


def fetch_images(p, root):
    """把窗口内(其次是之前的聊天里)已存的图片下到 root/<vx>/, 消息文字改成 [图片#n], 返回目录和张数。
    下不到的保持 [图片](按看不到处理)。图片之后会 base64 附进请求, 图多时压得更狠, 别堆成巨无霸。"""
    d = os.path.join(root, re.sub(r"[^A-Za-z0-9_-]", "_", p["vxId"]))
    os.makedirs(d, exist_ok=True)
    cand = [m for c in p["customers"] for m in c["today"] if m.get("img")]
    cand += [m for c in p["customers"] for m in c.get("history", [])[-4:] if m.get("img")]
    n, tot = 0, 0
    for m in cand:
        if n >= MAX_IMAGES:
            break
        try:
            r = requests.get(f"{BASE_URL}/chatReview/media", params={"token": TOKEN, "id": m["img"]}, timeout=60)
            if r.status_code != 200 or not r.content:
                continue
            n += 1
            fn = os.path.join(d, f"{n}.jpg")
            try:
                from io import BytesIO
                from PIL import Image
                im = Image.open(BytesIO(r.content)).convert("RGB")
                if tot > 8_000_000:
                    im.thumbnail((1000, 1000))
                    im.save(fn, "JPEG", quality=72)
                else:
                    im.thumbnail((IMG_MAX_SIDE, IMG_MAX_SIDE))
                    im.save(fn, "JPEG", quality=82)
            except Exception:
                with open(fn, "wb") as f:
                    f.write(r.content)
            tot += os.path.getsize(fn)
            m["text"] = f"[图片#{n}]"
        except Exception as e:
            log(f"{p['vxId']}: 取图失败 {type(e).__name__}")
    return d, n


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


SYSTEM_PROMPT = """你是「保心上人」保险经纪团队里一位成交经验很丰富的老规划师，也是大家的成交教练。每个工作日中午，你把一位规划师上一个工作日（连同后面连着的休息日，可能是好几天）和客户的企业微信 1 对 1 聊天全部读一遍，只从「怎么把单子往成交推」的角度，告诉他哪里可以做得更好、换成怎么说，明天先联系谁。

【公司背景】
- 规划师通过企业微信服务客户，客户多为 45–70 岁、手里有一笔闲钱的人。
- 卖两类产品：内地保险（增额终身寿、年金、养老金、快返年金等）和香港保险（储蓄分红险，常见代号如「116」=一次性交一年后每年领 6%，「258」=两年交第五年领 8%）。香港保险要客户本人赴港签单，常涉及港澳通行证、香港银行账户、资金过去的方式。
- 客户状态：需求了解状态 → 方案讲解状态 → 已成交状态。

【第一步：先把事实定准（最重要）】
- 先给每个客户定背景，再评价任何一句话：新客户 / 老客户重新联系 / 删除或拉黑后重新加回 / 长期沉默后回来 / 已成交客户 / 看过很多方案、反复比较的老客户。依据是加好友日期、「之前的聊天」、「本次之前已 N 天没聊过」、客户状态和成交情况。同一个动作放在不同背景下对错完全不同：新加的高意向客户做好计划书只问「现在有空吗」可能是慢了；但一个以前因为直接发方案把规划师拉黑、这次重新加回来的客户，先约讲解再发方案恰恰是对的。
- 你看到的历史只有最近几条，加好友很早、历史却很少或断档很久的客户，很可能有你看不到的过往（多轮比较过、拒绝过、拉黑过）。背景看不全时，结论要降级：写「如果这是新客户……；如果之前已经沟通过，这条忽略」，不要按新客户的标准直接判慢了、错了。
- 评价语气、追问、反驳、客户压力时，必须按原始先后顺序读完整的连续消息，连同中间的解释和铺垫一起看；不能只挑出几句问号句拼在一起（那样正常的解释会被读成「三连问逼客户」），也不能调换顺序（先答「买保险和偷税没关系」再问「你从哪听到的」，和先反问再否定，意思完全不同）。quote 引原话时也保持原顺序，截短可以，重新组合不行。
- 判断顺序固定：先读这位客户的完整时间线 → 判断他现在处在哪个阶段 → 判断眼前这句话承接的是哪个话题 → 再判断规划师有没有做错 → 最后才给下一步。不能拿一句话单独下判断，再往前后推演出一个并不存在的问题。
- 客户的基础信息前后必须一致：客户说自己 72 岁，规划师回复或跟进里写成 75 岁；客户说 20 万，方案按 30 万做——这类是明确问题，要指出，并提醒核对计划书当时实际用的是哪个数。但「文字里没看到客户说过」（比如性别）不等于记错或没确认，企微资料、电话里可能早就有了：写「当前文字聊天中未看到性别确认；如果企微资料、电话或其他渠道已经确认，这条忽略」，不要写「你没有确认性别」。
- 分清三种金额：客户说的预算区间、客户明确确认的投保金额、规划师拿来演示的金额。客户说「100–200 万都可以」，先按 100 万做示例方便看懂比例和领取，不是「主动砍掉一半保费」。
- 客户明确说「不做」之后，规划师只发一个「？」「在吗」，很多时候是在确认自己有没有被删除或拉黑（企业微信发不出去会提示），不是催客户回复，不要评成「不耐烦」「催促」。
- 客户提出顾虑，规划师先答（「买保险和偷税没关系」）、再问「您从哪听到的」，是先回应再了解他具体担心什么，属于正常顺序，不要评成「先否定再反问」「回答顺序反了」。能提的只是：这类税务、换汇问题少用绝对说法。
- 客户一句礼貌话（「谢谢指导」「好的我考虑一下」）不等于关闭对话，没有后续证据就不要写「客户已经收尾」。
- 反过来，「你说得对」「明白了」「确实」「谢谢」「那算了」「这个不适合」「我知道了」这类话，常常是在确认上一轮结论、结束一个支线话题，不是提出新顾虑。不能只抓里面的负面词（「收益确实不高而且有风险」）当成新异议、再要求规划师回头处理。先往前看：它在回应哪个话题（可能是客户临时问的另一款产品，规划师已经解释过不适合他）？是在提问，还是在认同？如果是认同、话题已结束，规划师接着推进原来的主线就是正常的。拿不准时写「这句可能是在总结前面的讨论，如果是这样这条忽略」。
- 对客户心理只能说可能性：写「可能让客户有压力」「有被理解成反驳的风险」，不要写「客户看了只会有压力」「节后就凉了」，并且这是风险判断，不是已经发生的事实。
- 事实和因果分开：「客户 10:15 的问题记录里没看到直接回答」是事实，可以说；「这就是客户唯一的卡点」「客户就是因为这个没成交」「这句话导致客户流失」是因果，除非证据链非常完整，否则不写。客户往往同时有几个顾虑（信任、偏好的产品在当地办不了……），写成「这是一个明确存在的卡点之一」「从他后面连续追问看，这个问题分量不轻」。事实可以确定，因果要保守。
- 你手上只有企业微信的文字记录，而且会漏：看不到手机电话、腾讯会议、面谈、私人微信，看不到语音/图片/文件里的内容，看不到窗口结束之后的消息，系统偶尔还会漏抓个别消息。
- 所以「看不到」绝不等于「没做」。「没回复」「没给方案」「没追」「没约时间」「没问通行证」这类事实性结论，门槛要比策略建议高得多：只要错一条，规划师就不再信整份复盘。
- 写这类结论时一律说「当前记录里没看到……」，不要说「你没有……」，并且带上可核对的证据：客户几点说了什么、记录里最后一条规划师消息是几点、说的什么。例：「客户 09-22 17:10 问『这是啥』，记录里之后没看到你的消息；如果已经回了或打了电话，这条忽略。」
- 记录里有通话（[语音通话 N 秒]）、聊天里提到「刚才电话里」「会议上说的」「上次见面」，说明很多事已经在线下谈过，文字里没出现的不要当成没谈。
- 区分「当时可以做得更好」和「现在有没有补救」：规划师后面已经补了动作（比如已经追问「明天上午还是下午」），就不能再说他没追，只能说当时那一步可以更早。
- 图片现在能看：聊天里写成 [图片#n] 的图片已按编号附在你收到的消息里（第 n 张就是 [图片#n]）。客户提问后规划师发的图片、规划师说「你看我发的图」「你再理解一下这个图片」时，必须先看图里有没有回答；图里答了就不能判「没回答」「没解释」。规划师发的计划书、对比表截图，客户发来的保单/资料截图都要看，表情包类的不用管。
- 客户的追问本身建立在误解上（比如没看懂一张表的口径，把「按销售年份分批统计」当成「每年给产品打分」），规划师重发带标注的图、再约电话或视频讲，是合理做法，不判「明确问题」「没正面回答」（10-10 崔伟定，34741）。最多归「可优化」：文字里先用一句话把客户理解偏的那一点掰过来，再约电话。
- 只写 [图片]（没有编号）、[文件]、[语音] 的看不到，答案很可能就在里面。这时不能写「没给数字」「没解释」，只能写「答案可能在 09-24 11:51 发的图片里，看不到，请你自己确认」，evidence 标「需确认」。
- 时间点也别机械卡：规划师说「等明天再约」，窗口截止前还没约，不算拖延；很多客户有固定方便的时段（比如只在下午看微信），规划师比你清楚。
- 客户只回一个「哦」「收到」「嗯」，是弱信号，不要翻译成「嫌低」「没兴趣」；年龄、预算、用途都还没问到时，结论只能是「信息不足，先补齐」。

【第二步：判断客户现在在哪个阶段】
- 阶段大致是：了解产品 → 处理顾虑 → 比较方案 → 做决定 → 办手续执行。先判断阶段，再给这个阶段该做的动作，不要每个热客户都机械地套「问通行证 → 约时间 → 逼单」。
- 客户还在问「收益怎么来的」「这条合同什么意思」「风险在哪」，就是还在理解阶段：先把问题讲透，这时强行问通行证、约签单反而把人推走。客户开始问「要准备什么」「什么时候过去」「钱怎么交」「额度够不够」，才是转入执行，这时要把下一步定死。
- 分清「客户不愿意」和「客观条件暂时不允许」：单位审批没下来、通行证还在办、钱还没到期，这时锁不了签单日期，但可以锁一个检查节点（例：「单位批下来第一时间告诉我；周五前还没消息我们再碰一下」）。有外部等待事项、时间区间、下一个联系点的，已经是闭环，不要评成「聊完就散」，最多建议在微信里留一句文字把节点写下来。

【你要看的：一切为了成交】
- 成交信号有没有抓住：客户说「你帮我选一款」「给我出个方案」「哪天过来」、问签单流程和要求，这些是最该抓的时刻；规划师有没有当场把下一步定死（出什么方案、什么时候给、要客户准备什么、约哪天见）。
- 关键信息有没有问到：年龄、预算、这笔钱的用途（自己领钱还是留给孩子）、港澳通行证、香港账户、谁做投保人和被保人。缺了这些方案做不出来，单子就停住。
- 客户的顾虑有没有接住：怕税、怕汇率、嫌领得少、嫌公司小、犹豫时，是认真一条条解答、把顾虑变成推进的理由，还是一句话挡回去、或者直接放弃、或者顾虑没解决就逼单。
- 专业意见给得对不对：投保架构（投保人、被保人、受益人怎么安排才贴合客户目的）、产品和需求是否匹配、客户的预期和能买到的差太远时有没有先把预期拉回来、客户对产品的理解有偏差时有没有顺势讲清楚。
- 沟通节奏：一次发太多、连问好几个问题让客户不知道先答哪个；客户回了一句就没下文；客户主动发来的消息没回。
- 客户说「收益低」「不做」，先看他是怎么算的：如果他算得没错、产品长期真实回报确实不高，那就是产品对他吸引力不够，不是「客户没看懂」；这时该建议的是问清资金真实用途和他要多高的收益才肯搬钱、换一类方案或者坦白说暂时没有合适的，而不是把客户「教育回来」。
- 客户抛了一个具体问题（税、保全、资料变更、服务流程）之后没回复，不能当成「没需求」。先看他的问题有没有被正面回答；如果规划师后面只在问「还买不买」「要不要合作」，问题就出在这里：客户的未解决问题优先于确认购买意愿。
- 售后、异常类问题（发票、扣款、保单信息不对、到账、资料变更）：顺序是先收证据（让客户发图、核对具体票据/单号）→ 再判断原因 → 最后才解释规则。凭经验先讲「制度就是这样」，等看了证据发现确实有问题，客户会觉得你没认真听，已成交客户的信任就是这样丢的。
- 已成交客户来办服务（CRS/资料变更、保全、理赔）时顺带提到银行或别家也在推保险，只是弱线索，先把服务闭环，再轻轻探一句有没有真感兴趣；别升级成「明确加保信号」催规划师推产品。
- 客户拿大公司比规划师推荐的小公司，诚实承认体量差异没错，但话术要完整：承认事实 → 拉回客户自己的需求（比如偏短交）→ 说清为什么这款更匹配。别让话停在「对方更大」。
- 客户已经在谈公司补贴/优惠、问「有没有优惠」，说明离下一步很近：这时不要再讲产品，先确认「这个条件满足了，其他方面是不是就没问题了」，把优惠和下一步动作绑在一起，别开放式地一轮轮问「够不够」。
- 「额外优惠」「公司补贴」是公司层面的安排，不是规划师个人返佣，照成交推进的角度看就行。
- 不要替客户过早下判断：规划师凭经验一看到障碍（额度不够、预算小、家里有人反对）就先放弃客户感兴趣的那条路、转去推别的产品，要问一句这是客观上走不通，还是规划师替客户判了死刑。先把各方案差在哪、各自解决什么讲透，客户真认可了，再一起看障碍能不能解决。
- 客户要方案 ≠ 永远先出方案。建议「先发计划书」「先做一份示例方案」「先做一张对比表」之前，先检查年龄、大概预算、这笔钱的用途（养老/留给孩子/短期要用/长期增值）、产品方向、关键偏好（保证还是能接受分红浮动、流动性）够不够做出一份有针对性的方案。明显不够时——哪怕客户说了「年龄金额你设定」——随便定个年龄金额做出来的只是一份猜测，这时建议的是缩小问题，而不是替客户选好产品。
- 开放式问题（「您的需求是什么」「把您的情况说一下」「您倾向什么类型」）客户不回，下一轮要降低回复成本：变成一句话、二选一、一个数字，例如「这笔钱主要考虑自己以后用，还是以后留给孩子？回 A 或 B 就行」「您更看重保证收益，还是能接受部分分红浮动？回『保证』或『分红』就行」「这笔钱准备放十年八年，还是三五年可能要用」。优先级是：缩小问题 > 自动做方案。但低门槛不等于不做需求分析，需求还是要一层层拿到（偏好 → 年龄 → 预算 → 方案），别变成客户问一个数就只报一个数的报价机器。
- 结果导向型客户（已经给了年龄、性别、金额、交法，明确问「能领多少」）：先回答他问的核心数字（可以按已知条件先给一个，说明是按什么假设算的），再说明为什么还要补一个信息，一次只问一个关键问题。客户嫌「啰嗦」往往不是拒绝沟通，而是不愿意在自己的问题没得到答案前被带去答另一组问题。前期问需求确实也是筛客户，但筛的是有没有真实需求、钱合不合适、愿不愿意往下走，不是筛「愿不愿意连续回答规划师很多问题」。
- 客户抛出一个问题，先正面回答，再要资料：例如客户问「有没有 5000 起投」，先答能不能，再说「您告诉我年龄，我把 5000 和 1 万两档一起算给您」，而不是只重复追问年龄。缺年龄等关键信息时不做方案是对的，不要因此批评。
- 「家人不同意」只是异议的入口：先问是哪位家人、最担心什么（收益、安全、怕被骗、资金安排），再决定怎么解释；不要一上来就说「家里人可能不了解」，客户会觉得你在说他家人不懂。
- 以前在别处（亲戚那里）买过的客户，又来问原规划师，说明还在关注、也愿意听别的意见，不要当成没机会；先把他主动问的问题（比如税）讲清楚，看他会不会接着往下问，再自然谈合作，别一上来就问「这次要不要跟我合作」。
- 客户正在问一个具体问题（比如「投保是指被保险人还是交钱的人」），先把这个问题完整接住，再结合他之前说过的需求（「怕儿子乱花」）解释，最后才进入产品比较；产品差异该讲，但别抢在客户的问题前面。
- 下一次联系时间：规划师自己说过明确的时间（「您先看两天，我周三再联系您」），明天先联系和客户状态里就按他说的这个节点写；规划师没留任何时间，只提醒「建议留一个下次联系的时间」，不要替他编一个日期。
- 高意向客户聊了很久说「我考虑一下」，给空间是对的，不用逼单；可以建议留一个很轻的下一步（「您先考虑，节后我再问问还有哪点没想明白」），别让下次联系完全悬空。
- 客户给过明确的等待事项（「等爱人晋升公示出来再定」），下次联系应围绕这件事的进展开口（「公示结果出来了吗」），而不是围绕「之前给您发消息您没回」。
- 客户第一次说不喜欢、不想买，问一句原因完全合理；客户第二次仍只回很短的「不喜欢」，就是不想展开了，这时该收口、留台阶，而不是继续挖。
- 客户在几个条件之间取舍（缴费年限、每年保费、保额三者不能同时满足），把几个选项和各自代价摆清楚让客户选，规划师可以给专业意见，但别替客户直接关掉某个选项（比如说死「30 万以下没意义」）。
- 客户问到税务、换汇、资金来源这类问题时，规划师少用「不会」「肯定没问题」「完全没关系」这种绝对回答，先问清客户具体是什么情况、从哪听说的，再回答——这是为了客户信任，照成交角度提，不上升到合规评价。要分两层看：一句完整话轮（「不会，你这从哪听到的消息，买保险和偷税没关系」）按整句理解，不要拆出半句挑毛病；但后面为了打消客户害怕，把换汇、境外收益、将来的纳税义务、未来的政策和税率说成「不用担心」「问题不大」「肯定不会」，这类未来不确定的事说死了，将来一旦对不上客户就不再信你——建议「已知的讲清楚，不确定的明确说边界」。税务、法律、外汇、未来政策、医疗这类问题都照这个标准。
- 从未互动、没有任何画像的老客户，通用唤醒是正常做法，不要求强行个性化；可以建议至少加一句为什么现在联系他，或一个很低门槛的问题，别只发一条链接。
- 做得好的地方也要指出来，让他知道哪些该保持。

【不要碰的】
- 不点评合规、违规、监管、法律责任，不提《保险法》、投诉、监管处罚这类角度。
- 不点评返佣、个人头衔、荣誉、自我介绍里的资质说法。
- 不单独挑「分红保证不保证的说法」「收益怎么表述」这类措辞毛病；只有当客户明显理解错了、影响他做决定时，才从帮客户弄明白、推进成交的角度提。

【判断原则】
- 复盘的目的是提高下一步的质量，不是给每个客户找一个错。该指出的问题继续明确指出，不要因此变得畏手畏脚；但证据不足时，不要把推测包装成确定事实——宁可写「当前证据不足，需要你确认」，也不要写一个听上去很专业、却建立在错误上下文上的结论。按「发生了什么 → 客户现在真正卡在哪 → 规划师的做法合不合理 → 有没有更好的备选 → 下一步最值得做什么」来想。大部分客户规划师做得没问题，就不写进 improve。
- 只写值得上会的：崔伟会拿 improve 里每一条和规划师当面讨论，所以只写值得在会上花几分钟谈的，宁可只写一两条，也不要凑数。
  值得写：①关于这个客户怎么走、值不值得继续投入的大判断——比如客户长期只来验证信息、不肯深入沟通；只谈返佣、拒绝电话、身份动机不明；支付能力和期待对不上。这类建议里要把「先放一放／降低联系频率／先核实身份」当成可选结论，不要只给一句新话术让规划师继续推。②快成交的客户真正卡在哪、下一步怎么推进。③客户明确说出的需求或选择标准，和规划师推荐的方向不一致。
  不写：第一次回答没把细节讲全（先简单让客户知道能解决、客户愿意再细聊才展开，是正常节奏；客户接着往下聊、甚至主动约时间，就说明这句没出问题）；原话也说得过去的话术微调（比如开放问题改二选一）；只凭一句话推断错过了机会（先看这个客户几个月来的沟通习惯）；因为客户没成交就倒推规划师做错了。
- 【10-10 崔伟定：不凑数】improve 只挑和客户有深入沟通的。规划师只发了一句问候或一条消息、客户没回的，没有讨论价值，不进 improve。值得聊的只有 1 户就写 1 户，没有就不写。
- 【同类合并】同一个问题出现在几户身上，合成一条：c 写成「{{c:编号1}}、{{c:编号2}}」，fact 分别列，problem 一句话说清共同的问题（例：「这几户都很久没联系、文字发了几轮没回——别再发文字，直接打电话，问清还打不打算办」）。
- 【先说结论】problem 第一句就是结论（该做什么、哪里不对），理由最多两三句；better 写能照着做的动作，建议打电话时写清电话里问什么，不用每户附一大段话术。
- 客户自己定了下次联系的时间或条件（「钱 12 月到期再说」），规划师也答应了的，不进 improve、不放「明天先联系」，customers 里写「按客户约定的时间再联系」。
- 建议规划师「补一句 X」「说明一下 X」之前，先查之前的聊天里是不是已经说过；说过的不写。
- 标了「⚠系统标记企微已拉黑」的客户：不点评话术，不放「明天先联系」，customers 里只写「系统显示已拉黑，先确认还能不能联系」。
- 跟进记录里电话聊得深的客户（有金额、到账时间、要方案），比企微里只发了问候的客户更值得讨论，可以进 improve 和「明天先联系」；但电话内容你只看到规划师自己记的那一句，判断要保守。
- 每条 improve 必须标 kind：
  「明确问题」= 事实答错、客户基础信息记错、客户的关键问题或条件被漏掉；
  「可优化」= 做法没错，但有更好的开场、顺序或更客户化的讲法；
  「策略参考」= 存在多种合理做法，只是另一种思路供参考，不评对错（例如推自家主推产品而不是客户提到的别家、先约讲解而不是直接发计划书、先做一档示例而不是多档方案、老客户先给空间而不是马上约）。拿不准是错误还是策略差异时，归「策略参考」。
- 涉及具体产品规则（能不能改 20 年交、改了保费涨多少、保额降到多少、领取金额），材料里没有依据的，不要把你想出来的方案当成标准答案，写成「待产品规则核实：……」。
- 每一条都要落到具体客户和聊天原话上，不写「要加强沟通」这类空话；没问题的客户不用硬挑毛病，条数宁少勿滥。
- 只评价「本次窗口」里的消息；「之前的聊天」只用来理解来龙去脉。
- 你看不到语音、文件、通话内容和没编号的图片，只知道发了什么类型。不要猜测这些内容，也不要因为看不到就判定规划师做错。
- 客户名字不在材料里，一律用占位符 {{c:编号}} 称呼客户（编号就是材料里每个客户标题上的那个编号，例如 {{c:33703}}），所有字段都这样写，包括 push。系统会自动换成客户微信名。
- 引用原话放在 quote 字段，要是聊天里的原文（太长可以用…截短），不要改写。
- 「换成这样」本身要能执行：给出的话术需要的信息（产品方向、年龄、预算），材料里得真有；没有就换成先拿信息的那一句，不要为了显得具体去假设一个产品、一组对比。
- 你写的「换成这样」和开场白，规划师会照着发给客户，所以里面不能出现你自己编的事实：公司的赴港行程和日期、名额、报销、优惠活动，产品的分红实现率、历史数据、具体利益数字，利率下调、产品停售之类的政策和市场说法。聊天材料里客户或规划师说过的可以用；材料里没有的，一律写成【待填：赴港日期】【待填：该产品分红实现率】这样的空位，让规划师自己核实后再填。
- 用「你」称呼规划师，语气像一个懂行、说话直接的老同事，是帮他多成交，不是挑他的错。

【空余时间用在哪：coverage 字段（10-10 崔伟定）】
材料末尾的「覆盖情况」列了这位规划师当天：AI 推荐逐户有没有在企微发消息、有没有跟进记录、有没有点「已联系」「推错了」；答应过当天要联系的客户做没做；跟进记录总条数、只写一两个字的条数、录入时间。
- 深入沟通的客户少，说明当天有不少空余时间：要如实写这些时间有没有用在追 AI 推荐、兑现答应过的联系、主动挖掘客户上。按事实写数字，例：「深入沟通只有 1 户。AI 重点推荐 5 户：企微联系 2 户（都只发了问候），2 户标推错，1 户没动；候补 13 户只碰了 2 户。跟进记录 14 条全在 17:53–17:56 录入，7 条只写了『1』」。
- 「点了已联系」但当天企微和跟进记录都没有痕迹的，如实写「系统里没看到联系痕迹，可能用的私人微信或手机，需确认」，不下「造假」之类的结论。
- 当天深入沟通的客户多、确实很忙时，coverage 一两句带过即可。
- 只写事实和缺口，不替他找理由，也不扣帽子。

【输出字段】
- overview：两三句话，今天整体怎么样、离成交最近的是谁、最要紧的一件事是什么。
- improve：0–6 条，没有值得说的就少写甚至不写，按 明确问题 → 可优化 → 策略参考 排序；kind 取「明确问题」「可优化」「策略参考」之一；fact 只写事实：客户问了/说了什么、规划师回了什么（带时间），不加评价；evidence 标证据状态：「直接证据」= 文字记录直接支持你的判断，「推断」= 结合上下文推出来的，「需确认」= 关键内容在电话/语音/图片/私人微信里看不到、要规划师确认；problem 写判断和原因：这里是否真有问题、为什么可能影响推进（涉及「没回、没给、没追、没约」的，按上面的要求写「当前记录里没看到」并附时间证据），better 写出具体可以照着说的话。宁可少写，不写建立在没看全的事实上的条目。
- good：今天做得好的 1–2 段，why 说明好在哪里、以后要保持。
- tomorrow：明天最该先联系的 2–4 个客户，why 说原因，opener 给一句可以直接发的开场白。
- customers：本次窗口里聊过的每个客户各一行，status 一句话，先写客户背景（新客户/老客户重新联系/重新加回/已成交等），再说清楚这个客户现在处在哪个阶段（了解产品/处理顾虑/比较方案/做决定/办手续，或在等某个外部条件）、下一步是什么，level 取「快成交」「推进中」「卡住了」「一般」之一。
- coverage：按上面「空余时间用在哪」写，3–6 句纯文字；客户用 {{c:编号}}。
- push：发到规划师企业微信的文字，300 字以内，纯文本不用 markdown。3–4 行：离成交最近的客户和该做的动作；最该改的一两条（优先「明确问题」，没有就写可优化的）；深聊少时加一行 AI 推荐/约定联系还差哪几户；明天先联系谁。每行开头可用一个表情符号。"""


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
            "properties": {"c": {"type": "string"},
                           "kind": {"type": "string", "enum": ["明确问题", "可优化", "策略参考"]},
                           "quote": {"type": "string"}, "fact": {"type": "string"},
                           "evidence": {"type": "string", "enum": ["直接证据", "推断", "需确认"]},
                           "problem": {"type": "string"}, "better": {"type": "string"}},
            "required": ["c", "kind", "quote", "fact", "evidence", "problem", "better"], "additionalProperties": False}},
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
        "coverage": {"type": "string"},
        "push": {"type": "string"},
    },
    "required": ["overview", "improve", "good", "tomorrow", "customers", "coverage", "push"],
    "additionalProperties": False,
}


def name_unarchived(s):
    """系统里没建档的客户(编号 x1、x2…)生产端查不到昵称, 这里直接写成「未建档客户1」, 规划师靠引用原话认人。"""
    return re.sub(r"\{\{c:x(\d+)\}\}", r"未建档客户\1", s or "")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _mime_of(data):
    """按文件头判图片类型(取图失败的兜底保存没经 PIL 重编码, 不一定是 jpeg)。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _json_ok(out, schema):
    """轻校验结构化输出: required 字段在、类型和 enum 大致对(数组逐项查)。不追求完备, 把明显跑偏的拦下来重试。"""
    if not isinstance(schema, dict):
        return True
    t = schema.get("type")
    if t == "object":
        if not isinstance(out, dict) or any(k not in out for k in schema.get("required", [])):
            return False
        return all(_json_ok(out[k], v) for k, v in (schema.get("properties") or {}).items() if k in out)
    if t == "array":
        return isinstance(out, list) and all(_json_ok(x, schema.get("items") or {}) for x in out)
    if "enum" in schema and out not in schema["enum"]:
        return False
    if t == "string":
        return isinstance(out, str)
    if t == "integer":
        return isinstance(out, int) and not isinstance(out, bool)
    if t == "number":
        return isinstance(out, (int, float)) and not isinstance(out, bool)
    if t == "boolean":
        return isinstance(out, bool)
    return True


def call_deepseek(system, user, schema=None, images=None, tries=3):
    """一次分析调用, 走 DeepSeek(V4.1-Flash) API(按量付费)。
    返回 (文本或 dict, 用量 dict): 有 schema 时返回校验过的 dict, 否则返回纯文本。
    images: 图片文件路径列表, 按顺序 base64 附在用户消息后面(材料里 [图片#n] = 第 n 张)。
    Claude CLI 的 --json-schema 硬约束换成: schema 写进系统提示 + json_object 模式, 拿回来自行校验, 不合格整次重试。"""
    if not DEEPSEEK_KEY:
        raise RuntimeError("缺 DEEPSEEK_API_KEY(本机可从 baoxin application-dev.yml 读)")
    if schema:
        system = (system + "\n\n【输出格式】只输出一个 JSON 对象（json），不要 markdown 代码块，严格符合以下 JSON Schema：\n"
                  + json.dumps(schema, ensure_ascii=False))
    content = user
    if images:
        parts = [{"type": "text", "text": user}]
        for p in images:
            try:
                raw = open(p, "rb").read()
                parts.append({"type": "image_url",
                              "image_url": {"url": f"data:{_mime_of(raw)};base64,{base64.b64encode(raw).decode()}"}})
            except Exception as e:
                log(f"读图失败 {os.path.basename(p)} {type(e).__name__}")
        content = parts
    last = None
    for k in range(tries):
        try:
            body = {"model": MODEL,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                    "temperature": 0.3, "max_tokens": MAX_TOKENS}
            if schema:
                body["response_format"] = {"type": "json_object"}
            resp = requests.post(DEEPSEEK_URL, headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"},
                                 json=body, timeout=2400)
            resp.raise_for_status()
            d = resp.json()
            ch = d["choices"][0]
            if ch.get("finish_reason") == "length":
                raise RuntimeError(f"输出被 max_tokens({MAX_TOKENS}) 截断")
            text = (ch["message"].get("content") or "").strip()
            text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
            u = d.get("usage") or {}
            if not schema:
                return text, u
            out = json.loads(text)
            if not _json_ok(out, schema):
                raise ValueError("结构不符合 schema")
            return out, u
        except Exception as e:
            last = e
            if k < tries - 1:
                log(f"DeepSeek 第{k+1}次失败({type(e).__name__}: {str(e)[:120]}), 重试")
                time.sleep(2)
    raise RuntimeError(f"DeepSeek 调用失败: {type(last).__name__}: {str(last)[:200]}")


def usage_line(u):
    if not u:
        return "无用量信息"
    rt = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
    return (f"{MODEL}: in={u.get('prompt_tokens')} cache_hit={u.get('prompt_cache_hit_tokens')} "
            f"out={u.get('completion_tokens')}" + (f"(含思考 {rt})" if rt else ""))


def cust_meta(c):
    meta = [c.get("state") or "", c.get("appeal") or ""]
    if c.get("dealState"):
        meta.append(f"成交情况:{c['dealState']}")
    if c.get("addDate"):
        meta.append(f"加好友{c['addDate']}")
    if c.get("blacklisted"):
        meta.append("⚠系统标记企微已拉黑")
    return meta


def connect_lines(c):
    if not c.get("connects"):
        return []
    return (["—— 规划师手填的跟进记录（多是电话，最近几条）——"]
            + [f"{x['t']} [{x.get('way') or ''}] {x.get('text') or ''}" for x in c["connects"]])


def coverage_lines(p):
    out = ["", "===== 覆盖情况（当天的 AI 推荐、答应过的联系、跟进记录；用来写 coverage）====="]
    for cov in p.get("coverage") or []:
        day = cov.get("day") or ""
        cs = cov.get("connects") or {}
        out.append(f"【{day}】跟进记录 {cs.get('total', 0)} 条，其中内容只有一两个字的 {cs.get('contentBlank', 0)} 条"
                   + (f"，录入时间 {cs.get('firstEntry')} ~ {cs.get('lastEntry')}" if cs.get("firstEntry") else ""))
        recs = cov.get("aiRec") or []
        if recs:
            out.append(f"【{day}】AI 推荐 {len(recs)} 户（企微=当天企微里给他发过消息；电话=当天有跟进记录）：")
            for r in recs:
                flags = ["企微有" if r.get("wechat") else "企微无", "电话有" if r.get("phone") else "电话无"]
                if r.get("markedContacted"):
                    flags.append(f"{r['markedContacted']} 点了已联系")
                if r.get("markedWrong"):
                    flags.append(f"标推错:{r['markedWrong']}")
                out.append(f"  {r.get('type')}#{r.get('rank')} {{{{c:{r['key']}}}}}"
                           + (f" 成交指数{r['dealIndex']}" if r.get("dealIndex") is not None else "") + " · " + " / ".join(flags))
        else:
            out.append(f"【{day}】当天没有 AI 推荐")
        for r in cov.get("promises") or []:
            out.append(f"【{day}】答应过当天联系 {{{{c:{r['key']}}}}}（{r.get('what') or ''}）：{r.get('status')}；"
                       f"{'企微有' if r.get('wechat') else '企微无'} / {'电话有' if r.get('phone') else '电话无'}")
    return out


def build_user_prompt(p, window_start, window_end, img_count=0):
    lines = [f"规划师：{p['name']}", f"本次窗口：{window_start} 至 {window_end}（北京时间）"]
    if p.get("span_note"):
        lines.append(p["span_note"])
    if img_count:
        lines.append(f"聊天里写成 [图片#n] 的 {img_count} 张图片已按编号顺序附在本次消息后面（第 n 张 = [图片#n]）；"
                     "只写 [图片] 没有编号的看不到。")
    lines.append("")
    for c in p["customers"]:
        meta = cust_meta(c)
        if c.get("silentDays") is not None:
            meta.append(f"本次之前已 {c['silentDays']} 天没聊过")
        elif not c.get("history"):
            meta.append("之前没有聊天记录")
        lines.append(f"===== 客户 {c['key']}（{' / '.join(m for m in meta if m)}）=====")
        lines += connect_lines(c)
        if c.get("history"):
            lines.append("—— 之前的聊天（仅供理解来龙去脉）——")
            lines += [f"{m['t']} {m['who']}：{m['text']}" for m in c["history"]]
        lines.append("—— 本次窗口 ——")
        lines += [f"{m['t']} {m['who']}：{m['text']}" for m in c["today"]]
        lines.append("")
    if p.get("phoneOnly"):
        lines.append("===== 本次窗口只填了跟进记录（多是电话）、企微里没聊的客户 =====")
        for c in p["phoneOnly"]:
            lines.append(f"===== 客户 {c['key']}（{' / '.join(m for m in cust_meta(c) if m)}）=====")
            lines += connect_lines(c)
            lines.append("")
    lines += coverage_lines(p)
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
.k-明确问题{background:var(--redbg);color:var(--red)}.k-可优化{background:var(--amberbg);color:var(--amber)}
.k-策略参考{background:var(--line);color:var(--sub)}
.ev{border:1px solid var(--line);color:var(--sub)}
.foot{color:var(--sub);font-size:12px;margin-top:30px}
"""


def esc(s):
    """HTML 转义, 但保留 {{c:xxx}} 占位符原样(生产端替换成已转义的昵称)。"""
    return html.escape(s or "", quote=True)


def who(c):
    """c 可以是一个编号, 也可以是合并条目的多个编号(「{{c:1}}、{{c:2}}」)。"""
    keys = [k for k in re.split(r"[\s,，、;；]+", (c or "").replace("{", " ").replace("}", " ").replace("c:", " ")) if k]
    return "、".join(f"{{{{c:{esc(k)}}}}}" for k in keys) or "{{c:?}}"


def render_page(p, r, window_start, window_end):
    out = [f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<title>聊天复盘 · {esc(p['name'])}</title><style>{PAGE_CSS}</style></head><body><div class='wrap'>",
           f"<h1>{esc(p['name'])} · 聊天复盘</h1>",
           f"<div class='meta'>{esc(window_start)} – {esc(window_end)}{esc(p.get('span_label', ''))} · {len(p['customers'])} 位客户 · {p['msgCount']} 条消息 · AI 分析</div>",
           f"<div class='ov'>{esc(r['overview'])}</div>"]
    if r["improve"]:
        out.append("<h2>🔻 可以做得更好</h2><div class='meta'>明确问题＝要改；可优化＝有更好的做法；策略参考＝另一种思路，不评对错。直接证据＝文字记录直接支持；推断＝结合上下文推的；需确认＝关键内容看不到，请你确认</div>")
        rank = {"明确问题": 0, "可优化": 1, "策略参考": 2}
        for x in sorted(r["improve"], key=lambda x: rank.get(x.get("kind"), 1)):
            k = x.get("kind") if x.get("kind") in rank else "可优化"
            out.append(f"<div class='card'><div class='who'>{who(x['c'])} <span class='tag k-{k}'>{k}</span> <span class='tag ev'>{esc(x.get('evidence') or '')}</span></div><div class='q'>{esc(x['quote'])}</div>"
                       + (f"<div class='lab'>事实</div><div>{esc(x['fact'])}</div>" if x.get('fact') else "")
                       + f"<div class='lab'>{'判断' if k == '明确问题' else '说明'}</div><div>{esc(x['problem'])}</div>"
                       f"<div class='lab'>换成这样</div><div class='say'>{esc(x['better'])}</div></div>")
    if r.get("coverage"):
        out.append(f"<h2>🕒 空余时间用在哪</h2><div class='card'>{esc(r['coverage'])}</div>")
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
    out.append("<div class='foot'>AI 看了文字聊天、存下来的图片和你填的跟进记录；语音、文件和通话内容看不到。这份复盘只发给你本人。</div>")
    out.append("</div></body></html>")
    return "".join(out)


# ---------------------------------------------------------------- 主流程
def ping():
    text, u = call_deepseek("你是一个测试助手。", "只回复两个字：正常")
    log(f"ping ok, model={MODEL}, 回复长度={len(text)}, {usage_line(u)}")


def main():
    if MODE == "ping":
        ping()
        return
    if not TOKEN:
        log("缺 CHAT_REVIEW_TOKEN")
        sys.exit(1)

    report_day = WINDOW_END[:10] if WINDOW_END else datetime.datetime.now(BJ).date().isoformat()
    ok, why = workday_status(report_day)
    if not ok and not WINDOW_END and not FORCE:
        log(f"{report_day} {why}, 不出复盘(挪到节后第一个工作日一起出)")
        return
    days = window_days(report_day)
    exports = []
    for d in days:
        end = (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat() + " 00:00"
        resp = requests.get(f"{BASE_URL}/chatReview/export", params={"token": TOKEN, "end": end}, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            log(f"导出 {d} 失败: {data.get('msg')}")
            sys.exit(1)
        data["_day"] = d[5:]
        exports.append(data)
        log(f"导出 {d}({workday_status(d)[1]}): 规划师 {len(data['planners'])} 位")
    planners = merge_exports(exports, days)
    ws, we = f"{days[0][5:]} 00:00", f"{report_day[5:]} 00:00"
    labels = [f"{d[5:]}{'' if workday_status(d)[0] else '(' + workday_status(d)[1] + ')'}" for d in days]
    span_note = (f"本次共 {len(days)} 天：{'、'.join(labels)}。窗口跨了休息日，「明天先联系」指收到这份复盘的今天（{report_day[5:]}）。"
                 if len(days) > 1 else "")
    log(f"出复盘日 {report_day}, 窗口 {ws} ~ {we}({len(days)} 天), 规划师 {len(planners)} 位")

    results, team_input, usages = [], [], []
    todo = []
    for p in planners:
        if ONLY and p["vxId"] not in ONLY:
            continue
        drop_broadcast_only(p)
        trim_to_fit(p)
        p["span_label"] = f"（共 {len(days)} 天）" if len(days) > 1 else ""
        p["span_note"] = span_note + (f"材料太长，{('、'.join(p['trimmed']))} 这几天的聊天没放进来。" if p.get("trimmed") else "")
        p["msgCount"] = sum(len(c["today"]) for c in p["customers"])
        has_cov = p["phoneOnly"] or any(cv.get("aiRec") for cv in p["coverage"])
        if p["msgCount"] < MIN_MESSAGES and not has_cov:
            log(f"{p['vxId']}: 窗口内 {p['msgCount']} 条且没有电话/AI推荐, 跳过")
            continue
        todo.append(p)

    img_root = tempfile.mkdtemp(prefix="chatimg-")

    def analyze(p):
        t0 = time.time()
        try:
            img_dir, n_img = fetch_images(p, img_root)
            p["imgCount"] = n_img
            images = [os.path.join(img_dir, f"{i}.jpg") for i in range(1, n_img + 1)] or None
            for attempt in (0, 1):
                try:
                    r, u = call_deepseek(SYSTEM_PROMPT, build_user_prompt(p, ws, we, n_img),
                                         schema=PLANNER_SCHEMA, images=images)
                    return p, r, u, time.time() - t0
                except Exception as e:
                    # 兜底: 万一整份超上下文, 再丢掉一半材料重试一次
                    if attempt == 0 and p["customers"] and re.search(r"上下文|context|too long|maximum", str(e), re.I):
                        trim_to_fit(p, 120_000)
                        p["msgCount"] = sum(len(c["today"]) for c in p["customers"])
                        log(f"{p['vxId']}: 超上下文, 剩 {p['msgCount']} 条重试一次")
                        continue
                    raise
            raise RuntimeError("unreachable")
        except Exception as e:  # 一人失败不影响其他人
            log(f"{p['vxId']}: 分析失败 {type(e).__name__}: {str(e)[:200]}")
            return p, None, None, time.time() - t0

    # 每人一次调用互不依赖, 并行跑(串行时一人约 7 分钟, 4 人 23 分钟)
    with ThreadPoolExecutor(max_workers=4) as pool:
        done = list(pool.map(analyze, todo))

    for p, r, u, secs in done:
        if r is None:
            continue
        usages.append(u or {})
        log(f"{p['vxId']}: {len(p['customers'])} 户/{p['msgCount']} 条/图 {p.get('imgCount', 0)} 张/电话户 {len(p['phoneOnly'])}, 用时 {secs:.0f}s, "
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
    tot_in = sum(x.get("prompt_tokens", 0) for x in usages)
    tot_hit = sum(x.get("prompt_cache_hit_tokens", 0) for x in usages)
    tot_out = sum(x.get("completion_tokens", 0) for x in usages)
    tot_rt = sum((x.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) for x in usages)
    log(f"本次用量: in={tot_in}(缓存命中 {tot_hit}) out={tot_out}(含思考 {tot_rt}), {len(results)} 位（deepseek-flash 按量付费）")
    if body.get("code") != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
