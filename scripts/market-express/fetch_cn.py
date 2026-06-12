# -*- coding: utf-8 -*-
"""隔夜市场速递·取数(内地可直连源, akshare)。替代 Yahoo/FRED(内地被墙)。
覆盖:三大指数+DXY/中美国债/LPR/Mag7/油金/人民币/中国国债/实时新闻。输出 data.json(同schema)+内地字段。
所有调用带 signal.alarm 硬超时 + 重试(akshare 偶发 RemoteDisconnected)。"""
import os
BASE = os.path.dirname(os.path.abspath(__file__))
import socket; socket.setdefaulttimeout(20)
import signal, json, time, sys
import akshare as ak
from datetime import datetime, timezone, timedelta

OUT = f"{BASE}/data.json"

class TO(Exception): pass
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TO()))
def retry(label, fn, tries=3, secs=20):
    last = None
    for k in range(tries):
        try:
            signal.alarm(secs); r = fn(); signal.alarm(0); return r
        except Exception as e:
            signal.alarm(0); last = e; time.sleep(1.2)
    print(f"!! {label} 失败: {type(last).__name__}: {str(last)[:80]}", file=sys.stderr)
    return None

def year(ts):  # 'YYYY-..' 或 Timestamp
    s = str(ts); return int(s[:4])

def series_metrics(dates, closes):
    """末值/前值/YTD基准(上一年最后收盘)。"""
    pairs = [(str(d)[:10], float(c)) for d, c in zip(dates, closes) if c == c and c is not None]
    if len(pairs) < 2: return None
    cur_d, cur = pairs[-1]; _, prev = pairs[-2]
    cy = int(cur_d[:4])
    prior = [c for d, c in pairs if int(d[:4]) < cy]
    base = prior[-1] if prior else pairs[0][1]
    return dict(cur=cur, prev=prev, day_pct=(cur-prev)/prev*100,
                ytd_pct=(cur-base)/base*100, cur_d=cur_d)

out = {"generated_utc": datetime.now(timezone.utc).isoformat()}

# ---- 1. 三大指数 + 美元指数(现价/日涨跌); 指数主走新浪, 此处为DXY+兜底 ----
g = retry("index_global_spot_em", lambda: ak.index_global_spot_em(), tries=4)
def gpick(name):
    if g is None: return (None, None)
    r = g[g["名称"] == name]
    return (float(r["最新价"].iloc[0]), float(r["涨跌幅"].iloc[0])) if len(r) else (None, None)
idx_now = {k: gpick(n) for k, n in {"DJI":"道琼斯","GSPC":"标普500","IXIC":"纳斯达克"}.items()}
dxy_now = gpick("美元指数")

# ---- 2. 指数 YTD(新浪历史) ----
SINA_IDX = {"DJI":".DJI", "GSPC":".INX", "IXIC":".IXIC"}
out["indices"] = {}
for k, sym in SINA_IDX.items():
    df = retry(f"index_us_stock_sina {sym}", lambda s=sym: ak.index_us_stock_sina(symbol=s))
    cur, day = idx_now[k]
    ytd = None
    if df is not None and len(df):
        m = series_metrics(df["date"], df["close"])
        if m:
            ytd = m["ytd_pct"]
            if cur is None: cur, day = m["cur"], m["day_pct"]
    out["indices"][k] = dict(cur=cur, day_pct=day, ytd_pct=ytd)
    print(f"指数 {k}: cur={cur} day={day} ytd={ytd}")

# ---- 3. 中美国债收益率(含中国10年) ----
bd = retry("bond_zh_us_rate", lambda: ak.bond_zh_us_rate())
out["yields"] = {}
out["cn_bond"] = {}
if bd is not None and len(bd) >= 2:
    last, prev = bd.iloc[-1], bd.iloc[-2]
    def yld(col):
        try: return float(last[col]), (float(last[col]) - float(prev[col]))*100
        except: return None, None
    for key, col in {"Y1":"美国国债收益率2年","Y10":"美国国债收益率10年","Y30":"美国国债收益率30年"}.items():
        lv, bp = yld(col); out["yields"][key] = dict(level=lv, bp=bp)
    cn10_lv, cn10_bp = yld("中国国债收益率10年")
    cn30_lv, _ = yld("中国国债收益率30年")
    out["cn_bond"] = dict(cn10=cn10_lv, cn10_bp=cn10_bp, cn30=cn30_lv, asof=str(last["日期"])[:10])
    print(f"美债 2/10/30: {out['yields']}")
    print(f"中国国债10年: {cn10_lv} ({cn10_bp:+.2f}bp)" if cn10_lv else "中国国债: NA")

# ---- 4. LPR ----
lpr = retry("macro_china_lpr", lambda: ak.macro_china_lpr())
out["lpr"] = {}
if lpr is not None and len(lpr):
    r = lpr.dropna(subset=["LPR1Y"]).iloc[-1]
    out["lpr"] = dict(y1=float(r["LPR1Y"]), y5=float(r["LPR5Y"]), date=str(r["TRADE_DATE"])[:10])
    print(f"LPR: {out['lpr']}")

