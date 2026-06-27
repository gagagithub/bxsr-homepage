# -*- coding: utf-8 -*-
"""稳健理财·存钱周报 —— 原型抓取器。
本地实测可达的固定源:
  · 东财保险频道 insurance.eastmoney.com  → 保险/分红险/监管 文章(标题+meta摘要,服务端渲染)
  · akshare bond_china_yield             → 中债国债收益率曲线(每日)
  · akshare macro_china_lpr              → LPR 1Y/5Y
仅本地跑、写 wealth_raw.json,不碰生产、不推送。
银行存款挂牌利率/大额存单/高息存款/储蓄国债期次/港险 —— 源未稳,本原型先留空待补(人工或专源)。
"""
import json, re, time
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
OUT = {}

# ---------- 1. 东财保险频道文章 ----------
def fetch_insurance(max_articles=22, fetch_summary=18):
    r = requests.get("https://insurance.eastmoney.com", headers=UA, timeout=20)
    r.encoding = r.apparent_encoding or "utf-8"
    html = r.text
    pairs = re.findall(r'href="(https?://(?:finance|insurance)\.eastmoney\.com/a/\d+\.html)"[^>]*>([^<]{6,60})', html)
    seen, arts = set(), []
    for u, t in pairs:
        if u in seen:
            continue
        seen.add(u)
        arts.append({"title": t.strip(), "url": u})
        if len(arts) >= max_articles:
            break
    # 取 meta 描述(=【标题】完整摘要, 颗粒同晨报)
    for a in arts[:fetch_summary]:
        try:
            ar = requests.get(a["url"], headers=UA, timeout=15)
            ar.encoding = ar.apparent_encoding or "utf-8"
            ah = ar.text
            desc = (re.search(r'<meta name="description" content="(.*?)"', ah, re.S) or [None, ""])[1]
            a["summary"] = re.sub(r"\s+", " ", desc).strip()
            t = (re.search(r'<title>(.*?)</title>', ah, re.S) or [None, ""])[1]
            if not a["title"]:
                a["title"] = re.split(r"[_|-]", t)[0].strip()
        except Exception as e:
            a["summary"] = ""
        time.sleep(0.15)
    return [a for a in arts if a.get("title")]

# ---------- 2. akshare 利率 ----------
def fetch_rates():
    out = {}
    try:
        import akshare as ak
        df = ak.bond_china_yield(start_date="20260601", end_date="20260630")
        gz = df[df["曲线名称"] == "中债国债收益率曲线"].sort_values("日期")
        if len(gz):
            last = gz.iloc[-1]
            out["gov_bond"] = {"date": str(last["日期"]),
                               "y3": float(last["3年"]), "y5": float(last["5年"]),
                               "y10": float(last["10年"]), "y30": float(last["30年"])}
        lpr = ak.macro_china_lpr().sort_values("TRADE_DATE").iloc[-1]
        out["lpr"] = {"date": str(lpr["TRADE_DATE"]), "y1": float(lpr["LPR1Y"]), "y5": float(lpr["LPR5Y"])}
    except Exception as e:
        out["error"] = str(e)
    return out

if __name__ == "__main__":
    OUT["insurance"] = fetch_insurance()
    OUT["rates"] = fetch_rates()
    OUT["generated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(OUT, open("wealth_raw.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"保险文章 {len(OUT['insurance'])} 条(带摘要 {sum(1 for a in OUT['insurance'] if a.get('summary'))})")
    print("利率:", OUT["rates"])
