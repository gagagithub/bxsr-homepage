#!/usr/bin/env python3
"""
规划师日报 - 自动生成脚本
每天 10:30 (北京时间) 由 GitHub Actions 调用，
通过企业微信会话存档 SDK 拉取昨日聊天记录，
用 Claude API 分析后生成 HTML 页面。
"""

import os
import sys
import json
import ctypes
import base64
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

# ── 配置 ──────────────────────────────────────────────
BEIJING_TZ = timezone(timedelta(hours=8))
NOW = datetime.now(BEIJING_TZ)

# 分析的是昨天的数据
YESTERDAY = NOW - timedelta(days=1)
DATE_STR = YESTERDAY.strftime("%Y-%m-%d")            # 2026-03-30
DATE_CN = YESTERDAY.strftime("%Y年%-m月%-d日")         # 2026年3月30日
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
WEEKDAY_CN = WEEKDAYS[YESTERDAY.weekday()]

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_FILE = os.path.join(PROJECT_ROOT, "planner-daily-reports.html")

# 从环境变量读取配置
CORP_ID = os.environ.get("WEWORK_CORP_ID", "")
CHAT_SECRET = os.environ.get("WEWORK_CHAT_SECRET", "")
PRIVATE_KEY_PEM = os.environ.get("WEWORK_PRIVATE_KEY", "")

# 规划师名单：userid -> 中文名
# 从环境变量读取 JSON 格式，例如：{"jiangning":"江宁","makangting":"马康挺",...}
PLANNER_MAP_STR = os.environ.get("WEWORK_PLANNER_MAP", "{}")
PLANNER_MAP = json.loads(PLANNER_MAP_STR)

# SDK 路径
SDK_PATH = os.path.join(PROJECT_ROOT, "sdk", "libWeWorkFinanceSdk_C.so")


# ── 企业微信 SDK 封装 ────────────────────────────────────
class WeWorkFinanceSDK:
    """封装企业微信会话内容存档 C SDK 的 Python 调用。"""

    def __init__(self, corp_id, secret, private_key_pem, sdk_path):
        if not os.path.exists(sdk_path):
            raise FileNotFoundError(f"SDK file not found: {sdk_path}")

        self.dll = ctypes.cdll.LoadLibrary(sdk_path)
        self.sdk = self.dll.NewSdk()

        # Init 返回 0 表示成功
        ret = self.dll.Init(self.sdk, corp_id.encode("utf-8"), secret.encode("utf-8"))
        if ret != 0:
            raise RuntimeError(f"WeWork SDK Init failed, error code: {ret}")

        # 加载 RSA 私钥
        self.rsa_key = RSA.import_key(private_key_pem)
        self.cipher = PKCS1_v1_5.new(self.rsa_key)

        print("WeWork SDK initialized successfully.")

    def get_chat_data(self, seq=0, limit=1000):
        """拉取聊天数据，返回 chatdata 列表。"""
        all_data = []
        current_seq = seq

        while True:
            slice_ptr = self.dll.NewSlice()
            ret = self.dll.GetChatData(
                self.sdk, current_seq, limit, b"", b"", 5, ctypes.c_long(slice_ptr)
            )
            if ret != 0:
                self.dll.FreeSlice(slice_ptr)
                print(f"GetChatData failed at seq={current_seq}, error code: {ret}")
                break

            content = self.dll.GetContentFromSlice(slice_ptr)
            data_str = ctypes.string_at(content, -1).decode("utf-8")
            self.dll.FreeSlice(slice_ptr)

            result = json.loads(data_str)
            chat_data = result.get("chatdata", [])

            if not chat_data:
                break

            all_data.extend(chat_data)
            current_seq = max(item["seq"] for item in chat_data)
            print(f"  Fetched {len(chat_data)} messages, max_seq={current_seq}")

            if len(chat_data) < limit:
                break

        return all_data

    def decrypt_message(self, encrypt_random_key, encrypt_chat_msg):
        """解密单条消息，返回明文 JSON dict。"""
        # RSA 解密 random_key
        encrypted_key = base64.b64decode(encrypt_random_key)
        random_key = self.cipher.decrypt(encrypted_key, sentinel=b"")

        # 用 random_key 通过 SDK 解密消息体
        slice_ptr = self.dll.NewSlice()
        ret = self.dll.DecryptData(
            random_key, encrypt_chat_msg.encode("utf-8"), ctypes.c_long(slice_ptr)
        )
        if ret != 0:
            self.dll.FreeSlice(slice_ptr)
            return None

        content = self.dll.GetContentFromSlice(slice_ptr)
        msg_str = ctypes.string_at(content, -1).decode("utf-8")
        self.dll.FreeSlice(slice_ptr)

        return json.loads(msg_str)

    def destroy(self):
        """释放 SDK 资源。"""
        self.dll.DestroySdk(self.sdk)


