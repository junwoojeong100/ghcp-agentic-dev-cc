#!/usr/bin/env python3
"""Prepare final media from the completed real retake; never launch the CLI."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'production/ghcp-live/retake-20260926'
EV = ROOT / 'evidence/ghcp-live/retake-20260926'
TAKE = EV / 'raw/headless.1FbXjt'
APP = EV / 'app/2026-09-26T13-36-18-252Z-3613f866'
NATIVE = EV / 'native-session'
WORK = Path('/Users/junwoojeong/ghcp-headless-demo-H976Zg')
OUT = ROOT / 'deliverables/ghcp-live/final'


def read(p):
    return json.loads(p.read_text())


def rel(p):
    return p.resolve().relative_to(ROOT).as_posix()


def digest(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def save(p, value):
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if p.exists():
        if p.read_text() != text:
            raise ValueError(f'Refusing to replace existing record: {p}')
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('x') as f:
            f.write(text)


def native(name):
    matches = list(NATIVE.glob(name + '-*.json'))
    assert len(matches) == 1, name
    value = read(matches[0])
    assert value['completion']['data']['success'] is True
    return matches[0], value


def counts(path, exit_code):
    obj = read(path)
    text = obj['completion']['data']['result']['content']
    assert f'completed with exit code {exit_code}>' in text
    result = {k: int(re.search(r'^# ' + v + r' (\d+)$', text, re.M)[1])
              for k, v in [('passed', 'pass'), ('failed', 'fail'), ('skipped', 'skipped'), ('total', 'tests')]}
    return {**result, 'exitCode': exit_code, 'executor': '촬영 중 Copilot CLI', 'evidence': [rel(path)]}


def main():
    manifest = read(TAKE / 'capture.json')
    assert manifest['status'] == 'recorded' and manifest['finalized'] and manifest['fixture'] is False
    assert manifest['childExit']['exitCode'] == 0 and not manifest['childExit']['stopped']
    assert manifest['seq'] == manifest['ack'] == manifest['renderedSeq'] and manifest['rendererEnded']
    assert manifest['videoCopyVerified'] and not manifest['failure'] and not manifest['evidenceError']
    raw = Path(manifest['rawVideo'])
    assert digest(raw) == manifest['originalVideoSha256'] == digest(Path(manifest['localVideo']))
    app = read(APP / 'manifest.json')
    assert app['executionStatus'] == 'passed' and app['summary']['failed'] == 0 and not app['errors']
    for asset in app['files']:
        assert digest(APP / asset['filename']) == asset['sha256']
    assert app['codeBefore'] == app['codeAfter']
    base = read(TAKE / 'workspace-baseline.json')
    names = list(base['codeHashes']) + ['test/margin.test.mjs']
    hashes = {n: digest(WORK / n) for n in names}
    for file in app['codeAfter']['final']['files']:
        assert file['sha256'] == hashes[file['path']]
    for n in ['package.json', 'test/quote.test.mjs', 'test/server.test.mjs', 'public/index.html', 'public/style.css']:
        assert hashes[n] == base['codeHashes'][n], n
    snapshot = ROOT / 'demo/ghcp-live/final'
    for n in names:
        dest = snapshot / n
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            assert digest(dest) == hashes[n]
        else:
            with dest.open('xb') as f:
                f.write((WORK / n).read_bytes())
    changed = [n for n in names if hashes[n] != base['codeHashes'].get(n)]
    assert set(changed) == {'src/quote.mjs', 'src/server.mjs', 'public/app.js', 'test/margin.test.mjs'}
    code_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    impl = EV / 'implementation-record.json'
    save(impl, {'status': 'verified', 'sourceWorkspace': str(WORK), 'snapshot': rel(snapshot),
                'codeHashes': hashes, 'codeHash': code_hash, 'protectedFilesUnchanged': True,
                'changedFiles': [rel(snapshot / n) for n in changed],
                'note': 'Producer hash comparison after the filmed run, not independent review or adoption.'})
    red = counts(NATIVE / 'test-run-40e8f954-e235-4462-afde-d156e5aacfe8.json', 1)
    green = counts(NATIVE / 'test-run-a5e9a708-338d-4237-8ac3-8bfbc30274b2.json', 0)
    assert red['failed'] == 17 and green['passed'] == 55 and green['failed'] == green['skipped'] == 0
    analysis = [native('cxo-server-analysis'), native('cxo-ui-analysis')]
    intervals = [(next(e['timestamp'] for e in v['subagentEvents'] if e['type'] == 'subagent.started'),
                  next(e['timestamp'] for e in v['subagentEvents'] if e['type'] == 'subagent.completed')) for _, v in analysis]
    assert max(x[0] for x in intervals) < min(x[1] for x in intervals)
    review_path, review = native('cxo-review')
    summary = review['completion']['data']['result']['content']
    assert '확정 결함 **0건**' in summary
    browser_path = NATIVE / 'browser_run_code_unsafe-a1f25958-5174-4399-b66e-9b13b0730657.json'
    browser = read(browser_path)['completion']['data']['result']['content']
    result = json.loads(browser.split('### Result\n', 1)[1].split('\n### ', 1)[0])
    assert result['passed'] and result['frameCount'] == 36 and result['failures'] == result['pageErrors'] == []
    body_path = EV / 'body-verification/report.json'
    body = read(body_path)
    assert body['status'] == 'passed' and body['sourceHashesUnchanged']
    assert all(c['status'] == 'passed' for c in body['checks'])
    for name, sha in body['sourceHashesAfter'].items():
        assert hashes[name] == sha, name
    approval_path = TAKE / 'implementation-approval.json'
    assert read(approval_path)['relayStatus'] == 'sent'
    def snap(id): return rel(TAKE / 'snapshots' / (id + '.png'))
    def image(name): return rel(APP / name)
    captures = {'before': image('02-baseline-q1001-10-sent.png'),
                'analysis': snap('2d880a3b-35b4-4136-b7da-9c9c1030fa45'),
                'approval': snap('07fd04ec-ec6f-4745-9dfc-1496fd1d6700'),
                'implementation': snap('669c4be8-ed59-4408-a8ab-cebf51e1338b'),
                'tests': snap('346746fe-0cf2-45b1-91dd-5baa76aa6fd5'),
                'blocked': image('03-final-q1001-10-blocked.png'),
                'allowed': image('05-final-q1001-0-sent.png'),
                'boundary': image('05-final-q1499-rounded15-blocked.png'),
                'review': snap('169f561f-aff2-416f-bec8-bd3b2c67d9dc')}
    sources = [{'source': rel(APP / f['filename']), 'kind': 'app', 'sha256': f['sha256'],
                'evidence': [rel(APP / 'manifest.json')]} for f in app['files']]
    for name in sorted(set(captures.values()) | {rel(raw)}):
        if any(s['source'] == name for s in sources): continue
        p = ROOT / name
        source = {'source': name, 'kind': 'terminal', 'sha256': digest(p),
                  'captureManifest': rel(TAKE / 'capture.json'), 'evidence': [rel(TAKE / 'capture.json')]}
        if p.suffix == '.png':
            source['snapshotManifest'] = rel(p.with_suffix('.json'))
            source['evidence'].append(source['snapshotManifest'])
        sources.append(source)
    limitations = [
        '사용자는 대화에서 계획과 로컬 실행을 승인했고 제작자가 CLI에 전달했습니다. 이후 도구 요청은 일괄 승인 범위에서 대행했습니다.',
        '합성 견적·모의 전송입니다. 운영 채택·외부 발송·배포는 수행하지 않았습니다.',
        '5분 본편의 권한 장면은 사전 승인 범위 내 서버 수정 요청과 대행을 연속·정속으로 보존합니다. 계획 승인 전체 구간은 별도 보충 영상으로 제공합니다.',
        '실제 CLI 녹화, 표시된 정지 캡처, 별도 제작 세션의 앱 녹화와 설명 카드를 구분했습니다. 원본 구간 외 대기는 생략했습니다.',
        '검토 역할은 TAP 로그 3종을 읽지 못했습니다. 테스트 실행 수치는 동일 세션의 원시 도구 반환에서 별도로 확인했습니다. 검토자의 독립 재확인으로 표현하지 않습니다.',
        '촬영 중 본문 오류 검사는 node:vm 모의 회귀입니다. 촬영 종료 후 별도 제작 세션에서 실제 Chromium·로컬 프록시로 422 JSON 오류·본문 단절·타임아웃을 주입해 잠금과 복구를 확인했습니다. 실제 운영 장애나 촬영 중 검증으로 표현하지 않습니다.',
        '최초 브라우저 스크립트의 동적 import와 로그 저장 경로가 실패했으며 수정된 호출로 검증·로그 보존을 완료했습니다. 실패 기록을 남겼습니다.',
        'Firefox/WebKit, 실제 모바일 기기, 스크린리더, 브라우저 확대 및 모든 운영 조건은 미검증입니다.',
        '한국어 합성 내레이션입니다. 사람 청취와 PowerPoint 앱의 실제 링크 재생은 별도 확인 사항입니다. 시간·비용·생산성 효과는 미측정입니다.',
        '원본 터미널에 로컬 사용자 경로가 포함돼 있습니다. 자료는 로컬 전달용이며 외부 게시하지 않았습니다.',
    ]
    facts = {'schemaVersion': 1, 'cli': {'name': 'GitHub Copilot CLI', 'version': '1.0.88', 'model': 'GPT-6 Astra (CLI 표시)'},
             'requirementsSatisfied': True, 'requirementsScope': 'Recorded synthetic-demo conditions only; not operational acceptance',
             'finalAdoption': 'not-approved', 'openFindings': [],
             'production': {'status': 'capture-complete', 'captureComplete': True,
                            'captureManifest': [{'path': rel(TAKE / 'capture.json'), 'sha256': digest(TAKE / 'capture.json')}],
                            'evidence': [rel(TAKE / 'capture.json')]},
             'implementation': {'status': 'verified', 'changedFiles': [rel(snapshot / n) for n in changed],
                                'codeHash': code_hash, 'evidence': [rel(impl), rel(review_path)]},
             'baselineTests': {'passed': 22, 'failed': 0, 'executor': '촬영 밖 준비 세션', 'evidence': ['evidence/ghcp-live/baseline-test-record.json']},
             'redTests': red, 'tests': green,
             'browserChecks': {'passed': 36, 'failed': 0, 'executor': '촬영 중 Copilot의 Chromium 검사', 'counting': '6 viewport widths × 6 states; not 36 separate test files', 'evidence': [rel(browser_path)]},
             'bodyVerification': {'status': 'passed', 'executor': '촬영 종료 후 별도 제작 세션의 Chromium·로컬 HTTP 검사',
                                  'scenarios': ['422 JSON 오류', '422 본문 단절', '422 본문 타임아웃', '정상 모의 전송', '실제 서버 직접 요청 차단'],
                                  'faultsInjected': True, 'evidence': [rel(body_path)]},
             'delegation': {'status': 'completed', 'summary': '서버/API와 UI/검증의 읽기 전용 조사를 병렬 위임하고 결과를 통합했습니다.', 'intervals': intervals, 'evidence': [rel(p) for p, _ in analysis]},
             'review': {'status': 'completed', 'summary': summary, 'evidence': [rel(review_path)]},
             'approvals': [{'kind': 'implementation-plan', 'status': 'approved', 'evidence': [rel(approval_path)]},
                           {'kind': 'scoped-server-edit-relay', 'status': 'approved', 'source': rel(raw), 'in': 3148, 'out': 3248,
                            'evidence': [rel(approval_path), rel(TAKE / 'capture-events.jsonl')]}],
             'captures': captures, 'sources': sources, 'limitations': limitations,
             'video': {'path': rel(OUT / 'ghcp-cxo-demo-ko.mp4')}}
    scenes = []
    def card(id, title, narration, duration):
        scenes.append(dict(id=id, title=title, narration=narration, duration=duration, kind='card', approval=False))
    def still(id, title, narration, duration, source, kind='app'):
        scenes.append(dict(id=id, title=title, narration=narration, duration=duration, source=source, kind=kind, approval=False))
    def clip(id, title, narration, start, end, source, kind='app', approval=False, label='별도 제작 세션의 실제 앱'):
        scenes.append(dict(id=id, title=title, narration=narration, source=source, kind=kind, approval=approval, speed=1, editLabel=label, **{'in': start, 'out': end}))
    card('opening', '사업 요청을 작동하는 변화로', '기존 견적 업무에 이익률 정책을 적용합니다. 실제 코파일럿 실행과 검증 결과를 살펴보겠습니다.', 10)
    clip('before-motion', '변경 전 · 기준 미달도 모의 전송', '변경 전에는 기준 미달 견적도 전송됩니다.', 0, 6.6, image('01-baseline-q1001-10-send.webm'), label='별도 제작 세션의 실제 앱')
    still('before-detail', '이익은 남지만 · 정책 기준에는 미달', '할인 십 퍼센트, 순매출 구백만 원, 이익 백만 원입니다. 적자는 아니지만 최소 이익률 십오 퍼센트에는 미달합니다.', 13.4, captures['before'])
    still('analysis', '서버와 UI 조사 · 실제 병렬 위임', '서버와 화면 조사를 두 역할에 나눴습니다. 기존 코드의 재사용 지점과 검증 방법을 모아 하나의 계획으로 통합했습니다.', 14, captures['analysis'], 'terminal')
    still('plan', '계획과 로컬 실행 범위를 사람이 승인', '사용자가 대화에서 계획과 로컬 실행을 승인했습니다. 다음 도구 요청은 그 범위 안에서 제작자가 대행합니다.', 12, captures['approval'], 'terminal')
    clip('permission', '서버 수정 요청 → 범위 확인 → 대행', '지금은 실제 서버 수정 권한 요청입니다. 사용자는 앞서 구현 계획과 범위 안의 로컬 실행을 함께 승인했습니다. 제작자는 요청 내용을 확인하고 이번 실행에만 허용을 전달합니다. 각 요청을 사용자가 직접 승인한 장면은 아닙니다. 이 구간은 대기 시간을 포함해 정속으로 보존했습니다. 서버는 화면에서 받은 금액을 믿지 않고 등록된 원가와 견적으로 다시 계산합니다. 반올림해 표시한 비율이 아니라 실제 원 단위 금액으로 정책을 판정합니다. 기준 미달은 전송 단계에서 차단하고, 정상 업무 흐름은 유지합니다. 구현 승인과 운영 출시 승인은 별개입니다.', 3148, 3248, rel(raw), 'terminal', True, '사전 일괄 승인 범위에서 제작자 대행')
    card('red', '변경 전 · 회귀 17건 실패', '구현 전 새 회귀 검사에서 열일곱 건이 실패했습니다. 직접 전송 차단과 화면 잠금이 아직 없다는 것을 실행으로 확인했습니다.', 12)
    still('implementation', '422 본문을 읽기 전에 허용 폐기', '차단 응답의 본문을 읽지 못해도 새 허용 판정 전까지 전송 잠금을 유지하도록 구현했습니다.', 10, captures['implementation'], 'terminal')
    still('green', '변경 후 · 전체 55건 통과', '같은 실행에서 기존 스물두 건과 새 회귀 서른세 건, 모두 쉰다섯 건이 통과했습니다. 건너뛴 테스트는 없습니다.', 16, captures['tests'], 'terminal')
    clip('blocked-motion', '같은 견적 · 이제는 전송 차단', '같은 입력의 전송이 차단됩니다.', 0, 5.2, image('02-final-q1001-10-blocked.webm'), label='별도 제작 세션의 실제 앱')
    still('blocked-detail', '화면에는 이유 · API에는 HTTP 422', '화면에 이유를 표시하고 버튼을 잠급니다. 화면을 거치지 않은 직접 요청도 서버에서 차단하는 것을 확인했습니다.', 12.8, captures['blocked'])
    clip('allowed-motion', '정상 견적의 모의 전송은 유지', '할인을 없애면 이익률 이십 퍼센트로 정상 전송됩니다.', 0, 7.4, image('03-final-q1001-0-allowed-sent.webm'), label='별도 제작 세션의 실제 앱')
    still('allowed-detail', '정책 적용과 정상 흐름을 함께 확인', '차단 기능만 확인하지 않았습니다. 정상 견적의 성공 안내와 입력 변경 후 상태 갱신도 검증했습니다. 외부 발송은 없습니다.', 12.6, captures['allowed'])
    clip('exact', '정확히 15% · 허용', '', 0, 4.2, image('04-final-q1500-exact15-allowed.webm'), label='별도 제작 세션의 실제 앱')
    clip('rounded', '표시가 15.0%여도 · 미달이면 차단', '', 0, 4.2, image('05-final-q1499-rounded15-blocked.webm'), label='별도 제작 세션의 실제 앱')
    still('boundary', '원가 1원 차이도 실제 금액으로 판정', '원가가 경계보다 일 원 높으면 표시가 같아도 기준 미달입니다. 정확한 경계는 허용하고 실제 미달은 차단합니다.', 11.6, captures['boundary'])
    still('async', '빠른 입력 · 늦은 응답 · 작은 화면', '늦게 도착한 이전 허용 응답이 최신 차단 상태를 덮지 않는지 확인했습니다. 작은 화면의 업무 흐름도 검사했습니다.', 12, image('12-final-stale-preview-ignored.png'))
    clip('review', '읽기 전용 검토와 한계를 함께 기록', '검토 범위에서 확정 결함은 없었습니다. 다만 검토자가 읽지 못한 테스트 로그와 독립 재확인 범위는 그대로 밝혔습니다.', 6320, 6335, rel(raw), 'terminal', False, '검토 한계 포함 · 원본 구간 발췌')
    card('pilot', '업무 하나로 시작하고\n효과는 측정합니다', '업무 책임자는 정책과 범위를 정하고, 개발자는 변경을 검증하며, 검토 책임자가 채택을 판단합니다. 파일럿에서는 리드타임과 검토 시간, 재작업을 비교하세요. 이번 영상은 개발 시간이나 생산성 효과를 측정한 자료가 아닙니다.', 21)
    assert sum(s.get('duration', s.get('out', 0) - s.get('in', 0)) for s in scenes) == 300
    save(RUN / 'facts.json', facts)
    save(RUN / 'edit-list.json', {'title': '사업 요청에서 검증까지', 'scenes': scenes})
    print('Prepared final facts and 19 scenes / 300 seconds. Four changed files; native full suite 55 passed.')


if __name__ == '__main__':
    main()
