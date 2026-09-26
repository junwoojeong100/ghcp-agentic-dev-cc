#!/usr/bin/env python3
"""Create explicitly redacted copies without changing frozen execution evidence."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'evidence/shareable'
SOURCES = {
    **{f'copilot-run/{stage}.log': f'evidence/copilot-run/{stage}.log'
       for stage in ('plan', 'implement', 'review')},
    **{f'copilot-run/{stage}-record.json': f'evidence/copilot-run/{stage}-record.json'
       for stage in ('plan', 'implement', 'review')},
    'copilot-run/local-test-record.json': 'evidence/copilot-run/local-test-record.json',
    'qa/deck-report.json': 'production/deck-qa/qa-report.json',
    'qa/video-metadata.json': 'production/video-qa/metadata.json',
}
UUID = re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I)
HOME = re.compile(r'/(?:Users|home)/[^/\s"\']+')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    rows = []
    for relative, source in SOURCES.items():
        original = (ROOT / source).read_bytes()
        text = original.decode('utf-8')
        text = text.replace(str(ROOT), '<PROJECT_ROOT>')
        text = HOME.sub('<HOME>', text)
        text = UUID.sub('<REDACTED_SESSION_ID>', text)
        if relative.endswith('.json'):
            json.loads(text)
        output = text.encode('utf-8')
        path = DEST / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != output:
            raise SystemExit(f'Refuse to overwrite a changed published copy: {path.relative_to(ROOT)}')
        path.write_bytes(output)
        rows.append({'source': source, 'publishedCopy': path.relative_to(ROOT).as_posix(),
                     'originalSha256': sha(original), 'publishedSha256': sha(output),
                     'redactionsApplied': original != output})
    manifest = {'purpose': 'Shareable redacted copies, not original execution transcripts.',
                'redactions': ['Project absolute path -> <PROJECT_ROOT>',
                               'Home directory prefix -> <HOME>',
                               'Session UUID -> <REDACTED_SESSION_ID>'],
                'hashNote': 'Original hashes remain unchanged in historical records and excerpt-manifest.json. '
                            'They identify local originals, not these redacted copies. '
                            'Nested path/content hashes in copied records are also historical, not recomputed.',
                'files': rows}
    dest = DEST / 'manifest.json'
    raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    if dest.exists() and dest.read_bytes() != raw:
        raise SystemExit('Refuse to overwrite a changed shareable manifest.')
    dest.write_bytes(raw)
    print(f'Created {len(rows)} shareable copies; all original evidence unchanged.')


if __name__ == '__main__':
    main()
