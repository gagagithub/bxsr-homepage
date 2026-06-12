# -*- coding: utf-8 -*-
"""读取 data.json -> 渲染 index.html (保心上人 隔夜市场速递·保险适配版)。
红涨绿跌(中国习惯)。市场解读/焦点/保险视角为基于真实数据的文案,生产环境改AI生成。
"""
import os
BASE = os.path.dirname(os.path.abspath(__file__))
import json
from datetime import datetime
from jinja2 import Template

D = json.load(open(f"{BASE}/data.json"))

def cls(v):       return "flat" if v is None else ("up" if v >= 0 else "down")
def ar(v):        return "·"    if v is None else ("▲" if v >= 0 else "▼")
def pct(v):       return "—"    if v is None else f"{v:+.2f}%"
def comma3(v):    return "—"    if v is None else f"{v:,.3f}"
def comma2(v):    return "服务器接入" if v is None else f"{v:,.2f}"

# ---- 指数 ----
IDX_NAME = {"DJI": "道琼斯指数", "GSPC": "标普500指数", "IXIC": "纳斯达克指数"}
indices = []
for k in ("DJI", "GSPC", "IXIC"):
    d = D["indices"][k]
    indices.append(dict(name=IDX_NAME[k], pts=comma3(d["cur"]), pct=pct(d["day_pct"]),
                        cls=cls(d["day_pct"]), ar=ar(d["day_pct"]),
                        ytd=pct(d["ytd_pct"]), ytd_cls=cls(d["ytd_pct"])))

# ---- 大宗 ----
COMM = {"WTI": ("WTI原油", "美元/桶 · 当月连续"), "GOLD": ("COMEX黄金", "美元/盎司 · 当月连续")}
commodities = []
for k in ("WTI", "GOLD"):
    d = D["commodities"][k]; nm, unit = COMM[k]
    commodities.append(dict(name=nm, unit=unit, price=comma2(d["cur"]), pct=pct(d["day_pct"]),
                            cls=cls(d["day_pct"]), ar=ar(d["day_pct"]),
                            ytd=pct(d["ytd_pct"]), ytd_cls=cls(d["ytd_pct"])))

# ---- 美债 ----
YL = {"Y1": "2年期", "Y10": "10年期", "Y30": "30年期"}   # bond_zh_us_rate 给美债2/10/30年
yields = []
for k in ("Y1", "Y10", "Y30"):
    d = D["yields"][k]
    lvl = "—" if d.get("level") is None else f"{d['level']:.4f}"
    bp = "—" if d.get("bp") is None else f"{d['bp']:+.2f}bp"
    yields.append(dict(label=YL[k], level=lvl, bp=bp, cls=cls(d.get("bp")), ar=ar(d.get("bp"))))

# ---- 外汇 ----
def fxv(v, n): return "—" if v is None else f"{v:.{n}f}"
forex = []
cnh = D["forex"]["CNH"]; dxy = D["forex"]["DXY"]
forex.append(dict(label="人民币 (USD/CNY·中行)", value=fxv(cnh["cur"], 4),
                  chg=pct(cnh["day_pct"]), cls=cls(cnh["day_pct"]), ar=ar(cnh["day_pct"])))
forex.append(dict(label="美元指数 (DXY)", value=fxv(dxy["cur"], 2),
                  chg=pct(dxy["day_pct"]), cls=cls(dxy["day_pct"]), ar=ar(dxy["day_pct"])))

# ---- 七姐妹 ----
M7 = [("AAPL","苹果"),("AMZN","亚马逊"),("MSFT","微软"),("GOOGL","谷歌A"),
      ("NVDA","英伟达"),("TSLA","特斯拉"),("META","Meta")]
mag7 = []
for tk, cn in M7:
    d = D["mag7"][tk]
    mag7.append(dict(tk=f"({tk})", cn=cn, pct=pct(d["day_pct"]), cls=cls(d["day_pct"]),
                     ytd=pct(d["ytd_pct"]), ytd_cls=cls(d["ytd_pct"])))

# ======== 文案(基于真实数据;生产改AI) ========
dji, sp, nq = D["indices"]["DJI"], D["indices"]["GSPC"], D["indices"]["IXIC"]
y10, y30 = D["yields"]["Y10"], D["yields"]["Y30"]
aapl, gold = D["mag7"]["AAPL"], D["commodities"]["GOLD"]

lead = ('美股三大指数涨跌不一，道指微涨 <b class="up">%s</b>，标普 <b class="down">%s</b>，'
        '纳指 <b class="down">%s</b>。市场在关键通胀数据出炉前维持谨慎，资金从高估值科技股'
        '撤出、转向防御与资源板块；苹果在 WWDC 后遭获利了结领跌，拖累科技股。'
        ) % (pct(dji["day_pct"]), pct(sp["day_pct"]), pct(nq["day_pct"]))

