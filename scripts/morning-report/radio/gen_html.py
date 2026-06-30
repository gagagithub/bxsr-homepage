# -*- coding: utf-8 -*-
"""audio/timeline.json + content/meta.json → index.html（电台版完整片）。
用法: python3 gen_html.py <project_dir>
资产: assets/bg.jpg  assets/nsc-heavy.woff2  assets/outro.mp4  audio/narration.mp3
"""
import json, os, sys

P = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
tl = json.load(open(os.path.join(P, "audio", "timeline.json"), encoding="utf-8"))
meta = json.load(open(os.path.join(P, "content", "meta.json"), encoding="utf-8"))
NARR = tl["total"]; lines = tl["lines"]
OUTRO = 4.3
SCENE = round(NARR + 0.15, 3)
OUT_AT = SCENE
DATE_CN = meta["date_cn"]; WEEK_CN = meta["week_cn"]
PUB_DOT = meta["pub_date"].replace("-", ".")

# 字幕: 持续到下一句开始, 最后一句到旁白结束, 留 0.05 间隙防重叠
subs = []
for k, m in enumerate(lines):
    s = m["start"]
    nxt = lines[k + 1]["start"] if k + 1 < len(lines) else NARR
    subs.append((k, round(s, 3), round(max(0.5, nxt - s - 0.05), 3), m["text"]))

def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

sub_html = "\n".join(
    f'  <div id="sub{k}" class="clip" data-start="{s}" data-duration="{d}" data-track-index="6"><div class="subtitle">{esc(t)}</div></div>'
    for k, s, d, t in subs)
sub_anim = "\n".join(
    f"    tl.from('#sub{k} .subtitle', {{ y:24, opacity:0, duration:0.22, ease:'power2.out' }}, {s});"
    for k, s, d, t in subs)
bars = "".join('<span class="wf-bar"></span>' for _ in range(24))

