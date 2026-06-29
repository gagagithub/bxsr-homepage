#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐句 CosyVoice 配音 -> 每句 mp3 + audio/lines.json（含原始时长）。
用法：python3 tts_gen.py <project_dir>
读：<project_dir>/content/script.json  = [[para, "句子"], ...]（para 整数，用于段落停顿）
出：<project_dir>/audio/line{NN}.mp3 + audio/lines.json
凭证：~/.aliyun/nls.env（DASHSCOPE_API_KEY / DASHSCOPE_VOICE，默认 longxiang）。
坑：① SDK 每次 .call() 后 WebSocket 关闭，必须每句新建 synthesizer。
    ② 句中逗号会让 CosyVoice 停顿——不想停的地方把逗号去掉。"""
import os, sys, json, subprocess, dashscope
from dashscope.audio.tts_v2 import SpeechSynthesizer

PROJ = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
with open(os.path.expanduser("~/.aliyun/nls.env")) as f:
    for line in f:
        if "=" in line:
            k, v = line.strip().split("=", 1); os.environ[k] = v
dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
voice = os.environ.get("DASHSCOPE_VOICE", "longxiang")

OUT = os.path.join(PROJ, "audio"); os.makedirs(OUT, exist_ok=True)
LINES = json.load(open(os.path.join(PROJ, "content", "script.json")))

def dur(p):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", p]).decode().strip())

meta = []
for i, (para, text) in enumerate(LINES):
    syn = SpeechSynthesizer(model="cosyvoice-v1", voice=voice)
    p = os.path.join(OUT, f"line{i:02d}.mp3")
    open(p, "wb").write(syn.call(text))
    d = dur(p)
    meta.append({"i": i, "para": para, "text": text, "file": f"line{i:02d}.mp3", "dur": round(d, 3)})
    print(f"[{i:02d}] {d:5.2f}s  {text}")
json.dump(meta, open(os.path.join(OUT, "lines.json"), "w"), ensure_ascii=False, indent=2)
print(f"\n总(无间隔) {sum(m['dur'] for m in meta):.2f}s  {len(meta)} 句  voice={voice}")
