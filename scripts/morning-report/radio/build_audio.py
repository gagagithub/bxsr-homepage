#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拼接逐句配音 -> audio/narration.mp3，输出 audio/timeline.json（每句在成片中的起止秒）。
用法：python3 build_audio.py <project_dir> [SPEED]
SPEED：变速倍数（保音高），默认 1.12。longxiang 默认偏慢，1.08~1.15 更紧凑。
timeline.json 是后面所有环节（bg 分镜 / 字幕 / hero / 图表）的唯一时间真值。"""
import os, sys, json, subprocess

PROJ = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
SPEED = float(sys.argv[2]) if len(sys.argv) > 2 else 1.12
GAP, PARA_GAP, LEAD, TAIL = 0.12, 0.40, 0.25, 0.40

AUD = os.path.join(PROJ, "audio")
lines = json.load(open(os.path.join(AUD, "lines.json")))
tmp = os.path.join(AUD, "_tmp"); os.makedirs(tmp, exist_ok=True)

def dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
        "format=duration", "-of", "default=nk=1:nw=1", p]).decode().strip())
def sil(d, p):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "anullsrc=r=22050:cl=mono", "-t", str(d), "-q:a", "9", p], check=True)
def speedup(src, dst, f):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter:a",
        f"atempo={f}", "-c:a", "libmp3lame", "-q:a", "2", dst], check=True)

seq, timeline, t = [], [], 0.0
lead = os.path.join(tmp, "lead.mp3"); sil(LEAD, lead); seq.append(lead); t += LEAD
prev = None
for m in lines:
    if prev is not None:
        g = os.path.join(tmp, f"gap{m['i']}.mp3"); sil(PARA_GAP if m["para"] != prev else GAP, g)
        seq.append(g); t += (PARA_GAP if m["para"] != prev else GAP)
    sp = os.path.join(tmp, f"sp{m['i']:02d}.mp3"); speedup(os.path.join(AUD, m["file"]), sp, SPEED)
    d = dur(sp); start = t; seq.append(sp); t += d
    timeline.append({"i": m["i"], "para": m["para"], "text": m["text"],
                     "start": round(start, 3), "end": round(t, 3)})
    prev = m["para"]
tail = os.path.join(tmp, "tail.mp3"); sil(TAIL, tail); seq.append(tail); t += TAIL

listf = os.path.join(tmp, "concat.txt")
open(listf, "w").write("".join(f"file '{p}'\n" for p in seq))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listf,
    "-c:a", "libmp3lame", "-q:a", "2", os.path.join(AUD, "narration.mp3")], check=True)
json.dump({"total": round(t, 3), "speed": SPEED, "lines": timeline},
          open(os.path.join(AUD, "timeline.json"), "w"), ensure_ascii=False, indent=2)
print(f"✓ narration.mp3 {t:.2f}s  speed={SPEED}")
for m in timeline:
    print(f"  [{m['i']:02d}] {m['start']:6.2f}-{m['end']:6.2f}  {m['text']}")