# ── 数据采集 ─────────────────────────────────────────
def fetch_yesterday_messages(sdk):
    """拉取并解密昨日的聊天记录，按规划师分组返回。"""
    # 昨天 00:00:00 ~ 23:59:59 的时间戳 (毫秒)
    day_start = YESTERDAY.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = YESTERDAY.replace(hour=23, minute=59, second=59, microsecond=999999)
    ts_start = int(day_start.timestamp() * 1000)
    ts_end = int(day_end.timestamp() * 1000)

    print(f"Fetching messages for {DATE_STR} ({ts_start} ~ {ts_end})")

    # 拉取所有聊天数据
    raw_data = sdk.get_chat_data(seq=0, limit=1000)
    print(f"Total raw messages fetched: {len(raw_data)}")

    # 解密并过滤昨日消息
    planner_userids = set(PLANNER_MAP.keys())
    messages_by_planner = {uid: [] for uid in planner_userids}

    for item in raw_data:
        msg = sdk.decrypt_message(item["encrypt_random_key"], item["encrypt_chat_msg"])
        if msg is None:
            continue

        msg_time = msg.get("msgtime", 0)
        if msg_time < ts_start or msg_time > ts_end:
            continue

        sender = msg.get("from", "")
        to_list = msg.get("tolist", [])

        # 如果发送者是规划师
        if sender in planner_userids:
            messages_by_planner[sender].append(msg)
        # 如果接收者包含规划师
        else:
            for recipient in to_list:
                if recipient in planner_userids:
                    messages_by_planner[recipient].append(msg)

    # 过滤掉没有消息的规划师
    messages_by_planner = {
        uid: msgs for uid, msgs in messages_by_planner.items() if msgs
    }

    for uid, msgs in messages_by_planner.items():
        print(f"  {PLANNER_MAP.get(uid, uid)}: {len(msgs)} messages")

    return messages_by_planner


def format_messages_for_analysis(messages):
    """将消息列表格式化为文本，供 Claude 分析。"""
    lines = []
    for msg in sorted(messages, key=lambda m: m.get("msgtime", 0)):
        sender = msg.get("from", "unknown")
        to_list = msg.get("tolist", [])
        msgtype = msg.get("msgtype", "")
        time_ms = msg.get("msgtime", 0)
        time_str = datetime.fromtimestamp(time_ms / 1000, tz=BEIJING_TZ).strftime("%H:%M")

        sender_name = PLANNER_MAP.get(sender, sender)
        to_names = ", ".join(PLANNER_MAP.get(t, t) for t in to_list)

        if msgtype == "text":
            content = msg.get("text", {}).get("content", "")
            lines.append(f"[{time_str}] {sender_name} → {to_names}: {content}")
        elif msgtype == "image":
            lines.append(f"[{time_str}] {sender_name} → {to_names}: [发送了一张图片]")
        elif msgtype == "voice":
            lines.append(f"[{time_str}] {sender_name} → {to_names}: [发送了语音消息]")
        elif msgtype == "video":
            lines.append(f"[{time_str}] {sender_name} → {to_names}: [发送了视频]")
        elif msgtype == "file":
            filename = msg.get("file", {}).get("filename", "文件")
            lines.append(f"[{time_str}] {sender_name} → {to_names}: [发送了文件: {filename}]")
        elif msgtype == "link":
            title = msg.get("link", {}).get("title", "链接")
            lines.append(f"[{time_str}] {sender_name} → {to_names}: [分享了链接: {title}]")
        elif msgtype == "revoke":
            lines.append(f"[{time_str}] {sender_name}: [撤回了一条消息]")
        else:
            lines.append(f"[{time_str}] {sender_name} → {to_names}: [{msgtype}消息]")

    return "\n".join(lines)


