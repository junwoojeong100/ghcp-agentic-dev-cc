#!/usr/bin/env python3
"""Check local review-draft media and render contact sheets for visual inspection."""
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'production/ghcp-live'
OUT = ROOT / 'deliverables/ghcp-live'
DRAFT = '검토용 초안 · 승인 연속 영상 미확보'


def execute(args):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return result.stdout


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def contact_sheet(paths, destination, columns=3):
    width, height = 640, 385
    sheet = Image.new('RGB', (columns * width, ((len(paths) + columns - 1) // columns) * height), '#101923')
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(paths):
        with Image.open(path) as source:
            image = source.convert('RGB')
            image.thumbnail((width, 360))
            x, y = (i % columns) * width, (i // columns) * height
            sheet.paste(image, (x + (width - image.width) // 2, y + 25))
            draw.text((x + 8, y + 5), path.stem, fill='white')
    sheet.save(destination)


def main():
    video = OUT / 'ghcp-cxo-demo-ko.mp4'
    srt = OUT / 'ghcp-cxo-demo-ko.srt'
    pptx = OUT / 'ghcp-agentic-development.pptx'
    pdf = OUT / 'ghcp-agentic-development.pdf'
    for path in [video, srt, pptx, pdf]:
        assert path.is_file() and path.stat().st_size, f'Missing output: {path}'
    facts = json.loads((RUN / 'facts.json').read_text())
    timeline = json.loads((RUN / 'timeline.json').read_text())
    assert facts['production']['status'] == 'review-draft'
    assert facts['production']['captureComplete'] is False
    assert facts['requirementsSatisfied'] is False and facts['finalAdoption'] == 'not-approved'
    assert facts['openFindings']
    for record in timeline['sourceEvidence']:
        path = (ROOT / record['path']).resolve()
        assert path.is_relative_to(ROOT) and path.is_file(), f'Missing source: {path}'
        assert digest(path) == record['sha256'], f'Source changed after rendering: {path}'
    metadata = json.loads(execute(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', video]))
    v = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
    a = next(s for s in metadata['streams'] if s['codec_type'] == 'audio')
    assert (v['width'], v['height'], v['codec_name']) == (1920, 1080, 'h264')
    assert a['codec_name'] == 'aac' and v['r_frame_rate'] == '30/1'
    duration = float(v['duration'])
    assert abs(duration - timeline['duration']) < .001
    assert digest(video) == timeline['video']['sha256']
    execute(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', video, '-f', 'null', '-'])
    def seconds(value):
        h, m, s, ms = map(int, re.split('[:,]', value))
        return h * 3600 + m * 60 + s + ms / 1000
    cues = re.findall(r'(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})\n([^\n]+)', srt.read_text())
    assert cues
    previous = 0
    for start, end, _ in cues:
        start, end = seconds(start), seconds(end)
        assert previous <= start < end <= duration + .03
        previous = end
    ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with ZipFile(pptx) as z:
        slides = sorted(n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n))
        notes = [n for n in z.namelist() if re.fullmatch(r'ppt/notesSlides/notesSlide\d+\.xml', n)]
        assert len(slides) == len(notes) == 12
        text = [''.join(ET.fromstring(z.read(n)).itertext()) for n in slides]
        assert all(DRAFT in t for t in text), 'Missing draft notice on a slide'
        assert any('보완 필요 1건' in t for t in text)
        assert sum(len(ET.fromstring(z.read(n)).findall('.//a:t', ns)) for n in slides) > 100
        rels = '\n'.join(z.read(n).decode() for n in z.namelist() if n.endswith('.rels'))
        assert 'Target="ghcp-cxo-demo-ko.mp4"' in rels, 'Video relative link missing'
    inspection = RUN / 'inspection'
    inspection.mkdir(exist_ok=True)
    pages = []
    with fitz.open(pdf) as document:
        assert len(document) == 12
        for i, page in enumerate(document):
            assert '검토용 초안' in page.get_text(), f'PDF page {i + 1} missing draft text'
            target = inspection / f'slide-{i + 1:02d}.png'
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(target)
            pages.append(target)
    contact_sheet(pages, inspection / 'slides-contact.png')
    frames = []
    for i, scene in enumerate(timeline['scenes']):
        target = inspection / f'video-{i + 1:02d}-{scene["id"]}.png'
        midpoint = (scene['start'] + scene['end']) / 2
        execute(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', f'{midpoint:.3f}', '-i', video, '-frames:v', 1, target])
        assert target.is_file() and target.stat().st_size
        frames.append(target)
    contact_sheet(frames, inspection / 'video-contact.png')
    report = {
        'status': 'technical-checks-passed', 'productionStatus': 'review-draft',
        'durationSeconds': duration, 'video': {'width': 1920, 'height': 1080, 'codec': 'h264', 'audio': 'aac', 'fps': 30, 'fullDecode': 'passed'},
        'subtitles': {'cues': len(cues), 'timing': 'passed'},
        'presentation': {'slides': 12, 'notes': 12, 'editableText': True, 'relativeMp4Link': True, 'pdfPages': 12, 'draftNoticeEveryPage': True},
        'sourceMapping': {'scenes': len(timeline['scenes']), 'factsAndTimelinePresent': True},
        'files': [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in [video, srt, pptx, pdf]],
        'visualInspection': 'pending contact sheet inspection',
        'browserPlayback': 'not-yet-checked', 'humanListeningPerformed': False, 'powerPointLinkPlayback': 'not-checked',
        'captureComplete': False, 'requirementsSatisfied': False, 'openFindings': 1,
        'note': 'Media checks do not establish complete filming, application correctness, final adoption or publication approval.'
    }
    (RUN / 'deliverable-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
