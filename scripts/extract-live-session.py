#!/usr/bin/env python3
"""Preserve selected native execution records, without replaying the CLI."""
import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
SESSION = Path.home() / '.copilot/session-state/cbf6161c-a575-4320-8b36-2b997436c216/events.jsonl'
OUT = ROOT / 'evidence/ghcp-live/native-session'


def save(path, value):
    content = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if path.exists():
        if path.read_text() != content:
            raise ValueError(f'Refusing to replace an existing execution record: {path}')
    else:
        path.write_text(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-id', type=UUID, default=UUID(SESSION.parent.name))
    parser.add_argument('--output-dir', type=Path, default=OUT)
    options = parser.parse_args()
    session = SESSION.parent.parent / str(options.session_id) / 'events.jsonl'
    output = options.output_dir.resolve()
    output.relative_to(ROOT / 'evidence/ghcp-live')
    if session != SESSION and output == OUT:
        parser.error('A new session requires its own --output-dir; old evidence is preserved')
    raw = session.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    starts = {r['data']['toolCallId']: r for r in rows if r['type'] == 'tool.execution_start'}
    output.mkdir(parents=True, exist_ok=True)
    saved = []
    for result in rows:
        if result['type'] != 'tool.execution_complete':
            continue
        call_id = result['data']['toolCallId']
        start = starts.get(call_id)
        if not start:
            continue
        name = start['data']['toolName']
        args = start['data'].get('arguments', {})
        label = None
        if name == 'task' and args.get('agent_type', '').startswith('cxo-'):
            label = args['agent_type']
        elif name == 'bash' and args.get('command') == 'node --test test/margin.test.mjs':
            label = 'red-tests'
        elif name == 'bash' and args.get('command') == 'npm test':
            label = 'green-tests'
        elif name == 'bash' and ('node --test' in args.get('command', '') or 'npm test' in args.get('command', '')):
            label = 'test-run'
        elif name.startswith('playwright-headless-'):
            label = name.removeprefix('playwright-headless-')
        if label is None:
            continue
        path = output / f'{label}-{result["id"]}.json'
        related = [r for r in rows if r['type'].startswith('subagent.') and r['data'].get('toolCallId') == call_id]
        record = {'schemaVersion': 1, 'sourceSessionId': session.parent.name,
                  'sourceFile': str(session), 'executor': 'Filmed native GitHub Copilot CLI',
                  'start': start, 'completion': result, 'subagentEvents': related,
                  'note': 'Exact native event objects, extracted after execution; not a producer re-run.'}
        save(path, record)
        saved.append({'label': label, 'path': str(path.relative_to(ROOT)),
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'success': result['data'].get('success'),
                      'completedAt': result['timestamp']})
    index = {'schemaVersion': 1, 'sourceSessionId': session.parent.name,
             'sourceSha256AtExtraction': hashlib.sha256(raw).hexdigest(), 'records': saved,
             'note': 'Index of selected execution evidence; no model internals or credentials copied.'}
    # The index may advance while the same session completes further tools.
    (output / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n')
    for r in saved:
        print(r['label'], r['success'], r['path'])


if __name__ == '__main__':
    main()