# ── Claude API 分析 ──────────────────────────────────
def analyze_planner_chats(planner_name, chat_text, client):
    """用 Claude 分析单个规划师的聊天记录，返回结构化 JSON。"""

    prompt = f"""你是一个专业的保险销售团队管理分析助手。以下是规划师「{planner_name}」在 {DATE_CN}（{WEEKDAY_CN}）与客户的企业微信聊天记录。

请分析这些聊天记录，输出严格的 JSON（不要有 markdown 包裹），格式如下：
{{
  "planner_name": "{planner_name}",
  "total_clients": 数字,
  "total_messages_sent": 数字（规划师发出的消息数）,
  "total_messages_received": 数字（收到客户回复的消息数）,
  "proactive_count": 数字（规划师主动发起的对话数）,
  "proposals_sent": 数字（发送了产品方案/资料的对话数）,
  "score": 数字（1-10分，跟进质量评分）,
  "score_reason": "评分理由（一句话）",
  "clients": [
    {{
      "name": "客户姓名或代号（如无法判断用'客户A'）",
      "message_count": 数字,
      "initiated_by": "planner 或 client",
      "duration_minutes": 数字（估算沟通时长）,
      "status": "initial_contact / active_chat / proposal_sent / follow_up / no_response / rejected",
      "intent_level": "strong / medium / low / unknown",
      "intent_desc": "意向描述（10字以内）",
      "products": "涉及产品（10字以内）",
      "summary": "沟通摘要（50-100字）",
      "has_next_step": true/false,
      "next_step_desc": "下一步描述（如有）",
      "concerns": "客户顾虑（如有，否则为空字符串）",
      "next_action": "建议下一步行动（20-40字）"
    }}
  ],
  "highlights": {{
    "best_progress": "进展最好的客户及原因（30字以内）",
    "needs_attention": "需关注的情况（30字以内）"
  }},
  "ai_suggestions": [
    "综合建议1（30-50字）",
    "综合建议2",
    "综合建议3"
  ]
}}

要求：
- 只基于实际聊天记录进行分析，不要编造
- status 用英文，对应中文：initial_contact=初次接触, active_chat=有效沟通, proposal_sent=已发方案, follow_up=跟进中, no_response=无回应, rejected=明确拒绝
- intent_level: strong=强, medium=中等, low=低, unknown=不明
- score 评分标准：主动性、沟通质量、是否有明确下一步、客户反馈情况
- 客户姓名尽量从聊天上下文推断，推断不出的用「客户A/B/C」
- 只输出 JSON，不要有其他文字

聊天记录如下：

{chat_text}"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )

    result_text = ""
    for block in message.content:
        if block.type == "text":
            result_text += block.text

    result_text = result_text.strip()
    if result_text.startswith("```"):
        result_text = re.sub(r"^```(?:json)?\s*", "", result_text)
        result_text = re.sub(r"\s*```$", "", result_text)

    return json.loads(result_text)


# ── HTML 生成 ─────────────────────────────────────────
STATUS_MAP = {
    "initial_contact": ("初次接触", "status-new"),
    "active_chat":     ("有效沟通", "status-active"),
    "proposal_sent":   ("已发方案", "status-proposal"),
    "follow_up":       ("跟进中",   "status-pending"),
    "no_response":     ("无回应",   "status-cold"),
    "rejected":        ("明确拒绝", "status-cold"),
}

INTENT_MAP = {
    "strong":  "强",
    "medium":  "中等",
    "low":     "低",
    "unknown": "不明",
}

SCORE_CLASS = lambda s: "score-high" if s >= 7.5 else ("score-mid" if s >= 5.5 else "score-low")


def esc(text):
    """基本 HTML 转义。"""
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def generate_planner_detail_page(data):
    """生成单个规划师的详情页 HTML。"""
    name = esc(data["planner_name"])
    first_char = data["planner_name"][0]
    score = data.get("score", 0)
    score_cls = SCORE_CLASS(score)

    # 概览数据
    overview_html = f"""
  <div class="overview-row">
    <div class="overview-card"><div class="overview-num">{data['total_clients']}</div><div class="overview-label">沟通客户数</div></div>
    <div class="overview-card"><div class="overview-num">{data['total_messages_sent']}</div><div class="overview-label">发出消息</div></div>
    <div class="overview-card"><div class="overview-num">{data['total_messages_received']}</div><div class="overview-label">收到回复</div></div>
    <div class="overview-card"><div class="overview-num">{data['proactive_count']}</div><div class="overview-label">主动发起</div></div>
    <div class="overview-card"><div class="overview-num">{data['proposals_sent']}</div><div class="overview-label">发送方案</div></div>
  </div>"""

    # 评分
    score_html = f"""
  <div class="score-banner">
    <div class="score-circle">{score}</div>
    <div class="score-info">
      <h3>当日跟进质量评分</h3>
      <p>{esc(data.get('score_reason', ''))}</p>
    </div>
  </div>"""

    # 客户卡片
    clients_html = ""
    for c in data.get("clients", []):
        status_text, status_cls = STATUS_MAP.get(c.get("status", ""), ("未知", "status-new"))
        intent_text = INTENT_MAP.get(c.get("intent_level", ""), "不明")
        initiated = "规划师主动触达" if c.get("initiated_by") == "planner" else "客户主动咨询"
        first_c = esc(c["name"])[0] if c.get("name") else "?"

        # 顾虑区域
        concerns_block = ""
        if c.get("concerns"):
            concerns_block = f"""
      <div class="concerns">
        <div class="concerns-title">客户顾虑</div>
        <div class="concerns-text">{esc(c['concerns'])}</div>
      </div>"""

        # 下一步
        next_step_block = ""
        if c.get("next_action"):
            next_step_block = f"""
      <div class="next-step">
        <div class="next-step-title">建议下一步</div>
        <div class="next-step-text">{esc(c['next_action'])}</div>
      </div>"""

        has_next = "✅ " + esc(c.get("next_step_desc", "有")) if c.get("has_next_step") else "⏳ 暂无明确下一步"

        clients_html += f"""
  <div class="client-card">
    <div class="client-header">
      <div class="client-info">
        <div class="client-avatar">{first_c}</div>
        <div>
          <div class="client-name">{esc(c['name'])}</div>
          <div class="client-meta">消息 {c.get('message_count', 0)} 条 &middot; {initiated} &middot; 时长约 {c.get('duration_minutes', 0)} 分钟</div>
        </div>
      </div>
      <span class="status-tag {status_cls}">{status_text}</span>
    </div>
    <div class="client-body">
      <div class="chat-summary"><strong>沟通摘要：</strong>{esc(c.get('summary', ''))}</div>
      <div class="analysis-grid">
        <div class="analysis-item"><div class="analysis-label">客户意向</div><div class="analysis-value">{intent_text} — {esc(c.get('intent_desc', ''))}</div></div>
        <div class="analysis-item"><div class="analysis-label">沟通方式</div><div class="analysis-value">{initiated}</div></div>
        <div class="analysis-item"><div class="analysis-label">涉及产品</div><div class="analysis-value">{esc(c.get('products', ''))}</div></div>
        <div class="analysis-item"><div class="analysis-label">是否有下一步</div><div class="analysis-value">{has_next}</div></div>
      </div>
      {concerns_block}
      {next_step_block}
    </div>
  </div>"""

    # AI 建议
    suggestions_html = ""
    for s in data.get("ai_suggestions", []):
        suggestions_html += f"\n      <li>{esc(s)}</li>"

    # 完整页面
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{name} &middot; 规划师日报 &middot; {DATE_STR}</title>
  <style>
    :root {{
      --navy:  #1E2761; --teal:  #028090; --off:   #F4F6FB;
      --gray:  #8892A4; --dark:  #111827; --white: #FFFFFF;
      --blue:  #1976D2; --blue-dark: #0D47A1; --blue-light: #E3F2FD;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; background: var(--off); color: var(--dark); min-height: 100vh; display: flex; flex-direction: column; }}
    header {{ background: linear-gradient(135deg,#0D47A1 0%,#1565C0 60%,#1976D2 100%); color: var(--white); padding: 40px 40px 36px; position: relative; overflow: hidden; }}
    header::before {{ content:""; position:absolute; inset:0; background:url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.03'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E"); }}
    .header-inner {{ position:relative; z-index:1; max-width:900px; margin:0 auto; }}
    .header-nav {{ display:flex; align-items:center; gap:12px; margin-bottom:20px; }}
    .back-btn {{ display:inline-flex; align-items:center; gap:6px; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.2); border-radius:100px; padding:6px 14px; font-size:13px; color:rgba(255,255,255,.8); text-decoration:none; transition:background .15s; white-space:nowrap; }}
    .back-btn:hover {{ background:rgba(255,255,255,.2); }}
    .planner-header {{ display:flex; align-items:center; gap:16px; }}
    .planner-avatar-lg {{ width:56px; height:56px; border-radius:50%; background:rgba(255,255,255,.2); display:flex; align-items:center; justify-content:center; font-size:24px; font-weight:700; flex-shrink:0; }}
    .planner-header h1 {{ font-size:clamp(20px,3vw,28px); font-weight:700; }}
    .planner-header .subtitle {{ font-size:14px; opacity:.7; margin-top:4px; }}
    main {{ flex:1; max-width:900px; width:100%; margin:0 auto; padding:32px 24px 80px; }}
    .overview-row {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:12px; margin-bottom:32px; }}
    .overview-card {{ background:var(--white); border-radius:14px; padding:18px; text-align:center; border:1.5px solid #EEF2FF; }}
    .overview-num {{ font-size:24px; font-weight:800; color:var(--blue); }}
    .overview-label {{ font-size:11px; color:var(--gray); margin-top:3px; }}
    .score-banner {{ background:var(--white); border-radius:14px; border:1.5px solid #E3F2FD; padding:20px 24px; margin-bottom:32px; display:flex; align-items:center; gap:16px; }}
    .score-circle {{ width:56px; height:56px; border-radius:50%; background:linear-gradient(135deg,#0D47A1,#1976D2); color:var(--white); display:flex; align-items:center; justify-content:center; font-size:20px; font-weight:800; flex-shrink:0; }}
    .score-info h3 {{ font-size:15px; font-weight:700; color:var(--navy); }}
    .score-info p {{ font-size:13px; color:var(--gray); margin-top:3px; line-height:1.5; }}
    .section-title {{ font-size:16px; font-weight:700; color:var(--navy); margin-bottom:16px; padding-bottom:10px; border-bottom:2px solid #DDE3EE; }}
    .client-card {{ background:var(--white); border-radius:16px; border:1.5px solid #EEF2FF; margin-bottom:16px; overflow:hidden; }}
    .client-header {{ padding:16px 20px; display:flex; align-items:center; justify-content:space-between; gap:12px; border-bottom:1px solid #EEF2FF; }}
    .client-info {{ display:flex; align-items:center; gap:10px; }}
    .client-avatar {{ width:36px; height:36px; border-radius:50%; background:#E3F2FD; color:var(--blue-dark); display:flex; align-items:center; justify-content:center; font-size:14px; font-weight:700; flex-shrink:0; }}
    .client-name {{ font-size:15px; font-weight:700; color:var(--navy); }}
    .client-meta {{ font-size:12px; color:var(--gray); margin-top:2px; }}
    .status-tag {{ font-size:11px; font-weight:600; padding:4px 10px; border-radius:100px; white-space:nowrap; }}
    .status-active {{ background:#E8F5E9; color:#2E7D32; }}
    .status-pending {{ background:#FFF8E1; color:#F57F17; }}
    .status-cold {{ background:#FFEBEE; color:#C62828; }}
    .status-new {{ background:#E3F2FD; color:#0D47A1; }}
    .status-proposal {{ background:#F3E5F5; color:#7B1FA2; }}
    .client-body {{ padding:18px 20px; }}
    .chat-summary {{ font-size:13px; color:#444; line-height:1.7; margin-bottom:14px; }}
    .chat-summary strong {{ color:var(--dark); }}
    .analysis-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:14px; }}
    .analysis-item {{ background:var(--off); border-radius:10px; padding:12px 14px; }}
    .analysis-label {{ font-size:11px; color:var(--gray); font-weight:600; margin-bottom:4px; }}
    .analysis-value {{ font-size:13px; color:var(--dark); font-weight:600; }}
    .concerns {{ background:#FFF8E1; border-radius:10px; padding:12px 14px; margin-bottom:14px; }}
    .concerns-title {{ font-size:12px; font-weight:700; color:#F57F17; margin-bottom:4px; }}
    .concerns-text {{ font-size:13px; color:#666; line-height:1.5; }}
    .next-step {{ background:#E8F5E9; border-radius:10px; padding:12px 14px; }}
    .next-step-title {{ font-size:12px; font-weight:700; color:#2E7D32; margin-bottom:4px; }}
    .next-step-text {{ font-size:13px; color:#666; line-height:1.5; }}
    .ai-summary {{ background:var(--white); border-radius:16px; border:1.5px solid #E3F2FD; padding:24px; margin-top:32px; }}
    .ai-summary h3 {{ font-size:15px; font-weight:700; color:var(--navy); margin-bottom:12px; }}
    .ai-summary ul {{ list-style:none; padding:0; }}
    .ai-summary li {{ font-size:13px; color:#444; line-height:1.7; padding:6px 0; border-bottom:1px solid #F0F4FF; }}
    .ai-summary li:last-child {{ border-bottom:none; }}
    .ai-summary li::before {{ content:"💡 "; }}
    footer {{ background:var(--navy); color:rgba(255,255,255,.4); text-align:center; padding:28px 24px; font-size:12px; letter-spacing:1px; }}
    @media (max-width:600px) {{
      header {{ padding:28px 20px 24px; }}
      main {{ padding:20px 14px 60px; }}
      .analysis-grid {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <div class="header-nav">
      <a class="back-btn" href="planner-daily-{DATE_STR}.html">&larr; 返回当日总览</a>
    </div>
    <div class="planner-header">
      <div class="planner-avatar-lg">{first_char}</div>
      <div>
        <h1>{name} &middot; 客户沟通详情</h1>
        <div class="subtitle">{DATE_CN}（{WEEKDAY_CN}） &middot; 共沟通 {data['total_clients']} 位客户</div>
      </div>
    </div>
  </div>
</header>

<main>
{overview_html}
{score_html}

  <div class="section-title">客户沟通详情（{data['total_clients']} 位）</div>
{clients_html}

  <div class="ai-summary">
    <h3>AI 综合建议</h3>
    <ul>{suggestions_html}
    </ul>
  </div>
</main>

<footer>保心上人 &middot; CONFIDENTIAL &middot; INTERNAL USE ONLY</footer>

</body>
</html>"""
    return html


