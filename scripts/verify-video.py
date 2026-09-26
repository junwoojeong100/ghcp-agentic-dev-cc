#!/usr/bin/env python3
"""Check the finished film and prepare scene-by-scene visual review images."""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / 'deliverables/ghcp-cxo-demo-ko.mp4'
QA = ROOT / 'production/video-qa'


def call(args):
    return subprocess.run([str(v) for v in args], capture_output=True, text=True, check=True)


def main():
    QA.mkdir(exist_ok=True)
    metadata = json.loads(call(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', VIDEO]).stdout)
    video = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in metadata['streams'] if s['codec_type'] == 'audio')
    assert (video['width'], video['height'], video['codec_name'], video['pix_fmt']) == (1920, 1080, 'h264', 'yuv420p')
    assert video['r_frame_rate'] == video['avg_frame_rate'] == '30/1'
    assert int(video['nb_frames']) == 9000
    assert audio['codec_name'] == 'aac'
    assert abs(float(metadata['format']['duration']) - 300) < .05
    assert abs(float(video['start_time'])) < .01 and abs(float(audio['start_time'])) < .01
    atoms = []
    with VIDEO.open('rb') as stream:
        while True:
            header = stream.read(8)
            if not header:
                break
            size, kind = struct.unpack('>I4s', header)
            header_size = 8
            if size == 1:
                size = struct.unpack('>Q', stream.read(8))[0]
                header_size = 16
            atoms.append(kind.decode('ascii'))
            if size == 0:
                break
            assert size >= header_size
            stream.seek(size - header_size, 1)
    assert atoms.index('moov') < atoms.index('mdat'), 'MP4 not faststart'
    decode = call(['ffmpeg', '-v', 'error', '-xerror', '-i', VIDEO, '-f', 'null', '-'])
    assert not decode.stderr.strip(), decode.stderr
    analysis = call(['ffmpeg', '-hide_banner', '-nostats', '-i', VIDEO,
                     '-vf', 'blackdetect=d=0.1:pix_th=0.10', '-af', 'volumedetect', '-f', 'null', '-'])
    assert 'black_start:' not in analysis.stderr, 'Unexpected black frames'
    mean = float(re.search(r'mean_volume: ([\-\d.]+) dB', analysis.stderr).group(1))
    peak = float(re.search(r'max_volume: ([\-\d.]+) dB', analysis.stderr).group(1))
    assert -35 < mean < -10 and -6 < peak < 0, (mean, peak)
    timeline = json.loads((ROOT / 'production/timeline.json').read_text())
    assert len(timeline) == 23 and abs(timeline[-1]['end'] - 300) < .01
    cues = (ROOT / 'deliverables/ghcp-cxo-demo-ko.srt').read_text()
    times = re.findall(r'(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)', cues)
    previous = 0
    for groups in times:
        a = [int(v) for v in groups[:4]]
        b = [int(v) for v in groups[4:]]
        start = a[0]*3600 + a[1]*60 + a[2] + a[3]/1000
        end = b[0]*3600 + b[1]*60 + b[2] + b[3]/1000
        assert previous <= start < end <= 300
        previous = end
    assert len(times) >= 60
    thumb_w, thumb_h = 640, 360
    font = ImageFont.truetype('/System/Library/Fonts/AppleSDGothicNeo.ttc', 18)
    frames = []
    for scene in timeline:
        at = min(scene['end'] - .25, scene['start'] + 3)
        path = QA / f"scene-{scene['index'] + 1:02d}.jpg"
        call(['ffmpeg', '-v', 'error', '-y', '-ss', f'{at:.3f}', '-i', VIDEO, '-frames:v', '1', '-q:v', '2', path])
        frames.append((scene, at, path))
    for group_start in range(0, len(frames), 6):
        group = frames[group_start:group_start+6]
        sheet = Image.new('RGB', (thumb_w*2, (thumb_h+36)*3), '#DCE4EC')
        draw = ImageDraw.Draw(sheet)
        for offset, (scene, at, path) in enumerate(group):
            x, y = (offset % 2)*thumb_w, (offset//2)*(thumb_h+36)
            image = Image.open(path).resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            sheet.paste(image, (x, y))
            draw.text((x+10,y+thumb_h+8), f"{scene['index']+1:02d} · {at:.1f}s · {scene['title'].replace(chr(10),' ')}", font=font, fill='#172B45')
        sheet.save(QA / f'contact-{group_start//6+1}.jpg', quality=92)
    result = {
        'durationSeconds': float(metadata['format']['duration']),
        'resolution': '1920x1080', 'videoCodec': video['codec_name'], 'pixelFormat': video['pix_fmt'],
        'frameRate': video['avg_frame_rate'], 'videoFrames': int(video['nb_frames']),
        'audioCodec': audio['codec_name'], 'meanVolumeDb': mean, 'peakVolumeDb': peak,
        'faststart': True, 'decodeErrors': 0, 'blackFrameIntervals': 0,
        'subtitleCues': len(times), 'timelineScenes': len(timeline),
        'sha256': hashlib.sha256(VIDEO.read_bytes()).hexdigest(),
        'visualReview': 'Contact sheets prepared; coordinator inspection recorded separately.',
        'audioListening': 'Not performed by this automated check; check narration on the presentation computer.',
    }
    (QA / 'metadata.json').write_text(json.dumps(metadata, indent=2))
    (QA / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (QA / 'analysis.log').write_text(analysis.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
