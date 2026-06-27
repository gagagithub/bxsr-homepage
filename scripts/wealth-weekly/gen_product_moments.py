# -*- coding: utf-8 -*-
"""稳健理财·产品朋友圈素材 —— 每周生成器(生产版)。
流程:
  1) 角度源 = 线上选题池「对标账号动向」高赞产品标题(零额外 TikHub, 解析已生成页面)
  2) 市场背景 = akshare 国债收益率 + LPR(自动)
  3) DeepSeek 改写成 N 条「保心上人规划师自己的」朋友圈产品营销文案(冲, 带一行护身符)
输出 product_moments.json(供后端存盘 + 后台页渲染 + 企微一键发)。
本脚本只生成内容, 不推送、不调 TikHub。
"""
import os, re, json, sys, time, requests

BASE = os.path.dirname(os.path.abspath(__file__))
TOPICPOOL_URL = os.environ.get("TOPICPOOL_URL", "https://214club.com.cn/creation/topicPool")
N_POSTS = int(os.environ.get("WPM_N", "3"))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}

# 兜底种子角度(线上选题池抓不到时用; 均为真实对标爆款角度)
SEED_ANGLES = [
    "55岁提前退休，每月领7000养老金", "30万这样规划，退休后年领2万8",
    "200万投保，本金不动每年吃息3.3%", "5年美金定存，5年总收益25%",
    "手里60万这样存，每年多领66000", "深扒这款增额寿，4.4%收益是真的吗？",
    "50款选1款，香港储蓄险到底买哪个才不后悔？", "香港保底3%、结算7.25%的万能账户",
    "分红险窗口期倒计时！晚买几天少赚60万", "港险年金：收益写进合同",
]

def fetch_angles():
    try:
        r = requests.get(TOPICPOOL_URL, headers=UA, timeout=25)
        r.encoding = r.apparent_encoding or "utf-8"
        html = r.text
        i = html.find("对标账号动向")
        if i < 0:
            raise ValueError("页面无对标账号动向")
        seg = html[i:i + 12000]
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", seg, re.S)]
        angles = []
        for c in cells:
            # 去掉 #话题标签 与 ♥赞数, 取干净标题
            c = re.split(r"♥", c)[0]
            c = re.sub(r"#\S+", "", c).strip()
            if 8 <= len(c) <= 50 and any(k in c for k in ("养老", "退休", "存", "息", "收益", "分红", "港险", "香港", "年金", "增额", "寿", "万能", "美金", "理财", "保险", "利率")):
                angles.append(c)
        # 去重保序
        seen, uniq = set(), []
        for a in angles:
            if a not in seen:
                seen.add(a); uniq.append(a)
        return uniq[:14] if uniq else SEED_ANGLES
    except Exception as e:
        print(f"⚠ 抓线上选题池角度失败({e}), 用种子角度", file=sys.stderr)
        return SEED_ANGLES

def fetch_rates():
    out = {}
    try:
        sys.path.insert(0, "/Users/cuiwei/cuiwei_ai/baoxin/_ops/market-express")
        import akshare as ak
        df = ak.bond_china_yield(start_date="20260601", end_date="20260631")
        gz = df[df["曲线名称"] == "中债国债收益率曲线"].sort_values("日期")
        if len(gz):
            last = gz.iloc[-1]
            out["国债收益率"] = {"3年": round(float(last["3年"]), 2), "5年": round(float(last["5年"]), 2),
                                  "10年": round(float(last["10年"]), 2), "30年": round(float(last["30年"]), 2),
                                  "日期": str(last["日期"])}
        lpr = ak.macro_china_lpr().sort_values("TRADE_DATE").iloc[-1]
        out["LPR"] = {"1年": float(lpr["LPR1Y"]), "5年以上": float(lpr["LPR5Y"])}
    except Exception as e:
        out["error"] = str(e)
    return out

def get_deepseek_key():
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k
    yml = os.path.join(BASE, "..", "..", "..", "baoxin", "service", "src", "main", "resources", "application-dev.yml")
    try:
        m = re.search(r"deepseek:\s*\n\s*apiKey:\s*(\S+)", open(yml, encoding="utf-8").read())
        return m.group(1).strip() if m else None
    except Exception:
        return None

PROMPT = """你是保心上人(内地保险经纪团队)的资深保险规划师,擅长写朋友圈引流文案。
下面是同行爆款账号最近的高赞产品角度(已验证能吸引客户)和当前真实市场利率背景。
把它们改写成 %d 条【保心上人规划师自己的】朋友圈产品营销文案,目标:客户刷到心动、主动私信规划师。

要求:
1. 每条 = 钩子标题(数字/痛点/悬念,够冲够抓眼球)+ 2~3行大白话卖点 + 私信钩子词。语气可以冲、可以放具体数字(用"演示/预期/参考"措辞)。
2. %d 条覆盖不同品类: 养老年金现金流 / "利率下行→锁定"(引用下方真实国债/LPR做对比) / 香港储蓄险或美金资产 (有富余可加 增额终身寿)。
3. 合规底线(只一行、不削力度): 香港保险注明"须本人赴港投保、内地不销售,仅科普对比"; 收益用"演示/预期"不写"保证"。
4. 中老年友好: 句子短、白话、少术语。
5. 只输出 JSON: {"posts":[{"category":"品类","title":"钩子标题","body":"正文(可含\\n换行)","cta":"私信关键词","tag":"配图建议一句话"}]}

【同行爆款产品角度】
%s

【当前市场利率背景(真实)】
%s
"""

def main():
    angles = fetch_angles()
    rates = fetch_rates()
    key = get_deepseek_key()
    if not key:
        print("!! 没拿到 DeepSeek key", file=sys.stderr); sys.exit(1)
    prompt = PROMPT % (N_POSTS, N_POSTS, "\n".join("· " + a for a in angles), json.dumps(rates, ensure_ascii=False))
    resp = requests.post("https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": "deepseek-chat", "response_format": {"type": "json_object"},
              "messages": [{"role": "user", "content": prompt}], "temperature": 1.1}, timeout=120)
    posts = json.loads(resp.json()["choices"][0]["message"]["content"]).get("posts", [])
    week = os.environ.get("WPM_WEEK") or time.strftime("%Y-%m-%d")
    out = {"week": week, "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
           "rates": rates, "angles_used": angles, "posts": posts}
    json.dump(out, open(f"{BASE}/product_moments.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"已生成 product_moments.json  角度{len(angles)}条  文案{len(posts)}条  利率={json.dumps(rates, ensure_ascii=False)}")
    for i, p in enumerate(posts, 1):
        print(f"  [{i}] {p.get('category','')}: {p.get('title','')}")

if __name__ == "__main__":
    main()
