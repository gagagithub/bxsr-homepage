# -*- coding: utf-8 -*-
"""稳健理财·产品朋友圈素材 —— 样例生成器(B方案一次性试跑)。
输入: 真实对标账号爆款产品角度(取自系统已抓的对标账号动向) + 当前市场利率背景(akshare自动)。
处理: DeepSeek 改写成保心上人规划师自己的朋友圈产品营销文案(钩子标题+卖点+私信CTA, 带合规口径)。
仅本地跑、出 samples 给用户拍板风格, 不碰生产、不推送、不调 TikHub。
"""
import os, re, json, sys, requests

BASE = os.path.dirname(os.path.abspath(__file__))

# ---------- DeepSeek key(复用 application-dev.yml) ----------
def get_deepseek_key():
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k
    yml = os.path.join(BASE, "..", "..", "..", "baoxin", "service", "src", "main", "resources", "application-dev.yml")
    try:
        txt = open(yml, encoding="utf-8").read()
        m = re.search(r"deepseek:\s*\n\s*apiKey:\s*(\S+)", txt)
        if m:
            return m.group(1).strip()
    except Exception as e:
        print("读 application-dev.yml 失败:", e, file=sys.stderr)
    return None

# ---------- 真实对标爆款角度(取自对标账号动向 6月真实标题) ----------
BENCHMARK_ANGLES = [
    "55岁提前退休，每月领7000养老金",
    "30万这样规划，退休后年领2万8",
    "200万投保，本金不动每年吃息3.3%",
    "5年美金定存，5年总收益25%",
    "手里60万这样存，每年多领66000",
    "深扒这款增额寿，4.4%收益是真的吗？",
    "50款选1款，香港储蓄险到底买哪个才不后悔？",
    "香港保底3%、结算7.25%的万能账户，值得买吗？",
    "倒计时7天！高预期分红险必买清单",
    "苏州爷爷花50万美金买香港保险：钱只给孙子",
    "港险年金：收益写进合同",
    "比公务员还稳的养老方案来了",
]

# ---------- 市场利率背景(akshare 自动) ----------
def fetch_rates():
    out = {}
    try:
        sys.path.insert(0, "/Users/cuiwei/cuiwei_ai/baoxin/_ops/market-express")
        import akshare as ak
        df = ak.bond_china_yield(start_date="20260601", end_date="20260630")
        gz = df[df["曲线名称"] == "中债国债收益率曲线"].sort_values("日期")
        if len(gz):
            last = gz.iloc[-1]
            out["国债收益率"] = {"3年": float(last["3年"]), "5年": float(last["5年"]), "10年": float(last["10年"]), "30年": float(last["30年"])}
        lpr = ak.macro_china_lpr().sort_values("TRADE_DATE").iloc[-1]
        out["LPR"] = {"1年": float(lpr["LPR1Y"]), "5年以上": float(lpr["LPR5Y"])}
    except Exception as e:
        out["error"] = str(e)
    return out

PROMPT = """你是保心上人(内地保险经纪团队)的一名资深保险规划师,擅长写朋友圈引流文案。
下面是同行爆款账号最近发布的高赞产品角度(已验证能吸引客户),以及当前真实市场利率背景。

请把这些角度,改写成 3 条【保心上人规划师自己的】朋友圈产品营销文案,目标是:客户刷到后心动、主动私信规划师咨询。

硬性要求:
1. 每条结构 = 【钩子标题(用数字/痛点/悬念,像同行那样抓眼球)】+ 2~3行大白话卖点 + 结尾一句私信钩子(给一个私信关键词)+ 署名"林老师 · 保心上人"。
2. 3 条覆盖不同品类: 一条养老/年金现金流, 一条"利率下行→锁定"(可引用下方国债/LPR背景做对比烘托), 一条香港储蓄险/美金资产。
3. 合规口径: 香港保险必须注明"须本人赴港投保、内地不销售,仅做科普与方案对比"; 一切收益用"演示/预期/参考"措辞, 不写"保证收益"; 不照抄同行原句,要改写成自己的话。
4. 中老年友好: 句子短、白话、少术语。
5. 输出 JSON: {"posts":[{"category":"品类","title":"钩子标题","body":"正文(可含换行\\n)","cta":"私信关键词","tag":"配图建议(一句话)"}]}  只输出JSON。

【同行爆款产品角度】
%s

【当前市场利率背景(真实)】
%s
"""

def main():
    key = get_deepseek_key()
    if not key:
        print("!! 没拿到 DeepSeek key", file=sys.stderr); sys.exit(1)
    rates = fetch_rates()
    angles = "\n".join("· " + a for a in BENCHMARK_ANGLES)
    prompt = PROMPT % (angles, json.dumps(rates, ensure_ascii=False))
    resp = requests.post("https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": "deepseek-chat", "response_format": {"type": "json_object"},
              "messages": [{"role": "user", "content": prompt}], "temperature": 1.1},
        timeout=120)
    content = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    json.dump({"rates": rates, "posts": data.get("posts", [])}, open(f"{BASE}/samples.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("市场利率背景:", json.dumps(rates, ensure_ascii=False))
    print("=" * 56)
    for i, p in enumerate(data.get("posts", []), 1):
        print(f"\n【样例 {i}·{p.get('category','')}】")
        print(p.get("title", ""))
        print(p.get("body", ""))
        print(f"👉 私信「{p.get('cta','')}」   —— 林老师 · 保心上人")
        print(f"   (配图建议: {p.get('tag','')})")

if __name__ == "__main__":
    main()
