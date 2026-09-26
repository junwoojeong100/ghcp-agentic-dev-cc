#!/usr/bin/env python3
"""Verify finished (final, non-draft) ghcp-live retake deliverables.

Checks facts.json (requirementsSatisfied/openFindings/production.captureComplete),
integrity of the original terminal capture manifest and the timeline
sourceEvidence (SHA256), delivered MP4 (h264/aac/1920x1080/30fps, expected
duration, full ffmpeg decode), SRT cue bounds, PPTX slide/notes counts and its
relative MP4 link, PDF page count with no rendering errors, and renders contact
sheets (all pages + one frame per scene) for manual visual inspection.

This script never claims visual inspection, human listening, or native
PowerPoint link playback were performed: those report fields stay
'pending' / False / 'not-checked'. Any failed check is a hard error (exit 1).
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import fitz
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
RUN_BASE = ROOT / 'production/ghcp-live'
OUT_BASE = ROOT / 'deliverables/ghcp-live'
EXPECTED_SLIDES = 16


def under(path_arg, base, label):
    p = Path(path_arg)
    p = p if p.is_absolute() else ROOT / p
    p = p.resolve()
    if p != base and base not in p.parents:
        raise SystemExit(f'{label} must be inside {base}: got {p}')
    if not p.is_dir():
        raise SystemExit(f'{label} is not a directory: {p}')
    return p


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


def verify_entries(entries, label):
    assert entries, f'{label} manifest is empty or missing'
    for record in entries:
        path = (ROOT / record['path']).resolve()
        assert path.is_relative_to(ROOT) and path.is_file(), f'{label}: missing source {path}'
        assert digest(path) == record['sha256'], f'{label}: changed after rendering {path}'
    return len(entries)


def contact_sheet(paths, destination, columns=4):
    width, height = 480, 290
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * width, rows * height), '#101923')
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(paths):
        with Image.open(path) as source:
            image = source.convert('RGB')
            image.thumbnail((width, 265))
            x, y = (i % columns) * width, (i // columns) * height
            sheet.paste(image, (x + (width - image.width) // 2, y + 20))
            draw.text((x + 6, y + 4), path.stem, fill='white')
    sheet.save(destination)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True, help='Run directory inside production/ghcp-live')
    parser.add_argument('--output-dir', required=True, help='Output directory inside deliverables/ghcp-live')
    args = parser.parse_args()
    return under(args.run_dir, RUN_BASE, '--run-dir'), under(args.output_dir, OUT_BASE, '--output-dir')


def main():
    run_dir, out_dir = parse_args()
    video = out_dir / 'ghcp-cxo-demo-ko.mp4'
    srt = out_dir / 'ghcp-cxo-demo-ko.srt'
    pptx = out_dir / 'ghcp-agentic-development.pptx'
    pdf = out_dir / 'ghcp-agentic-development.pdf'
    for path in [video, srt, pptx, pdf]:
        assert path.is_file() and path.stat().st_size, f'Missing output: {path}'

    facts = json.loads((run_dir / 'facts.json').read_text())
    timeline = json.loads((run_dir / 'timeline.json').read_text())

    assert facts['requirementsSatisfied'] is True, 'requirementsSatisfied is not true'
    assert facts['openFindings'] == [], f'openFindings not empty: {facts["openFindings"]!r}'
    assert facts['production']['captureComplete'] is True, 'production.captureComplete is not true'

    manifest = facts.get('production', {}).get('captureManifest') or facts.get('captureManifest')
    capture_count = verify_entries(manifest, 'Original terminal capture')
    evidence_count = verify_entries(timeline['sourceEvidence'], 'Timeline source evidence')

    metadata = json.loads(execute(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', video]))
    v = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
    a = next(s for s in metadata['streams'] if s['codec_type'] == 'audio')
    assert (v['width'], v['height'], v['codec_name']) == (1920, 1080, 'h264'), 'Video stream mismatch'
    assert a['codec_name'] == 'aac' and v['r_frame_rate'] == '30/1', 'Audio codec or fps mismatch'
    duration = float(v['duration'])
    assert abs(duration - timeline['duration']) < .001, 'Duration does not match timeline'
    assert digest(video) == timeline['video']['sha256'], 'Delivered video sha256 mismatch'
    execute(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', video, '-f', 'null', '-'])

    def seconds(value):
        h, m, s, ms = map(int, re.split('[:,]', value))
        return h * 3600 + m * 60 + s + ms / 1000

    cues = re.findall(r'(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})\n([^\n]+)', srt.read_text())
    assert cues, 'No SRT cues found'
    previous = 0
    for start, end, _ in cues:
        start, end = seconds(start), seconds(end)
        assert previous <= start < end <= duration + .03, 'SRT cue out of bounds'
        previous = end

    with ZipFile(pptx) as z:
        slides = sorted(n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n))
        notes = [n for n in z.namelist() if re.fullmatch(r'ppt/notesSlides/notesSlide\d+\.xml', n)]
        assert len(slides) == len(notes) == EXPECTED_SLIDES, (
            f'Expected {EXPECTED_SLIDES} slides+notes, got {len(slides)}/{len(notes)}')
        rels = '\n'.join(z.read(n).decode() for n in z.namelist() if n.endswith('.rels'))
        assert f'Target="{video.name}"' in rels, 'Relative MP4 link missing or does not match delivered video'

    inspection = run_dir / 'inspection'
    inspection.mkdir(exist_ok=True)
    pages = []
    with fitz.open(pdf) as document:
        assert len(document) == EXPECTED_SLIDES, f'Expected {EXPECTED_SLIDES} PDF pages, got {len(document)}'
        for i, page in enumerate(document):
            try:
                page.get_text()
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            except Exception as exc:
                raise RuntimeError(f'PDF page {i + 1} rendering error: {exc}') from exc
            target = inspection / f'slide-{i + 1:02d}.png'
            pixmap.save(target)
            pages.append(target)
    contact_sheet(pages, inspection / 'slides-contact.png')

    frames = []
    for i, scene in enumerate(timeline['scenes']):
        target = inspection / f'video-{i + 1:02d}-{scene["id"]}.png'
        midpoint = (scene['start'] + scene['end']) / 2
        execute(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', f'{midpoint:.3f}', '-i', video, '-frames:v', 1, target])
        assert target.is_file() and target.stat().st_size, f'Frame capture failed for scene {scene["id"]}'
        frames.append(target)
    contact_sheet(frames, inspection / 'video-contact.png')

    report = {
        'status': 'technical-checks-passed',
        'productionStatus': facts.get('production', {}).get('status', 'final'),
        'durationSeconds': duration,
        'video': {'width': 1920, 'height': 1080, 'codec': 'h264', 'audio': 'aac', 'fps': 30, 'fullDecode': 'passed'},
        'subtitles': {'cues': len(cues), 'timing': 'passed'},
        'presentation': {'slides': EXPECTED_SLIDES, 'notes': EXPECTED_SLIDES, 'relativeMp4Link': True, 'pdfPages': EXPECTED_SLIDES},
        'sourceMapping': {
            'scenes': len(timeline['scenes']),
            'terminalCaptureEntries': capture_count,
            'sourceEvidenceEntries': evidence_count,
        },
        'files': [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in [video, srt, pptx, pdf]],
        'visualInspection': 'pending',
        'humanListeningPerformed': False,
        'powerPointLinkPlayback': 'not-checked',
        'captureComplete': True,
        'requirementsSatisfied': True,
        'openFindings': 0,
        'note': ('Automated technical checks only. Visual inspection, audio listening and native PowerPoint '
                 'link playback are NOT performed by this script and remain pending human review.'),
    }
    (run_dir / 'final-retake-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        sys.exit(1)
