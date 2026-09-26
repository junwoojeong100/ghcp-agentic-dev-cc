#!/usr/bin/env python3
"""Run one bounded Copilot CLI stage and retain unedited local evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'demo/run'
EVIDENCE = ROOT / 'evidence/copilot-run'
EDITABLE = ['src/quote.mjs', 'src/server.mjs', 'public/index.html', 'public/app.js', 'public/style.css', 'test/margin.test.mjs']


def snapshot():
    return {str(p.relative_to(WORK)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(WORK.rglob('*')) if p.is_file() and 'node_modules' not in p.parts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['plan', 'implement', 'review', 'repair'])
    parser.add_argument('--prompt-file', required=True)
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    stage = args.stage
    prompt = Path(args.prompt_file).resolve().read_text()
    role = 'implementer' if stage == 'repair' else ('reviewer' if stage == 'review' else stage)
    if role == 'implement':
        role = 'implementer'
    commands = ['copilot', '-C', str(WORK), '--agent', f'cxo-quote-{role}',
                '--disable-builtin-mcps', '--no-auto-update', '--no-remote', '--no-remote-export',
                '--no-ask-user', '--no-color', '--stream', 'on', '--disallow-temp-dir',
                '--log-dir', str(EVIDENCE / f'{stage}-internal'),
                '--usage-output-file', str(EVIDENCE / f'{stage}-usage.json')]
    tools = ['view', 'glob', 'rg']
    if stage in ('implement', 'repair'):
        tools += ['apply_patch', 'bash', 'read_bash', 'list_bash', 'stop_bash']
        commands += [f'--allow-tool=write({WORK / path})' for path in EDITABLE]
        commands += ['--allow-tool=shell(node --test)']
    else:
        commands += ['--deny-tool=write', '--deny-tool=shell']
    commands += ['--available-tools', *tools, '-p', prompt]
    # No --allow-all-tools, --allow-all-paths, --yolo, remote exports, or MCP tools.
    started = datetime.now(timezone.utc).isoformat()
    before = snapshot()
    (EVIDENCE / f'{stage}-prompt.txt').write_text(prompt)
    (EVIDENCE / f'{stage}-before.json').write_text(json.dumps(before, indent=2))
    with (EVIDENCE / f'{stage}.log').open('w') as log:
        process = subprocess.Popen(commands, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, cwd=WORK)
        try:
            for line in process.stdout:
                log.write(line)
                log.flush()
                sys.stdout.write(line)
                sys.stdout.flush()
            code = process.wait()
        except BaseException:
            process.terminate()
            process.wait()
            raise
    after = snapshot()
    changed = [p for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)]
    unexpected = [p for p in changed if p not in EDITABLE] if stage in ('implement', 'repair') else changed
    record = {'stage': stage, 'startedAt': started, 'endedAt': datetime.now(timezone.utc).isoformat(),
              'exitCode': code, 'command': commands, 'changedFiles': changed,
              'unexpectedChanges': unexpected, 'before': before, 'after': after,
              'approvalScope': 'User-approved production plan; final result adoption is pending.'}
    (EVIDENCE / f'{stage}-record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(f'\nRECORDED: {stage} exit={code}, changed={changed}, unexpected={unexpected}', flush=True)
    if unexpected:
        raise SystemExit('Unexpected changes recorded; inspect before continuing.')
    raise SystemExit(code)


if __name__ == '__main__':
    main()