def generate_date_overview_page(all_planner_data):
    """生成某天的所有规划师概览页 HTML。"""
    total_clients = sum(d["total_clients"] for d in all_planner_data)
    total_msgs = sum(d["total_messages_sent"] + d["total_messages_received"] for d in all_planner_data)
    avg_score = sum(d["score"] for d in all_planner_data) / len(all_planner_data) if all_planner_data else 0

    # 规划师卡片
    planner_cards_html = ""
    for d in all_planner_data:
        name = esc(d["planner_name"])
        first_char = d["planner_name"][0]
        uid = d.get("userid", d["planner_name"])
        score = d.get("score", 0)
        score_cls = SCORE_CLASS(score)
        best = esc(d.get("highlights", {}).get("best_progress", ""))
        attention = esc(d.get("highlights", {}).get("needs_attention", ""))

        # 生成拼音文件名 (简化处理：使用 userid)
        filename = f"planner-detail-{DATE_STR}-{uid}.html"

        planner_cards_html += f"""
    <a class="planner-card" href="{filename}">
      <div class="planner-card-header">
        <div class="planner-avatar">{first_char}</div>
        <div class="planner-name">{name}</div>
      </div>
      <div class="planner-card-body">
        <div class="planner-stats">
          <div class="p-stat"><div class="p-stat-num">{d['total_clients']}</div><div class="p-stat-label">沟通客户</div></div>
          <div class="p-stat"><div class="p-stat-num">{d['total_messages_sent']}</div><div class="p-stat-label">消息数</div></div>
        </div>
        <div class="planner-highlight">
          <strong>进展最好：</strong>{best}<br>
          <strong>需关注：</strong>{attention}
        </div>
        <div class="planner-score">
          <span class="score-badge {score_cls}">{score} 分</span>
          <span class="score-text">{esc(d.get('score_reason', ''))}</span>
        </div>
        <div class="view-detail">查看详情 ›</div>
      </div>
    </a>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>规划师日报 &middot; {DATE_STR}</title>
  <style>
    :root {{
      --navy:#1E2761; --teal:#028090; --off:#F4F6FB;
      --gray:#8892A4; --dark:#111827; --white:#FFFFFF;
      --blue:#1976D2; --blue-light:#E3F2FD;
    }}
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{ font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; background:var(--off); color:var(--dark); min-height:100vh; display:flex; flex-direction:column; }}
    header {{ background:linear-gradient(135deg,#0D47A1 0%,#1565C0 60%,#1976D2 100%); color:var(--white); padding:40px 40px 36px; position:relative; overflow:hidden; }}
    header::before {{ content:""; position:absolute; inset:0; background:url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.03'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E"); }}
    .header-inner {{ position:relative; z-index:1; max-width:900px; margin:0 auto; }}
    .header-nav {{ display:flex; align-items:center; gap:12px; margin-bottom:20px; }}
    .back-btn {{ display:inline-flex; align-items:center; gap:6px; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.2); border-radius:100px; padding:6px 14px; font-size:13px; color:rgba(255,255,255,.8); text-decoration:none; transition:background .15s; white-space:nowrap; }}
    .back-btn:hover {{ background:rgba(255,255,255,.2); }}
    header h1 {{ font-size:clamp(20px,3vw,28px); font-weight:700; }}
    header .subtitle {{ font-size:14px; opacity:.7; margin-top:4px; }}
    main {{ flex:1; max-width:900px; width:100%; margin:0 auto; padding:32px 24px 80px; }}
    .summary-row {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:14px; margin-bottom:36px; }}
    .summary-card {{ background:var(--white); border-radius:14px; padding:20px; text-align:center; border:1.5px solid #EEF2FF; }}
    .summary-num {{ font-size:28px; font-weight:800; color:var(--blue); }}
    .summary-label {{ font-size:12px; color:var(--gray); margin-top:4px; }}
    .section-title {{ font-size:16px; font-weight:700; color:var(--navy); margin-bottom:16px; padding-bottom:10px; border-bottom:2px solid #DDE3EE; }}
    .planner-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:16px; margin-bottom:40px; }}
    .planner-card {{ background:var(--white); border-radius:16px; border:1.5px solid #EEF2FF; text-decoration:none; color:inherit; overflow:hidden; transition:transform .15s,box-shadow .15s,border-color .15s; }}
    .planner-card:hover {{ transform:translateY(-3px); box-shadow:0 10px 28px rgba(13,71,161,.12); border-color:var(--blue); }}
    .planner-card-header {{ background:linear-gradient(135deg,#0D47A1,#1976D2); color:var(--white); padding:16px 20px; display:flex; align-items:center; gap:12px; }}
    .planner-avatar {{ width:40px; height:40px; border-radius:50%; background:rgba(255,255,255,.2); display:flex; align-items:center; justify-content:center; font-size:18px; font-weight:700; flex-shrink:0; }}
    .planner-name {{ font-size:16px; font-weight:700; }}
    .planner-card-body {{ padding:18px 20px; }}
    .planner-stats {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:14px; }}
    .p-stat {{ background:var(--blue-light); border-radius:10px; padding:10px 12px; text-align:center; }}
    .p-stat-num {{ font-size:20px; font-weight:800; color:#0D47A1; }}
    .p-stat-label {{ font-size:11px; color:var(--gray); margin-top:2px; }}
    .planner-highlight {{ font-size:13px; color:#555; line-height:1.6; }}
    .planner-highlight strong {{ color:var(--blue); }}
    .planner-score {{ display:flex; align-items:center; gap:8px; margin-top:12px; padding-top:12px; border-top:1px solid #EEF2FF; }}
    .score-badge {{ font-size:13px; font-weight:700; padding:4px 10px; border-radius:8px; }}
    .score-high {{ background:#E8F5E9; color:#2E7D32; }}
    .score-mid {{ background:#FFF8E1; color:#F57F17; }}
    .score-low {{ background:#FFEBEE; color:#C62828; }}
    .score-text {{ font-size:12px; color:var(--gray); }}
    .view-detail {{ display:inline-flex; align-items:center; gap:4px; margin-top:14px; font-size:13px; font-weight:600; color:var(--blue); }}
    footer {{ background:var(--navy); color:rgba(255,255,255,.4); text-align:center; padding:28px 24px; font-size:12px; letter-spacing:1px; }}
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <div class="header-nav">
      <a class="back-btn" href="planner-daily-reports.html">&larr; 日报列表</a>
    </div>
    <h1>规划师日报 &middot; {DATE_CN}（{WEEKDAY_CN}）</h1>
    <div class="subtitle">基于企业微信聊天记录 &middot; AI 自动分析</div>
  </div>
</header>

<main>
  <div class="summary-row">
    <div class="summary-card"><div class="summary-num">{len(all_planner_data)}</div><div class="summary-label">活跃规划师</div></div>
    <div class="summary-card"><div class="summary-num">{total_clients}</div><div class="summary-label">沟通客户数</div></div>
    <div class="summary-card"><div class="summary-num">{total_msgs}</div><div class="summary-label">消息总量</div></div>
    <div class="summary-card"><div class="summary-num">{avg_score:.1f}</div><div class="summary-label">团队平均分</div></div>
  </div>

  <div class="section-title">各规划师沟通概况</div>
  <div class="planner-grid">
{planner_cards_html}
  </div>
</main>

<footer>保心上人 &middot; CONFIDENTIAL &middot; INTERNAL USE ONLY</footer>

</body>
</html>"""
    return html


