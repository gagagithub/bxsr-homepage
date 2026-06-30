#!/usr/bin/env bash
# 财经晨报·电台视频版 一键出片: sections.json(+pub_date) → renders/morning-radio.mp4 + renders/poster.jpg
# 用法: bash make_video.sh [PUB_DATE=YYYY-MM-DD] [SECTIONS=../sections.json]
# 依赖: python3(dashscope/Pillow/fonttools) · node/npx hyperframes · ffmpeg · ~/.aliyun/nls.env(TTS凭证)
set -euo pipefail
cd "$(dirname "$0")"

PUB="${1:-$(date +%Y-%m-%d)}"
SECTIONS="${2:-../sections.json}"
SPEED="${RADIO_SPEED:-1.05}"

echo "▶ 1/6 生成口播稿 ($PUB)"
python3 build_script.py "$SECTIONS" . "$PUB"

echo "▶ 2/6 CosyVoice 逐句配音"
python3 tts_gen.py .

echo "▶ 3/6 拼接旁白 + 时间轴 (speed=$SPEED)"
python3 build_audio.py . "$SPEED"

echo "▶ 4/6 生成 index.html"
python3 gen_html.py .

echo "▶ 5/6 渲染 mp4 (1080x1920 / 30fps) + 压制 (CRF28 faststart, 静态画面压到 ~25MB)"
mkdir -p renders
# lint 仅作提示, 不阻断出片(新增 advisory 规则不该让每日视频静默断档)
npx hyperframes lint || echo "⚠ lint 有告警(不阻断渲染), 见上"
npx hyperframes render -q high -f 30 -o renders/_raw.mp4
ffmpeg -v error -y -i renders/_raw.mp4 -c:v libx264 -preset veryfast -crf 28 \
  -pix_fmt yuv420p -c:a aac -b:a 96k -movflags +faststart renders/morning-radio.mp4
rm -f renders/_raw.mp4

echo "▶ 6/6 生成视频封面海报 poster.jpg (帧 + ▶ 播放钮)"
ffmpeg -v error -y -ss 8 -i renders/morning-radio.mp4 -frames:v 1 renders/_frame.jpg
ffmpeg -v error -y -i renders/_frame.jpg -i assets/play-btn.png \
  -filter_complex "[0:v][1:v]overlay=(W-w)/2:(H-h)/2" -frames:v 1 renders/poster.jpg
rm -f renders/_frame.jpg

DUR=$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 renders/morning-radio.mp4)
SZ=$(du -h renders/morning-radio.mp4 | cut -f1)
echo "✓ 完成: renders/morning-radio.mp4  时长 ${DUR}s  大小 ${SZ}"
echo "✓ 封面: renders/poster.jpg"
