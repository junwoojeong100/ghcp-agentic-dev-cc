#!/usr/bin/env python3
"""Create the review-draft edit from existing real stills and app footage only."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'production/ghcp-live'
APP = 'evidence/ghcp-live/producer-verification/app/2026-09-26T09-15-54-621Z-4237d4a4/'
CAP = json.loads((RUN / 'capture-map.json').read_text())['captures']
scenes = []


def card(ident, title, narration, duration):
    scenes.append(dict(id=ident, title=title, narration=narration, duration=duration,
                       kind='card', approval=False, speed=1))


def still(ident, title, narration, duration, source, kind='app'):
    scenes.append(dict(id=ident, title=title, narration=narration, duration=duration,
                       source=source, kind=kind, approval=False, speed=1,
                       editLabel='촬영 당시 정지 캡처' if kind == 'terminal' else '별도 앱 캡처'))


def clip(ident, title, narration, end, source):
    scenes.append(dict(id=ident, title=title, narration=narration, source=APP + source,
                       kind='app', approval=False, speed=1, **{'in': 0, 'out': end},
                       editLabel='촬영 밖 제작 세션의 실제 앱'))


card('opening', '사업 요청에서 검증까지\n검토용 데모',
     '기존 견적 업무에 이익률 정책을 적용한 실제 개발 사례입니다. 테스트 결과와 검토에서 발견한 보완 사항을 함께 보여 드립니다.', 14)
clip('before-motion', '변경 전 · 기준 미달 견적도 모의 전송',
     '변경 전에는 이 견적도 모의 전송됩니다.', 6.6, '01-baseline-q1001-10-send.webm')
still('before-detail', '할인 10% · 매출총이익률 11.11%',
      '순매출 구백만 원에 예상 이익 백만 원입니다. 이익은 남지만, 회사가 정한 최소 십오 퍼센트 기준에는 미달합니다.', 13.4, CAP['before'])
card('policy', '15% 미만 차단\n정확히 15%는 허용',
     '정책은 단순합니다. 기준 미달 견적의 전송은 막고, 정상 견적은 그대로 처리합니다. 화면뿐 아니라 직접 호출한 서버에도 같은 기준을 적용합니다.', 16)
still('analysis', '서버와 화면 조사를 병렬 위임',
      '코파일럿이 서버와 화면 조사를 두 역할에 나눴습니다. 실제 호출과 보고서 반환이 기록돼 있습니다. 화면은 당시 정지 캡처입니다.', 16, CAP['analysis'], 'terminal')
still('approval', '구현 계획 승인 · 당시 정지 캡처',
      '사용자가 대화에서 구현 범위를 승인하고 제작자가 전달했습니다. 승인 연속 영상은 저장 오류로 확보하지 못했습니다. 이 화면은 정지 캡처입니다.', 16, CAP['approval'], 'terminal')
card('process', '구현 범위는 제한\n검증 결과는 보존',
     '기존 테스트와 의존성은 유지했습니다. 이후 로컬 검증은 일괄 승인 범위에서 진행했습니다. 운영 적용이나 외부 발송은 승인하지 않았습니다.', 14)
still('implementation', '표시 비율 대신 실제 금액으로 판정',
      '서버는 이익과 순매출을 정수 금액으로 비교합니다. 반올림된 화면 숫자만으로 전송 가능 여부를 결정하지 않습니다.', 14, CAP['implementation'], 'terminal')
card('red-green', '변경 전 2건 실패\n변경 후 56건 통과',
     '새 회귀 테스트는 변경 전 두 사례에서 실제로 실패했습니다. 예상한 차단 대신 성공 응답이 나왔기 때문입니다. 변경 후에는 기존 테스트를 포함한 쉰여섯 건이 통과했습니다.', 18)
still('tests', '테스트 통과가 검토를 대신하지 않습니다',
      '실제 실행 로그와 종료 코드를 보존했습니다. 테스트가 모두 통과해도, 빠진 예외 경로는 있을 수 있습니다. 그래서 별도 검토를 진행했습니다.', 14, CAP['tests'], 'terminal')
clip('blocked-motion', '변경 후 · 같은 입력의 전송 차단',
     '같은 견적은 이제 전송할 수 없습니다.', 5.2, '02-final-q1001-10-blocked.webm')
still('blocked-detail', '화면에는 이유 · 직접 API에는 HTTP 422',
      '차단 이유가 화면에 표시됩니다. 버튼을 거치지 않고 서버를 직접 호출해도 이익률 미달 응답을 반환하는 것을 확인했습니다.', 14.8, CAP['blocked'])
clip('allowed-motion', '정상 견적 · 모의 전송 유지',
     '할인을 없애면 기준을 충족하고 정상적으로 모의 전송됩니다.', 7.4, '03-final-q1001-0-allowed-sent.webm')
still('allowed-detail', '20% 견적의 정상 업무는 유지',
      '차단만 추가한 것이 아닙니다. 기준을 충족한 견적의 정상 흐름도 확인했습니다. 이 사례의 전송은 외부 발송 없는 모의 처리입니다.', 12.6, CAP['allowed'])
clip('exact-boundary', '정확히 15% · 허용',
     '', 4.2, '04-final-q1500-exact15-allowed.webm')
clip('rounded-boundary', '표시는 15.0% · 실제 기준 미달이면 차단',
     '', 4.2, '05-final-q1499-rounded15-blocked.webm')
still('boundary-detail', '원가 1원 차이도 판정에 반영',
      '두 견적 모두 화면에는 십오 퍼센트로 보일 수 있습니다. 하지만 원가가 경계보다 일 원 높으면 실제 이익률은 기준 미달입니다. 서버가 이 차이를 구분합니다.', 15.6, CAP['boundary'])
still('async-check', '입력 변경과 늦은 응답도 검사',
      '응답이 늦게 도착해도 이전 허용 상태가 최신 차단 상태를 덮지 않는지 검사했습니다. 잘못된 할인과 작은 화면도 확인했습니다.', 14, APP + '12-final-stale-preview-ignored.png')
still('review', '읽기 전용 검토 · 보완 사항 발견',
      '검토 역할이 코드와 실행 결과를 대조했습니다. 이 과정에서 테스트가 다루지 않은 응답 본문 오류 경로 한 건이 발견됐습니다.', 16, CAP['review'], 'terminal')
card('open-finding', '보완 필요 1건\n운영 적용 보류',
     '서버가 차단 응답을 보냈어도 본문을 읽지 못하면, 화면의 전송 버튼이 다시 켜질 수 있습니다. 서버의 이익률 차단을 우회하는 문제는 아닙니다. 코드 검토에서 발견했으며 실행 재현과 수정은 남아 있습니다.', 25)
card('capture-limit', '촬영 종료 오류\n승인 연속 영상 미확보',
     '코파일럿은 정상 종료했지만 녹화 파일 저장이 실패했습니다. 이 초안은 보존된 실제 정지 캡처와 별도로 녹화한 앱 동영상을 사용합니다. 완전한 촬영본은 아닙니다.', 18)
card('pilot', '업무 하나로 파일럿\n효과는 측정 후 판단',
     '다음 단계는 보완 사항을 수정하고 다시 검증하는 것입니다. 파일럿에서는 업무 하나와 책임자를 정하고, 리드타임과 검토 시간, 재작업을 비교하세요. 이번 데모로 생산성이나 비용 절감 효과를 보장하지 않습니다.', 21)

output = RUN / 'edit-list.json'
if output.exists():
    raise SystemExit('Edit list already exists; read before changing it')
output.write_text(json.dumps({'title': '사업 요청에서 검증까지 · 검토용 초안', 'scenes': scenes}, ensure_ascii=False, indent=2) + '\n')
print(f'{len(scenes)} scenes; {sum(s.get("duration", s.get("out", 0)) for s in scenes):.1f}s; no terminal video used')
