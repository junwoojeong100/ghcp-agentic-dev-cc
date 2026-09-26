#!/usr/bin/env python3
"""Check the release's recorded hashes without private originals or new execution."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(relative):
    path = (ROOT / relative).resolve()
    require(path.is_relative_to(ROOT), f'Path outside project: {relative}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(relative, expected):
    require(digest(relative) == expected, f'Hash mismatch: {relative}')


def main():
    shared = load('evidence/shareable/manifest.json')
    originals = {}
    for entry in shared['files']:
        require(entry['source'] not in originals, 'Duplicate source mapping')
        verify(entry['publishedCopy'], entry['publishedSha256'])
        originals[entry['source']] = entry

    stages = {name: load(f'evidence/shareable/copilot-run/{name}-record.json')
              for name in ('plan', 'implement', 'review')}
    for name, record in stages.items():
        require(record['exitCode'] == 0 and not record['unexpectedChanges'], f'Stage failed: {name}')
    for name in ('plan', 'review'):
        require(not stages[name]['changedFiles'], f'Read-only stage changed files: {name}')

    tested = load('evidence/shareable/copilot-run/local-test-record.json')
    require(tested['exitCode'] == 0, 'Recorded local tests failed')
    for relative, expected in tested['codeHashes'].items():
        verify(f'demo/run/{relative}', expected)
        verify(f'demo/fallback/{relative}', expected)
        require(stages['implement']['after'][relative] == expected,
                f'Tested file differs from recorded Copilot output: {relative}')
    combined = hashlib.sha256(json.dumps(tested['codeHashes'], sort_keys=True).encode()).hexdigest()
    facts = load('production/facts.json')
    require(combined == tested['codeHash'] == facts['codeHash'], 'Code identifier mismatch')
    require(set(stages['implement']['changedFiles']) == set(facts['changedFiles']), 'Changed-file list mismatch')

    baseline = load('evidence/copilot-run/baseline-hashes.json')
    for relative, expected in baseline.items():
        verify(f'evidence/copilot-run/baseline-snapshot/{relative}', expected)
        require(stages['implement']['before'][relative] == expected, f'Baseline mismatch: {relative}')

    for filename, fact_key in [('local-tests.log', 'tests'), ('baseline-tests.log', 'baselineTests')]:
        text = (ROOT / 'evidence/copilot-run' / filename).read_text()
        totals = {}
        for key in ('tests', 'pass', 'fail', 'skipped', 'cancelled'):
            match = re.search(rf'^# {key} (\d+)$', text, re.M)
            require(match is not None, f'Missing TAP total: {filename}/{key}')
            totals[key] = int(match.group(1))
        require(totals['pass'] == totals['tests'] == facts[fact_key]['passed'], 'Test count mismatch')
        require(totals['fail'] == totals['skipped'] == totals['cancelled'] == 0, 'Recorded tests not all passing')
    browser = load('evidence/copilot-run/browser-checks.json')
    require(browser['passed'] == len(browser['checks']) == facts['browserChecks']['passed'], 'Browser count mismatch')
    require(not browser['pageErrors'] and all(c['passed'] for c in browser['checks']), 'Recorded browser checks failed')

    excerpts = load('production/excerpt-manifest.json')
    for name, entry in excerpts.items():
        verify(f'production/excerpts/{name}.txt', entry['excerptSha256'])
        mapping = originals.get(entry['source'])
        if mapping:
            require(mapping['originalSha256'] == entry['sourceSha256'], f'Excerpt source mapping mismatch: {name}')
        else:
            verify(entry['source'], entry['sourceSha256'])

    release = load('production/release-manifest.json')
    for relative, expected in release['sha256'].items():
        verify(relative, expected)
    video = load('production/video-qa/report.json')
    verify('deliverables/ghcp-cxo-demo-ko.mp4', video['sha256'])
    require(video['durationSeconds'] == 300 and video['videoFrames'] == 9000, 'Video metadata mismatch')
    require(video['decodeErrors'] == 0, 'Recorded video decode errors')
    print(f'PASS: {len(shared["files"])} shareable copies, {len(tested["codeHashes"])} files in each app, '
          f'{len(baseline)} baseline files, {len(excerpts)} excerpts, {len(release["sha256"])} release assets.')
    print('Recorded results: 26 local tests; 22 baseline tests; 22 browser/API checks. No tests rerun.')
    print('Hashes check consistency, not independent authenticity. Private originals are not required or reconstructed.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        raise SystemExit(1)
