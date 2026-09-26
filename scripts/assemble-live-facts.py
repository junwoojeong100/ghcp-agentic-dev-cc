#!/usr/bin/env python3
"""Assemble actual native results and verified media; never drive the filmed CLI."""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'production/ghcp-live'
TAKE = ROOT / 'evidence/ghcp-live/raw/headless.GuKDBv'
NATIVE = ROOT / 'evidence/ghcp-live/native-session'


def read(path):
    return json.loads(path.read_text())


def rel(path):
    return str(path.resolve().relative_to(ROOT))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def event(label):
    matches = list(NATIVE.glob(label + '-*.json'))
    if len(matches) != 1:
        raise ValueError(f'Expected one completed native record: {label}; found {len(matches)}')
    record = read(matches[0])
    if not record['completion']['data']['success']:
        raise ValueError(f'Native tool failed: {label}')
    return matches[0], record


def result(record):
    return record['completion']['data'].get('result', {}).get('content', '')


def tests(label, expected_exit):
    path, record = event(label)
    output = result(record)
    counts = {key: int(re.search(r'^# ' + key + r' (\d+)$', output, re.M)[1])
              for key in ['tests', 'pass', 'fail', 'skipped']}
    if f'completed with exit code {expected_exit}>' not in output:
        raise ValueError(f'Unexpected test exit: {label}')
    return {'passed': counts['pass'], 'failed': counts['fail'], 'skipped': counts['skipped'],
            'total': counts['tests'], 'exitCode': expected_exit,
            'executor': '촬영 중 Copilot CLI', 'evidence': [rel(path)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-map', required=True, help='Reviewed mapping of real PNGs to deck roles')
    parser.add_argument('--review-snapshot', required=True)
    parser.add_argument('--approval-in', type=float)
    parser.add_argument('--approval-out', type=float)
    parser.add_argument('--draft', action='store_true', help='Explicit incomplete-capture review draft; no terminal video')
    parser.add_argument('--capture-failure', help='Repository-relative original capture failure diagnosis JSON')
    args = parser.parse_args()
    raw = failure_path = None
    if args.draft:
        if not args.capture_failure or Path(args.capture_failure).is_absolute():
            parser.error('--draft requires repository-relative --capture-failure')
        failure_path = (ROOT / args.capture_failure).resolve()
        failure_path.relative_to(ROOT)
        failure = read(failure_path)
        if (failure.get('kind') != 'recorder-finalization-failure' or failure.get('status') != 'incomplete'
                or failure.get('captureComplete') is not False or failure.get('fixture') is not False
                or failure.get('approvalVideoRecovered') is not False or not failure.get('takeId')):
            raise ValueError('Draft requires explicit nonfixture incomplete capture evidence')
        if args.approval_in is not None or args.approval_out is not None:
            parser.error('--draft cannot claim a protected approval video interval')
    else:
        if args.capture_failure:
            parser.error('--capture-failure is only valid with --draft')
        if args.approval_in is None or args.approval_out is None:
            parser.error('Final mode requires --approval-in and --approval-out')
        capture_manifest = read(TAKE / 'capture.json')
        assert capture_manifest['status'] == 'recorded' and capture_manifest['finalized']
        assert capture_manifest['childExit']['exitCode'] == 0
        assert capture_manifest['seq'] == capture_manifest['renderedSeq'] == capture_manifest['ack']
        raw = Path(capture_manifest['rawVideo'])
        assert digest(raw) == capture_manifest['originalVideoSha256']
    implementation_path = ROOT / 'evidence/ghcp-live/producer-verification/implementation-record.json'
    implementation = read(implementation_path)
    for name, expected in implementation['codeHashes'].items():
        assert digest(ROOT / implementation['snapshot'] / name) == expected, name
        assert digest(Path(implementation['sourceWorkspace']) / name) == expected, name
    red, green = tests('red-tests', 1), tests('green-tests', 0)
    assert red['failed'] == 2 and green['failed'] == green['skipped'] == 0
    analysis = [event('cxo-server-analysis'), event('cxo-ui-analysis')]
    for path, record in analysis:
        kinds = {r['type'] for r in record['subagentEvents']}
        assert {'subagent.started', 'subagent.completed'} <= kinds
    intervals = [(next(r['timestamp'] for r in rec['subagentEvents'] if r['type'] == 'subagent.started'),
                  next(r['timestamp'] for r in rec['subagentEvents'] if r['type'] == 'subagent.completed'))
                 for _, rec in analysis]
    assert max(a for a, _ in intervals) < min(b for _, b in intervals)
    review_path, review = event('cxo-review')
    assert any(r['type'] == 'subagent.completed' for r in review['subagentEvents'])
    browsers = []
    failed_browser = []
    for path in sorted(NATIVE.glob('browser_run_code_unsafe-*.json')):
        record = read(path)
        if not record['completion']['data']['success']:
            failed_browser.append(rel(path))
            continue
        output = result(record)
        body = json.loads(output.split('### Result\n', 1)[1].split('\n### ', 1)[0])
        browsers.append((path, body))
    functional = next(body for _, body in browsers if 'functional' in body)
    asynchronous = next(body for _, body in browsers if 'delayedResponses' in body)
    assert functional['pageErrors'] == [] and functional['keyboardEnterSend'] == 'passed'
    assert all(row['overflow'] == row['clippedText'] == 0 for row in functional['layout'])
    assert all(row['result'] == 'passed' for row in asynchronous['delayedResponses'])
    assert all(row['blocked'] for row in asynchronous['invalidInputs'])
    assert asynchronous['validInputRecovery'] == 'passed'
    browser_count = (len(functional['functional']) + len(functional['layout']) + 1 +
                     len(asynchronous['delayedResponses']) + len(asynchronous['invalidInputs']) + 1)
    mapping = read(ROOT / args.capture_map)
    captures = mapping['captures']
    review_snapshot_path = TAKE / 'snapshots' / (args.review_snapshot + '.json')
    review_snapshot = read(review_snapshot_path)
    captures['review'] = rel(Path(review_snapshot['screenshot']))
    sources = []
    for source, meta in mapping['sources'].items():
        path = ROOT / source
        sources.append({**meta, 'source': source, 'sha256': digest(path)})
    for source in set(captures.values()) | ({rel(raw)} if raw else set()):
        if any(item['source'] == source for item in sources):
            continue
        path = ROOT / source
        snapshot = path.with_suffix('.json') if path.suffix == '.png' else None
        entry = {'source': source, 'kind': 'terminal', 'sha256': digest(path), 'evidence': []}
        if snapshot:
            assert snapshot.is_file()
            entry['snapshotManifest'] = rel(snapshot)
            entry['evidence'].append(rel(snapshot))
        sources.append(entry)
    for entry in sources:
        if entry['kind'] != 'terminal':
            continue
        if args.draft:
            if Path(entry['source']).suffix.lower() != '.png':
                raise ValueError('Draft excludes all terminal video sources')
            entry.pop('captureManifest', None)
            entry['captureFailureManifest'] = rel(failure_path)
            if rel(failure_path) not in entry['evidence']:
                entry['evidence'].append(rel(failure_path))
            snapshot = read(ROOT / entry['snapshotManifest'])
            assert rel(Path(snapshot['screenshot'])) == entry['source']
            assert snapshot['takeId'] == failure['takeId']
            assert type(snapshot.get('seq')) is int and snapshot['seq'] == snapshot.get('renderedSeq')
            assert not snapshot.get('error') and snapshot.get('snapshotId')
        else:
            entry['captureManifest'] = rel(TAKE / 'capture.json')
            if rel(TAKE / 'capture.json') not in entry['evidence']:
                entry['evidence'].append(rel(TAKE / 'capture.json'))
    approval_path = TAKE / 'implementation-approval.json'
    approval = read(approval_path)
    assert approval['status'] == 'approved'
    finding_summary = ('HTTP 422 본문 JSON 읽기·타임아웃 실패 시 상태 코드가 유실되어 이전 허용 preview가 남고 '
                       '전송 버튼이 다시 활성화될 수 있습니다. 새 허용 preview 전 잠금 유지 요건 미충족입니다.')
    limits = [
        '보완 필요 1건 · 운영 적용 보류. 422 본문 읽기 실패 때 이전 허용 상태가 남는 코드상 결함이며 실행 재현은 미검증입니다. 서버 차단 우회는 아닙니다.',
        *(['촬영 불완전: 승인 연속 영상 미확보. 검토용 초안은 실제 터미널 PNG와 별도 앱 영상만 사용하며 잘린 터미널 영상은 제외합니다.'] if args.draft else []),
        '실제 CLI를 브라우저 터미널로 중계했습니다. 사용자 선택은 대화에서 확인 후 전달했으며 직접 CLI 키 입력이 아닙니다.',
        '합성 견적·모의 전송 사례입니다. 운영 채택·외부 발송·배포는 승인되지 않았습니다.',
        ('실제 캡처 정지 화면과 별도 앱 영상을 편집한 검토용 초안입니다. 승인 요청–응답–후속 실행의 연속 영상은 없습니다.' if args.draft else
         '영상은 원본 일부를 편집했습니다. 승인 예시는 CSS 변경 요청 전체를 연속·정속으로 보존하며 다른 승인 대기 구간은 생략했습니다.'),
        '남은 로컬 검증은 사용자 일괄 승인 범위에서 대행했습니다. 각각의 도구 호출을 사용자가 직접 승인한 것으로 표현하지 않습니다.',
        '첫 Playwright 검사는 URL 전역 객체 오류로 실패했습니다. 수정된 검증 호출은 통과했고 실패 기록도 보존했습니다.',
        '브라우저 콘솔의 HTTP 400 한 건은 순매출 0원 입력을 검사하며 발생한 예상 응답입니다. 콘솔 오류가 전혀 없었다고 주장하지 않습니다.',
        '앱 결과 화면은 촬영 밖 제작 세션에서 같은 코드로 별도 녹화했습니다. Copilot 자체 화면 녹화와 구분됩니다.',
        '한국어 내레이션은 합성 음성입니다. 사람의 청취 검수와 PowerPoint에서 실제 링크 재생은 미검증입니다.',
        '시간·비용·생산성 효과는 측정하지 않았습니다. 영상 길이는 개발 소요시간이 아닙니다.',
    ]
    facts = {
        'schemaVersion': 1,
        'cli': {'name': 'GitHub Copilot CLI', 'version': '1.0.88', 'model': 'GPT-6 Astra (CLI 표시)'},
        'requirementsSatisfied': False,
        'finalAdoption': 'not-approved',
        'openFindings': [{'status': 'unresolved', 'summary': finding_summary,
                          'verification': 'static-code-review', 'executedReproduction': False,
                          'serverBlockingBypassed': False, 'evidence': [rel(review_path)]}],
        'production': ({'status': 'review-draft', 'captureComplete': False,
                        'evidence': [rel(failure_path)]} if args.draft else
                       {'status': 'capture-complete', 'captureComplete': True,
                        'evidence': [rel(TAKE / 'capture.json')]}),
        'implementation': {'status': 'verified-with-open-finding', 'changedFiles': implementation['changedFiles'],
                           'codeHash': implementation['codeHash'], 'evidence': [rel(implementation_path), rel(review_path)]},
        'baselineTests': {'passed': 22, 'failed': 0, 'executor': '촬영 밖 제작 세션',
                          'evidence': ['evidence/ghcp-live/baseline-test-record.json']},
        'redTests': red, 'tests': green,
        'browserChecks': {'passed': browser_count, 'failed': 0, 'executor': '촬영 중 Copilot의 Chromium 검사',
                          'counting': '5 업무 사례 + 18 레이아웃 조합 + 키보드 1 + 지연 3 + 잘못된 입력 3 + 복구 1',
                          'earlierToolFailures': failed_browser, 'evidence': [rel(p) for p, _ in browsers]},
        'delegation': {'status': 'completed', 'summary': '서버/API와 UI/검증 조사를 두 전용 에이전트에 병렬 위임하고 결과를 통합했습니다.',
                       'intervals': intervals, 'evidence': [rel(p) for p, _ in analysis]},
        'review': {'status': 'completed', 'summary': result(review), 'evidence': [rel(review_path)]},
        'approvals': [
            {'kind': 'implementation-plan', 'status': 'approved', 'evidence': [rel(approval_path)]},
            *([{'kind': 'individual-css-update', 'status': 'approved', 'source': rel(raw),
                'in': args.approval_in, 'out': args.approval_out,
                'evidence': [rel(TAKE / 'snapshots/087b5102-5440-40aa-9643-3d631f97219c.json'), rel(TAKE / 'capture-events.jsonl')]}] if raw else []),
        ],
        'captures': captures, 'sources': sources,
        'video': {'path': 'deliverables/ghcp-live/ghcp-cxo-demo-ko.mp4'}, 'limitations': limits,
    }
    output = RUN / 'facts.json'
    if output.exists():
        raise ValueError('facts.json already exists; inspect it before editing')
    output.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + '\n')
    print(f'FACTS READY: {rel(output)}; native tests {green["passed"]}, browser scenarios {browser_count}')


if __name__ == '__main__':
    main()
