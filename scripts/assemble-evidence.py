#!/usr/bin/env python3
"""Validate retained execution evidence and prepare factual presentation inputs."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import textwrap

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'evidence/copilot-run'
P = ROOT / 'production'
APP = ROOT / 'demo/run'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name):
    return json.loads((E / name).read_text())


def counts(name):
    value = (E / name).read_text()
    result = {key: int(re.search(rf'^# {key} (\d+)$', value, re.M).group(1))
              for key in ('tests', 'pass', 'fail', 'skipped', 'cancelled')}
    assert result['tests'] == result['pass'] and result['fail'] == result['skipped'] == result['cancelled'] == 0
    return result


def main():
    records = {stage: load(f'{stage}-record.json') for stage in ('plan', 'implement', 'review')}
    for stage, record in records.items():
        assert record['exitCode'] == 0 and not record['unexpectedChanges'], stage
    assert not records['plan']['changedFiles'] and not records['review']['changedFiles']
    expected = {'public/app.js', 'src/quote.mjs', 'src/server.mjs', 'test/margin.test.mjs'}
    assert set(records['implement']['changedFiles']) == expected
    for name, sha in records['implement']['after'].items():
        assert digest(APP / name) == sha, f'Copilot output changed: {name}'
    test_record = load('local-test-record.json')
    assert test_record['exitCode'] == 0
    for name, sha in test_record['codeHashes'].items():
        assert digest(APP / name) == sha, f'Tested code changed: {name}'
    baseline, tests = counts('baseline-tests.log'), counts('local-tests.log')
    browser = load('browser-checks.json')
    assert browser['passed'] == len(browser['checks']) and not browser['pageErrors']
    assert all(c['passed'] for c in browser['checks'])
    api = load('api-responses.json')
    blocked = api[1]
    assert blocked['status'] == 422 and blocked['response']['error']['code'] == 'MARGIN_BELOW_MINIMUM'
    assert api[0]['status'] == api[2]['status'] == api[3]['status'] == api[5]['status'] == 200
    assert api[4]['status'] == 422
    assert all(item['status'] == 400 for item in api[6:])
    logs = {name: (E / f'{name}.log').read_text() for name in ('plan', 'implement', 'review')}
    assert 'Permission denied and could not request permission from user' in logs['implement']
    assert '구체적인 구현 결함은 발견하지 못했습니다.' in logs['review']
    for name in ('before', 'blocked', 'allowed', 'boundary'):
        assert (P / 'screenshots' / f'{name}.png').is_file()
        assert (P / 'clips' / f'{name}.mp4').is_file()

    facts = {
        'copilotImplementation': True,
        'copilotReview': True,
        'copilotTestExecution': 'blocked',
        'testExecutor': '별도 로컬 실행 (사용자 승인)',
        'tests': {'passed': tests['pass'], 'failed': tests['fail'], 'skipped': tests['skipped']},
        'baselineTests': {'passed': baseline['pass'], 'failed': baseline['fail']},
        'browserChecks': {'passed': browser['passed'], 'label': '브라우저·API 확인'},
        'codeHash': test_record['codeHash'],
        'reviewStatus': '정적 검토에서 구체적 결함 미발견 · 채택 판단 대기',
        'evidenceDir': 'evidence/copilot-run',
        'changedFiles': sorted(expected),
        'limitations': [
            'Copilot의 node --test는 권한 거절로 미실행. 사용자 별도 승인 뒤 로컬 테스트와 브라우저·API 검증을 실행했다.',
            '읽기 전용 Copilot 검토는 독립 감사나 사람의 최종 승인이 아니다. 결과 채택과 운영 배포는 승인되지 않았다.',
            '합성 데이터·localhost·모의 전송 데모이며 실제 발송, DB, 인증, 운영 배포는 없다.',
            '실제 앱 녹화와 실제 로그 발췌를 편집했다. 한국어 음성은 합성이며 영상 길이는 개발 소요시간이 아니다.',
            'VS Code UI, Copilot Studio, A2A 통신 프로토콜은 시연·검증하지 않았다.',
            '브라우저 기본 경로와 모바일 확인은 통과했지만 지연 응답 경쟁 조건의 브라우저 자동 검증과 전체 접근성 감사는 수행하지 않았다.',
            '촬영 전후 파일 해시를 대조했다. 시작 앱의 후속 favicon 수정은 별도 기록했고 촬영에는 해시가 일치하는 원본 스냅샷을 사용했다.',
        ],
    }
    (P / 'facts.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2) + '\n')
    dest = P / 'excerpts'
    dest.mkdir(exist_ok=True)
    manifest = {}

    def excerpt(name, value, source, transformation):
        lines = value.strip().splitlines()
        assert len(lines) <= 14, (name, len(lines))
        (dest / f'{name}.txt').write_text('\n'.join(lines) + '\n')
        manifest[name] = {'source': source, 'transformation': transformation,
                          'sourceSha256': digest(ROOT / source),
                          'excerptSha256': digest(dest / f'{name}.txt')}

    blocks = []
    for heading in ('● Read quote.mjs', '● Read server.mjs', '● Read app.js'):
        lines = logs['plan'].splitlines()
        i = lines.index(heading)
        blocks.append('\n'.join(lines[i:i+3]))
    excerpt('plan', '\n\n'.join(blocks), 'evidence/copilot-run/plan.log',
            'Three verbatim read-operation blocks selected; omitted intervening output.')

    lines = logs['implement'].splitlines()
    i = lines.index('● Edit')
    j = next(i for i, line in enumerate(lines) if line.startswith('✗ Run all Node tests'))
    excerpt('implement', '\n'.join(lines[i:i+5]) + '\n\n' + '\n'.join(lines[j:j+3]),
            'evidence/copilot-run/implement.log', 'Verbatim edit block and denied-test block; omitted intervening output.')

    lines = (E / 'changes.diff').read_text().splitlines()
    start = lines.index('+        if (!policy.sendAllowed) {')
    excerpt('diff', '--- baseline/src/server.mjs\n+++ run/src/server.mjs\n\n' + '\n'.join(lines[start:start+9]),
            'evidence/copilot-run/changes.diff', 'Verbatim server diff headers and blocking branch; omitted other hunks.')

    lines = (E / 'local-tests.log').read_text().splitlines()
    chosen = [line for line in lines if re.match(r'^ok [1-4] -', line)]
    chosen += [''] + [line for line in lines if re.match(r'^# (tests|pass|fail|cancelled|skipped) \d+$', line)]
    excerpt('tests', '\n'.join(chosen), 'evidence/copilot-run/local-tests.log',
            'Verbatim first four test outcomes and TAP totals; other tests retained in full source.')

    request = blocked['request']
    value = (f"{request['method']} {request['path']}\n" + json.dumps(request['body'], ensure_ascii=False) +
             f"\n\nHTTP {blocked['status']}\n" + json.dumps(blocked['response'], ensure_ascii=False, indent=2))
    excerpt('api', value, 'evidence/copilot-run/api-responses.json',
            'Formatting of the second recorded request/response object; no values changed.')

    conclusion = '구체적인 구현 결함은 발견하지 못했습니다.'
    limits = ('이 의견은 읽기 전용 AI 검토이며 인간 승인·독립 감사·결과 채택 또는 배포 승인이 아니고, 배포는 범위 밖입니다.')
    assert conclusion in logs['review'] and limits in logs['review']
    value = '\n'.join(textwrap.wrap(conclusion, 46)) + '\n\n' + '\n'.join(textwrap.wrap(limits, 46))
    excerpt('review', value, 'evidence/copilot-run/review.log',
            'Two verbatim sentences selected and reflowed for screen width; full reasoning and limits in source.')
    (P / 'excerpt-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')

    fallback = ROOT / 'demo/fallback'
    fallback.mkdir(exist_ok=True)
    for name, sha in test_record['codeHashes'].items():
        target = fallback / name
        if target.exists():
            assert digest(target) == sha, f'Refuse to overwrite changed fallback: {name}'
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(APP / name, target)
        assert digest(target) == sha
    print(f'EVIDENCE READY: {tests["pass"]} local tests, {browser["passed"]} browser/API checks, {len(manifest)} sourced excerpts.')
    print(f'CODE SHA256: {test_record["codeHash"]}')


if __name__ == '__main__':
    main()