# ── 更新列表页 ────────────────────────────────────────
def update_list_page(total_planners, total_clients):
    """在 planner-daily-reports.html 的 <!-- NEW_ENTRY_HERE --> 标记后插入新条目。"""
    day = YESTERDAY.day
    month = YESTERDAY.month

    new_entry = f"""
      <a class="report-card" href="planner-daily-{DATE_STR}.html">
        <div class="card-icon">💬</div>
        <div class="card-body">
          <div class="card-title">规划师日报 &nbsp;&middot;&nbsp; {month}月{day}日（{WEEKDAY_CN}）</div>
          <div class="card-meta">
            <span>📅 {DATE_CN}</span>
            <span>🤖 AI 自动分析</span>
          </div>
        </div>
        <div class="card-stats">
          <span class="stat-badge">{total_planners} 位规划师</span>
          <span class="stat-badge">{total_clients} 位客户</span>
        </div>
        <div class="card-arrow">›</div>
      </a>
"""

    with open(LIST_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    marker = "<!-- NEW_ENTRY_HERE -->"
    if marker in content:
        content = content.replace(marker, marker + new_entry)
    else:
        print("WARNING: marker not found in list page")
        content = content.replace("</main>", new_entry + "\n</main>")

    with open(LIST_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Updated list page: {LIST_FILE}")


# ── 主流程 ────────────────────────────────────────────
def main():
    print(f"=== 规划师日报生成 · 分析日期: {DATE_STR} ({WEEKDAY_CN}) ===")

    # 检查配置
    if not CORP_ID or not CHAT_SECRET or not PRIVATE_KEY_PEM:
        print("ERROR: Missing required environment variables.")
        print("  Required: WEWORK_CORP_ID, WEWORK_CHAT_SECRET, WEWORK_PRIVATE_KEY")
        sys.exit(1)

    if not PLANNER_MAP:
        print("ERROR: WEWORK_PLANNER_MAP is empty.")
        sys.exit(1)

    print(f"Planners configured: {list(PLANNER_MAP.values())}")

    # 1. 初始化企业微信 SDK
    print("Step 1: Initializing WeWork Finance SDK...")
    sdk = WeWorkFinanceSDK(CORP_ID, CHAT_SECRET, PRIVATE_KEY_PEM, SDK_PATH)

    # 2. 拉取昨日聊天记录
    print("Step 2: Fetching yesterday's messages...")
    messages_by_planner = fetch_yesterday_messages(sdk)

    if not messages_by_planner:
        print("No messages found for yesterday. Exiting.")
        sdk.destroy()
        return

    # 3. 用 Claude 分析每个规划师的聊天记录
    print("Step 3: Analyzing with Claude API...")
    claude_client = anthropic.Anthropic()
    all_planner_data = []

    for uid, messages in messages_by_planner.items():
        planner_name = PLANNER_MAP.get(uid, uid)
        print(f"  Analyzing {planner_name} ({len(messages)} messages)...")

        chat_text = format_messages_for_analysis(messages)
        analysis = analyze_planner_chats(planner_name, chat_text, claude_client)
        analysis["userid"] = uid
        all_planner_data.append(analysis)

    # 4. 生成 HTML 页面
    print("Step 4: Generating HTML pages...")

    # 4a. 每个规划师的详情页
    for d in all_planner_data:
        uid = d["userid"]
        detail_html = generate_planner_detail_page(d)
        detail_path = os.path.join(PROJECT_ROOT, f"planner-detail-{DATE_STR}-{uid}.html")
        with open(detail_path, "w", encoding="utf-8") as f:
            f.write(detail_html)
        print(f"  Written: {detail_path}")

    # 4b. 当日总览页
    overview_html = generate_date_overview_page(all_planner_data)
    overview_path = os.path.join(PROJECT_ROOT, f"planner-daily-{DATE_STR}.html")
    with open(overview_path, "w", encoding="utf-8") as f:
        f.write(overview_html)
    print(f"  Written: {overview_path}")

    # 5. 更新列表页
    print("Step 5: Updating list page...")
    total_clients = sum(d["total_clients"] for d in all_planner_data)
    update_list_page(len(all_planner_data), total_clients)

    # 清理
    sdk.destroy()
    print("=== Done ===")


if __name__ == "__main__":
    main()