# ---- 5. Mag7(新浪历史, 日+YTD) ----
M7 = ["AAPL","AMZN","MSFT","GOOGL","NVDA","TSLA","META"]
out["mag7"] = {}
for tk in M7:
    df = retry(f"stock_us_daily {tk}", lambda t=tk: ak.stock_us_daily(symbol=t), tries=2)
    if df is None and tk == "GOOGL":
        df = retry("stock_us_daily GOOG", lambda: ak.stock_us_daily(symbol="GOOG"), tries=2)
    m = series_metrics(df["date"], df["close"]) if df is not None and len(df) else None
    out["mag7"][tk] = dict(cur=m["cur"], day_pct=m["day_pct"], ytd_pct=m["ytd_pct"]) if m else None
    print(f"Mag7 {tk}: {'%.2f%% / YTD %.2f%%' % (m['day_pct'], m['ytd_pct']) if m else 'NA'}")

# ---- 6. 油/金(国际期货现价+日涨跌; YTD 暂无历史→None) ----
fg = retry("futures_global_spot_em", lambda: ak.futures_global_spot_em())
out["commodities"] = {}
def fpick(core, lo, hi):
    """按名取当月连续合约 + 价格区间合理性校验(防取错品种)。"""
    if fg is None: return None, None, None
    nm = fg["名称"].astype(str)
    bad = ("豆","菜","棕","沪","燃","汽","沥青","甲醇","乙二醇","布伦特" if core=="原油" else "X")
    r = fg[nm.str.contains(core, na=False) & nm.str.contains("当月连续", na=False) & fg["最新价"].notna()]
    for _, row in r.iterrows():
        if any(b in str(row["名称"]) for b in bad): continue
        v = float(row["最新价"])
        if lo <= v <= hi:
            return v, float(row["涨跌幅"]), str(row["名称"])
    return None, None, None
wti  = fpick("原油", 30, 200)     # WTI ~ $88
gold = fpick("黄金", 1500, 7000)  # COMEX ~ $4200
out["commodities"]["WTI"]  = dict(cur=wti[0],  day_pct=wti[1],  ytd_pct=None, src=wti[2])
out["commodities"]["GOLD"] = dict(cur=gold[0], day_pct=gold[1], ytd_pct=None, src=gold[2])
print(f"油: {wti}  金: {gold}")

# ---- 7. 人民币(中行牌价, USD/CNY) + 美元指数 ----
_today = datetime.now().strftime("%Y%m%d")
_start = (datetime.now() - timedelta(days=20)).strftime("%Y%m%d")
boc = retry("currency_boc_sina", lambda: ak.currency_boc_sina(symbol="美元", start_date=_start, end_date=_today))
out["forex"] = {}
cny = None
if boc is not None and len(boc):
    r = boc.dropna(subset=["中行汇买价"]).iloc[-1]
    cny = float(r["中行汇买价"]) / 100.0  # 牌价以"百"计, 取最新一日中行汇买价
out["forex"]["CNH"] = dict(cur=cny, day_pct=0.0)  # 中行价无现成日涨跌, 置0(展示为"较前日")
out["forex"]["DXY"] = dict(cur=dxy_now[0], day_pct=dxy_now[1])
print(f"人民币(中行): {cny}  美元指数: {dxy_now}")

# ---- 8. 内地实时新闻(新浪全球财经) ----
news = retry("stock_info_global_sina", lambda: ak.stock_info_global_sina())
out["cn_news_raw"] = []
if news is not None and len(news):
    for _, r in news.head(20).iterrows():
        out["cn_news_raw"].append({"time": str(r["时间"]), "text": str(r["内容"])})
    print(f"新闻 {len(out['cn_news_raw'])} 条, 例: {out['cn_news_raw'][0]['text'][:40]}")

# ---- 9. 内地关键利率(给内地块) ----
cn_rates = []
if out["cn_bond"].get("cn10"):
    cn_rates.append(dict(lab="中国10年期国债收益率", val=f"{out['cn_bond']['cn10']:.4f}", unit="%",
                         note=f"{out['cn_bond']['cn10_bp']:+.1f}bp", cls=("down" if out['cn_bond']['cn10_bp']<0 else "up")))
if out["lpr"]:
    cn_rates.append(dict(lab="LPR  1年 / 5年以上", val=f"{out['lpr']['y1']:.2f} / {out['lpr']['y5']:.2f}", unit="%", note="月度", cls="flat"))
# 存款挂牌利率 / 分红险预定利率上限: 监管半静态, 后台维护(此处占位真实近似值)
cn_rates.append(dict(lab="大行3年期定存(挂牌)", val="1.50", unit="%", note="半静态·后台维护", cls="down"))
cn_rates.append(dict(lab="分红险预定利率上限", val="2.00", unit="%", note="监管口径·后台维护", cls="down"))
out["cn_rates_real"] = cn_rates

# 数据日期
out["data_date"] = out["indices"]["GSPC"]["cur"] and (
    out["cn_bond"].get("asof") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
if not isinstance(out["data_date"], str):
    out["data_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=2)
print("\n已写", OUT)
