#!/usr/bin/env python3
"""Export the real plan-decision interval at normal speed, including the wait."""
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'production/ghcp-live/retake-20260926'
TAKE = ROOT / 'evidence/ghcp-live/retake-20260926/raw/headless.1FbXjt'
OUTPUT = ROOT / 'deliverables/ghcp-live/final/ghcp-plan-approval-continuous.mp4'
START, END = 1820, 2200


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def execute(args):
    return subprocess.check_output([str(a) for a in args], stderr=subprocess.PIPE, text=True)


def main():
    manifest_path = TAKE / 'capture.json'
    manifest = json.loads(manifest_path.read_text())
    assert manifest['fixture'] is False and manifest['status'] == 'recorded'
    assert manifest['finalized'] and manifest['videoCopyVerified'] and not manifest['failure']
    raw = Path(manifest['rawVideo']).resolve()
    assert raw.is_relative_to(ROOT) and digest(raw) == manifest['originalVideoSha256']
    assert not OUTPUT.exists(), 'Existing supplement must be inspected before replacement'
    with tempfile.TemporaryDirectory(prefix='.approval-export-', dir=RUN) as tmp:
        tmp = Path(tmp)
        image = Image.new('RGB', (1920, 1080), '#101923')
        draw = ImageDraw.Draw(image)
        font = '/System/Library/Fonts/AppleSDGothicNeo.ttc'
        draw.text((24, 8), '계획 승인 전체 구간 · 대기 포함 · 연속 정속 · 대화의 사용자 선택을 CLI에 전달',
                  font=ImageFont.truetype(font, 25), fill='#F4F7FA')
        draw.text((24, 1048), '실제 Copilot CLI 원본 발췌 · 새 승인 재현 아님 · 음성 없음 · 운영 채택·배포 승인 아님',
                  font=ImageFont.truetype(font, 22), fill='#C6D0DC')
        frame = tmp / 'frame.png'
        image.save(frame)
        encoded = tmp / OUTPUT.name
        execute(['ffmpeg', '-v', 'error', '-nostdin', '-loop', '1', '-framerate', 30, '-i', frame,
                 '-ss', START, '-i', raw, '-filter_complex_threads', 1, '-filter_complex',
                 '[1:v]trim=duration=380,setpts=PTS-STARTPTS,scale=1760:990,setsar=1,fps=30[raw];'
                 '[0:v][raw]overlay=80:50:shortest=1:eof_action=endall,format=yuv420p[out]',
                 '-map', '[out]', '-an', '-frames:v', (END-START)*30, '-c:v', 'libx264',
                 '-preset', 'veryfast', '-crf', 18, '-threads', 4, '-movflags', '+faststart', encoded])
        info = json.loads(execute(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', encoded]))
        video = info['streams'][0]
        assert int(video['nb_frames']) == (END-START)*30 and float(video['duration']) == END-START
        execute(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', encoded, '-f', 'null', '-'])
        with OUTPUT.open('xb') as dest, encoded.open('rb') as source:
            for block in iter(lambda: source.read(1024*1024), b''):
                dest.write(block)
    record = {'status': 'verified', 'source': str(raw.relative_to(ROOT)),
              'sourceSha256': manifest['originalVideoSha256'], 'in': START, 'out': END, 'speed': 1,
              'durationSeconds': END-START, 'fullDecode': 'passed', 'audio': 'none',
              'file': str(OUTPUT.relative_to(ROOT)), 'sha256': digest(OUTPUT),
              'approvalRelayInputId': '07fd04ec-ec6f-4745-9dfc-1496fd1d6700',
              'note': 'Original chronological footage, wait retained; no replay or fabricated approval. Visual source checks: menu at1825s, accepted at2196s. User decision is chat-mediated; still frames alone do not attest consent.'}
    with (RUN / 'approval-supplement.json').open('x') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