focus_banner = ('科技股延续弱势，苹果 WWDC 后遭抛售领跌，防御与资源板块逆势走强，'
                '<b>市场静待今晚美国 5 月 CPI 数据。</b>')

focus_box = ('科技股延续弱势，苹果 WWDC 后遭抛售领跌，防御板块与资源股逆势走强，'
             '市场静待 <b>今晚美国 5 月 CPI 数据。</b>')

interpret = [
    dict(k="美股逻辑", t='资金在关键通胀数据公布前撤离高估值科技股，转向防御性公用事业与资源股；'
        '苹果 WWDC 利好出尽、股价重挫 <b class="down">%s</b> 拖累纳指。' % pct(aapl["day_pct"])),
    dict(k="大宗商品", t='原油因全球经济放缓担忧走弱，金价回落至 %s 美元、实际利率回升令避险属性'
        '暂歇，但中长期配置逻辑未变。' % comma2(gold["cur"])),
    dict(k="美债与汇率", t='收益率曲线小幅走平，10 年期 %s%%、30 年期 %s%%；人民币'
        ' %s 附近企稳。' % (fxv(y10["level"],4), fxv(y30["level"],4), fxv(cnh["cur"],4))),
    dict(k="后续关注", t='① 今晚公布的美国 5 月 CPI；② 初请失业金等次级数据；'
        '③ 下周 FOMC 议息会议前进入静默期，官员表态留意。'),
]

# ======== 内地理财风向(示例·生产接 akshare 实时) ========
slogan = "保心上人 · 让天下人老有所养"

cn_rates = [
    dict(lab="中国10年期国债收益率", val="1.72", unit="%", note="-1.2bp", cls="down"),
    dict(lab="LPR  1年 / 5年以上",   val="3.00 / 3.50", unit="%", note="持平", cls="flat"),
    dict(lab="大行3年期定存(挂牌)",  val="1.50", unit="%", note="近期下调", cls="down"),
    dict(lab="分红险预定利率上限",   val="2.00", unit="%", note="或下调", cls="down"),
]
cn_news = [
    dict(date="06-09", tx="储蓄国债（电子式）本月开售，3 年期票面利率约 <b>1.93%</b>，额度紧俏、当日售罄。"),
    dict(date="06-08", tx="多家国有大行再度下调存款挂牌利率，3 年期定存正式步入 <b>“1 字头”</b>时代。"),
    dict(date="06-05", tx="业内传分红险预定利率上限或进一步下调，长期<b>锁息窗口</b>持续收窄。"),
]

# 保险配置视角(把行情翻译成对保单客户的意义)
insure = [
    dict(ic="💵", tx='<b>美元利率仍处高位：</b>10 年期 %s%%、30 年期 %s%% → '
        '美元储蓄险 / 分红险预定收益与红利仍在高位窗口，适合客户锁息配置。'
        % (fxv(y10["level"],2), fxv(y30["level"],2))),
    dict(ic="📈", tx='<b>美股稳健向上：</b>标普 YTD %s、纳指 YTD %s → '
        '利好分红险实现率与投连 / 美元基金账户表现。'
        % (pct(sp["ytd_pct"]), pct(nq["ytd_pct"]))),
    dict(ic="🛡️", tx='<b>避险压舱不变：</b>金价短线回落但 YTD 配置逻辑未改，'
        '高净值客户资产配置仍宜保留黄金 + 美元双锚。'),
]

# 用真实内地利率(akshare)覆盖示例
if D.get("cn_rates_real"):
    cn_rates = D["cn_rates_real"]

# 若有 AI 文案(commentary.json) 则覆盖硬编码(全自动模式)
import os
_cj = f"{BASE}/commentary.json"
if os.path.exists(_cj):
    _c = json.load(open(_cj))
    lead = _c.get("lead", lead)
    focus_banner = _c.get("focus_banner", focus_banner)
    focus_box = _c.get("focus_box", focus_box)
    interpret = _c.get("interpret", interpret)
    insure = _c.get("insure", insure)
    print("[已套用 AI 文案 commentary.json]")

html = Template(open(f"{BASE}/template.html").read()).render(
    pub_date=os.environ.get("MX_PUB_DATE") or datetime.now().strftime("%Y-%m-%d"),
    data_date=D["data_date"], slogan=slogan,
    indices=indices, commodities=commodities, yields=yields, forex=forex,
    cn_rates=cn_rates, cn_news=cn_news,
    mag7=mag7, insure=insure, interpret=interpret,
    lead=lead, focus_banner=focus_banner, focus_box=focus_box,
)
open(f"{BASE}/index.html", "w").write(html)
print("已写 index.html")
