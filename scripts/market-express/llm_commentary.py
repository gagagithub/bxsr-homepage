# -*- coding: utf-8 -*-
"""全自动文案：把 data.json 的真实数字喂 DeepSeek，生成市场综述/解读/焦点/保险视角。
带合规护栏。输出 commentary.json，供 render.py 使用。生产同理(+内地新闻喂akshare真实头条)。
"""
import os
BASE = os.path.dirname(os.path.abspath(__file__))
import json, re, sys, urllib.request

YML = os.environ.get("BAOXIN_DEV_YML", os.path.join(BASE, "..", "..", "service", "src", "main", "resources", "application-dev.yml"))
def get_key():
    txt = open(YML, encoding="utf-8", errors="ignore").read()
    m = re.search(r"apiKey:\s*(sk-[A-Za-z0-9_\-]+)", txt)
    return m.group(1) if m else None

D = json.load(open(f"{BASE}/data.json"))

def r(v, n=2):          return None if v is None else round(v, n)   # None安全
def f(d, k="day_pct"):  return r(d.get(k), 2)
fx = D["forex"]
snap = {
    "指数": {kk: {"点": r(D["indices"][kk]["cur"], 2), "日涨跌%": f(D["indices"][kk]),
                 "YTD%": f(D["indices"][kk], "ytd_pct")} for kk in D["indices"]},
    "大宗": {kk: {"价": r(D["commodities"][kk]["cur"], 2), "日涨跌%": f(D["commodities"][kk])}
            for kk in D["commodities"] if D["commodities"][kk].get("cur") is not None},
    "美债收益率%": {kk: {"水平": r(D["yields"][kk].get("level"), 4), "bp变动": r(D["yields"][kk].get("bp"), 2)} for kk in D["yields"]},
    "外汇": {k: v for k, v in {"人民币USDCNY": r(fx["CNH"]["cur"], 4), "美元指数DXY": r(fx["DXY"]["cur"], 2)}.items() if v is not None},
    "七姐妹": {kk: {"日%": f(D["mag7"][kk]), "YTD%": f(D["mag7"][kk], "ytd_pct")} for kk in D["mag7"]},
}

SYS = """你是持牌保险家族办公室「保心上人」的市场研究员，为规划师写每日《隔夜市场速递》图文文案。
读者是规划师及其高净值客户。语言专业、克制、口语化但不浮夸。

【合规硬约束，违反作废】
1. 只复述给定数据中的客观事实，绝不编造任何未给出的数字、政策、监管口径。
2. 禁止任何收益承诺/预测性保证；禁止"稳赚""保本高收益""存款搬家""躺赚"等话术。
3. 涉及保险/储蓄的表述用"配置参考""可关注"等中性措辞，不得构成销售要约。
4. 中国习惯红涨绿跌；涨用"涨/升"、跌用"跌/回落"。
5. 不出现具体保险产品名称或公司名。"""

USER = f"""今日(数据截至前一交易日收盘)真实行情如下(JSON)：
{json.dumps(snap, ensure_ascii=False)}

请严格输出如下 JSON（不要任何多余文字、不要markdown代码块）：
{{
  "lead": "60-90字市场综述，点名三大指数涨跌幅，提及当日主线(科技股/防御/通胀数据等)，可用<b class='up'>+x%</b>或<b class='down'>-x%</b>标红绿",
  "focus_banner": "30字内一句话焦点，关键词可<b>加粗</b>",
  "focus_box": "30字内焦点(同义另写)，关键词<b>加粗</b>",
  "interpret": [
    {{"k":"美股逻辑","t":"55字内"}},
    {{"k":"大宗商品","t":"55字内"}},
    {{"k":"美债与汇率","t":"55字内"}},
    {{"k":"后续关注","t":"55字内，列1-2个后续数据/事件"}}
  ],
  "insure": [
    {{"ic":"💵","tx":"把美元利率行情翻译成对美元储蓄险/分红险客户的配置参考，60字内，<b>关键短语加粗</b>"}},
    {{"ic":"📈","tx":"把美股表现翻译成对分红实现率/美元基金账户的影响，60字内"}},
    {{"ic":"🛡️","tx":"避险/资产配置视角的一句话，60字内"}}
  ]
}}"""

key = os.environ.get("DEEPSEEK_API_KEY") or get_key()
if not key:
    print("!! 未找到 DeepSeek key", file=sys.stderr); sys.exit(1)

payload = json.dumps({
    "model": "deepseek-chat",
    "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": USER}],
    "temperature": 0.5, "max_tokens": 1500,
}).encode("utf-8")

req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=payload,
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=90) as r:
    resp = json.loads(r.read().decode())
content = resp["choices"][0]["message"]["content"].strip()
content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
data = json.loads(content)
json.dump(data, open(f"{BASE}/commentary.json", "w"),
          ensure_ascii=False, indent=2)
print("AI 文案已生成 commentary.json：")
print(json.dumps(data, ensure_ascii=False, indent=2))