HTML = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>vid</title></head><body>
<div id="vid" data-composition-id="vid" data-start="0" data-width="1080" data-height="1920">
  <div id="bgclip" class="clip" data-start="0" data-duration="{SCENE}" data-track-index="0"><img class="bg" src="assets/bg.jpg"></div>
  <div id="topgrad" class="clip" data-start="0" data-duration="{SCENE}" data-track-index="1"><div class="top-grad"></div></div>
  <div id="botgrad" class="clip" data-start="0" data-duration="{SCENE}" data-track-index="2"><div class="bot-grad"></div></div>
  <div id="hero" class="clip" data-start="0" data-duration="{SCENE}" data-track-index="3">
    <div class="brandrow">
      <div class="logo">保</div>
      <div class="bname">保心上人<span class="ben">PROTECT · WEALTH · ADVISORY</span></div>
      <div class="datebadge">今日简讯<br><b>{PUB_DOT}</b></div>
    </div>
    <div class="bigtitle">财经晨报</div>
    <div class="slogan">让 天 下 人 老 有 所 养</div>
  </div>
  <div id="bullets" class="clip" data-start="0" data-duration="{SCENE}" data-track-index="4">
    <div class="tags"><span>全球市场</span><span>内地财经</span><span>一听速览</span></div>
  </div>
  <div id="player" class="clip" data-start="0" data-duration="{SCENE}" data-track-index="5">
    <div class="player">
      <div class="nowplaying">正在播放 · 中文版 {PUB_DOT}</div>
      <div class="wave">{bars}</div>
      <div class="ctrl">
        <svg class="ic ic-dn" viewBox="0 0 48 48"><path d="M24 8 V36 M24 36 L14 26 M24 36 L34 26" stroke="#f3d99b" stroke-width="4.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <svg class="ic" viewBox="0 0 48 48"><path d="M22 12 L8 24 L22 36 Z M42 12 L28 24 L42 36 Z" fill="#fff"/></svg>
        <svg class="ic ic-pause" viewBox="0 0 48 48"><rect x="14" y="9" width="8" height="30" rx="3" fill="#fff"/><rect x="26" y="9" width="8" height="30" rx="3" fill="#fff"/></svg>
        <svg class="ic" viewBox="0 0 48 48"><path d="M6 12 L20 24 L6 36 Z M26 12 L40 24 L26 36 Z" fill="#fff"/></svg>
        <svg class="ic ic-dots" viewBox="0 0 48 48"><circle cx="10" cy="24" r="4" fill="#f3d99b"/><circle cx="24" cy="24" r="4" fill="#f3d99b"/><circle cx="38" cy="24" r="4" fill="#f3d99b"/></svg>
      </div>
    </div>
  </div>
{sub_html}
  <div id="footer" class="clip" data-start="0" data-duration="{SCENE}" data-track-index="7">
    <div class="footer"><div class="show">财 经 晨 报</div><div class="ep">{DATE_CN} {WEEK_CN}</div></div>
  </div>
  <audio id="narr" data-start="0" data-duration="{NARR}" data-track-index="8" src="audio/narration.mp3" data-volume="1"></audio>
  <video id="outro" data-start="{OUT_AT}" data-duration="{OUTRO}" data-track-index="0" src="assets/outro.mp4" muted playsinline></video>
  <audio id="outro-audio" data-start="{OUT_AT}" data-duration="{OUTRO}" data-track-index="1" src="assets/outro.mp4" data-volume="0.85"></audio>
  <style>
    @font-face {{ font-family:'NSC Heavy'; font-weight:900; src:url('assets/nsc-heavy.woff2') format('woff2'); }}
    #vid {{ background:#06112a; overflow:hidden; font-family:'NSC Heavy',sans-serif; }}
    #vid .bg {{ width:100%; height:100%; object-fit:cover; transform-origin:50% 46%; }}
    #vid video {{ width:100%; height:100%; object-fit:cover; }}
    #vid .clip {{ position:absolute; inset:0; }}
    #vid .top-grad {{ position:absolute; top:0; left:0; width:100%; height:820px; background:linear-gradient(to bottom, rgba(8,20,48,.92), rgba(8,20,48,.5) 55%, transparent); pointer-events:none; }}
    #vid .bot-grad {{ position:absolute; bottom:0; left:0; width:100%; height:760px; background:linear-gradient(to top, rgba(8,20,48,.9), rgba(8,20,48,.4) 52%, transparent); pointer-events:none; }}
    #vid .brandrow {{ position:absolute; top:62px; left:54px; right:54px; display:flex; align-items:center; }}
    #vid .logo {{ width:74px; height:74px; border:4px solid #e8c66a; border-radius:14px; color:#e8c66a; font-size:46px; line-height:66px; text-align:center; font-weight:900; }}
    #vid .bname {{ margin-left:20px; color:#fff; font-size:40px; font-weight:900; letter-spacing:2px; display:flex; flex-direction:column; }}
    #vid .ben {{ font-size:15px; color:#9fb6da; letter-spacing:4px; font-weight:400; margin-top:4px; }}
    #vid .datebadge {{ margin-left:auto; background:#e8c66a; color:#0c2350; border-radius:14px; padding:10px 20px; font-size:22px; line-height:1.25; text-align:center; font-weight:900; }}
    #vid .datebadge b {{ font-size:30px; }}
    #vid .bigtitle {{ position:absolute; top:188px; left:0; width:100%; text-align:center; color:#fff; font-size:148px; font-weight:900; letter-spacing:10px; text-shadow:0 10px 30px rgba(0,0,0,.6); }}
    #vid .slogan {{ position:absolute; top:372px; left:0; width:100%; text-align:center; color:#e8c66a; font-size:40px; font-weight:900; letter-spacing:12px; text-shadow:0 5px 16px rgba(0,0,0,.6); }}
    #vid .tags {{ position:absolute; top:470px; left:0; width:100%; display:flex; gap:20px; justify-content:center; }}
    #vid .tags span {{ background:rgba(232,198,106,.16); border:2px solid rgba(232,198,106,.7); color:#f3d99b; font-size:30px; font-weight:900; padding:8px 26px; border-radius:40px; letter-spacing:3px; }}
    #vid .player {{ position:absolute; top:780px; left:0; width:100%; }}
    #vid .nowplaying {{ text-align:center; color:#cfe0ff; font-size:30px; font-weight:900; letter-spacing:3px; margin-bottom:30px; opacity:.9; }}
    #vid .wave {{ display:flex; align-items:center; justify-content:center; gap:9px; height:180px; }}
    #vid .wf-bar {{ display:block; width:10px; height:140px; border-radius:7px; background:linear-gradient(to bottom,#ffffff,#e8c66a); box-shadow:0 0 16px rgba(232,198,106,.55); }}
    #vid .ctrl {{ display:flex; align-items:center; justify-content:center; gap:44px; margin-top:40px; }}
    #vid .ic {{ width:82px; height:82px; filter:drop-shadow(0 4px 12px rgba(0,0,0,.7)); }}
    #vid .ic-pause {{ width:110px; height:110px; }}
    #vid .ic-dn {{ width:58px; height:58px; opacity:.95; }}
    #vid .ic-dots {{ width:62px; height:62px; opacity:.95; }}
    #vid .subtitle {{ position:absolute; bottom:500px; left:0; width:100%; text-align:center; font-size:62px; font-weight:900; color:#fff; -webkit-text-stroke:8px #0a1c3d; paint-order:stroke fill; letter-spacing:1px; line-height:1.22; padding:0 70px; box-sizing:border-box; text-shadow:0 4px 14px rgba(0,0,0,.8); }}
    #vid .footer {{ position:absolute; bottom:150px; left:0; width:100%; text-align:center; }}
    #vid .show {{ font-size:70px; font-weight:900; color:#fff; letter-spacing:16px; text-shadow:0 6px 18px rgba(0,0,0,.7); }}
    #vid .ep {{ margin-top:14px; font-size:44px; font-weight:900; color:#e8c66a; letter-spacing:8px; text-shadow:0 5px 16px rgba(0,0,0,.7); }}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <script>
    window.__timelines = window.__timelines || {{}};
    const tl = gsap.timeline({{ paused: true }});
    const SCENE={SCENE};
    tl.fromTo('#vid .bg', {{ scale:1.05 }}, {{ scale:1.13, duration:SCENE, ease:'none' }}, 0);
    tl.from('#vid .logo', {{ scale:0.4, opacity:0, duration:0.4, ease:'back.out(2)' }}, 0.1);
    tl.from('#vid .bname', {{ x:-30, opacity:0, duration:0.4, ease:'power2.out' }}, 0.25);
    tl.from('#vid .datebadge', {{ x:30, opacity:0, duration:0.4, ease:'power2.out' }}, 0.35);
    tl.from('#vid .bigtitle', {{ y:40, opacity:0, scale:0.92, duration:0.5, ease:'back.out(1.6)' }}, 0.3);
    tl.from('#vid .slogan', {{ opacity:0, duration:0.5, ease:'power2.out' }}, 0.7);
    tl.from('#vid .tags span', {{ y:20, opacity:0, duration:0.34, stagger:0.1, ease:'power2.out' }}, 0.9);
    tl.from('#vid .nowplaying', {{ opacity:0, duration:0.4 }}, 1.0);
    tl.from('#vid .ctrl .ic', {{ scale:0.4, opacity:0, duration:0.32, stagger:0.06, ease:'back.out(2)' }}, 1.05);
    tl.from('#vid .footer .show', {{ y:26, opacity:0, duration:0.4, ease:'power2.out' }}, 1.1);
    tl.from('#vid .footer .ep', {{ y:20, opacity:0, duration:0.4, ease:'power2.out' }}, 1.25);
{sub_anim}
    const TOTAL=SCENE;
    const wbars = gsap.utils.toArray('#vid .wf-bar');
    wbars.forEach((b, i) => {{
      const hi  = 0.42 + ((i * 41) % 58) / 100;
      const dur = 0.30 + ((i * 17) % 5) * 0.08;
      const off = ((i * 23) % 7) * 0.05;
      const reps = Math.floor((TOTAL - off) / dur) - 1;
      gsap.set(b, {{ transformOrigin:'50% 50%', scaleY:0.28 }});
      tl.to(b, {{ scaleY:hi, duration:dur, ease:'sine.inOut', repeat:reps, yoyo:true }}, off);
    }});
    window.__timelines['vid'] = tl;
  </script>
</div>
</body></html>'''
open(os.path.join(P, "index.html"), "w", encoding="utf-8").write(HTML)
print(f"index.html  SCENE={SCENE}s  subs={len(subs)}  ({DATE_CN} {WEEK_CN})")
