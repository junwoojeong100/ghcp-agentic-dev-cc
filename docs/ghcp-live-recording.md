# GitHub Copilot CLI headless 촬영

CXO 고객에게 **업무 요청 → 작동하는 변경 → 검증 근거 → 사람의 결정**을 보여 주는 제작 절차입니다. 실제 `copilot`을 PTY에서 실행하고 그 출력을 xterm에 실시간 전달하여 Playwright Chromium headless로 녹화합니다. 네이티브 Terminal 화면 녹화나 과거 로그 재생은 아닙니다.

**최신 상태는 `production/ghcp-live/retake-20260926/run.json`과 해당 take의 기록을 확인합니다.** 첫 촬영의 `production/ghcp-live/run.json`은 과거 기록입니다. 녹화기 시험 통과와 실제 촬영·업무 검증은 구분합니다.

## 2026-09-26 재촬영 완료

녹화기의 원격 `saveAs` 전송 중 8초 제한 후 브라우저를 종료하던 경로를 로컬 원본 보존·크기별 제한 시간·취소 가능한 복사·해시 검증 방식으로 수정했습니다. 녹화기 시험 **44건 통과** 후 새 실제 take를 실행했고, 정상 CLI 종료·마지막 출력 렌더링·영상 복사와 원본 전체 디코딩을 확인했습니다. 이전 불완전 원본은 변경하지 않았습니다.

새 실행은 미구현 기준 앱에서 시작해 병렬 조사 → 이번 계획·로컬 실행 승인 → 회귀 실패 → 구현 → 전체 테스트·브라우저 검사 → 읽기 전용 검토로 진행했습니다. **회귀 17건 실패 → 신규 33건 통과, 전체 55건 통과**, Chromium 6개 화면 폭·36개 상태를 기록했습니다. 422 본문 오류의 잠금·복구는 촬영 종료 후 같은 코드의 실제 Chromium·로컬 프록시 검사로도 확인했습니다. 검토자는 TAP 로그를 읽지 못했으므로 그 한계와 생산자가 확인한 실행 결과를 구분합니다.

최신 파일은 `deliverables/ghcp-live/final/`의 5분 MP4, SRT, 소개 포함 16장 PPT·PDF와 6분 20초 계획 승인 보충 영상입니다. PPT·PDF는 사용자 요청으로 최신 한 쌍만 유지합니다. 본편의 서버 수정 요청 구간은 일괄 승인 범위 내 제작자 대행이며 연속·정속, 계획 승인 전체 구간은 보충 영상입니다. 전체 디코딩·자막·파일 해시·음소거 재생과 AI 시각 표본 검사는 완료했지만 사람의 전체 청취·실제 PowerPoint 링크 재생은 미실시입니다. 로컬 전달용이며 외부 게시·운영 배포·최종 채택은 하지 않았습니다.

[최신 파일 안내](../deliverables/ghcp-live/final/READ-ME-FIRST.txt) · [최종 검사](../production/ghcp-live/retake-20260926/final-retake-verification.json)

## 첫 촬영 결과 — 역사적 기록

실제 Copilot의 병렬 조사·구현·테스트·읽기 전용 검토 기록은 확보했습니다. 변경 전 회귀 테스트는 **2건 실패**, 변경 후 전체 테스트는 **56건 통과**입니다. 검토에서 HTTP 422 응답 본문 파싱·수신 실패 시 UI 잠금이 풀릴 수 있는 **미해결 사항 1건**이 확인돼 운영 적용은 보류 상태입니다. 이 예외 경로의 실행 재현·수정은 하지 않았고, 서버 차단 우회 문제는 아닙니다.

CLI는 정상 종료했으나 녹화기가 `TargetClosedError`로 종료돼 `capture.json`을 쓰지 못했습니다. `capture.webm`은 헤더상 14,674.88초지만 실제 패킷은 5,072.48초에서 끊깁니다. 이후의 승인·구현 연속 영상은 미확보이며, 성공한 촬영으로 처리하지 않습니다. 원본·정지 캡처·실행 로그는 보존합니다.

`deliverables/ghcp-live/` 자료는 **검토용 초안**으로, 당시 실제 정지 캡처와 별도로 녹화한 앱 동영상을 사용합니다. 원래 요청한 촬영 완료본이 아닙니다. `--draft`는 이 제약을 표시하는 별도 제작 경로이고 기본 완료본 검사를 완화하지 않습니다. 생성된 파일과 검사 범위는 해당 폴더의 `READ-ME-FIRST.txt` 및 `production/ghcp-live/deliverable-verification.json`을 확인하세요.

아래 절차는 녹화 도구 사용 설명이며, 종료·저장이 모든 상황에서 정상 동작했다는 실증 결과가 아닙니다. 이번 실패의 정확한 내부 원인은 아직 규명하지 않았습니다.

## 구성과 실행

- 준비 자료: `production/ghcp-live/{change-request.md,initial-prompt.txt,agents/}`
- 미구현 기준 앱·해시: `evidence/ghcp-live/baseline-snapshot/`, `baseline-hashes.json`
- PTY: `scripts/copilot-pty-bridge.py` (Python 표준 라이브러리)
- 녹화기·로컬 제어: `scripts/record-ghcp-live.mjs` (Playwright 1.63.0)
- 실제 화면 렌더러: `production/ghcp-live/terminal.html`, `terminal.js` (`@xterm/xterm` 6.0.0)

브라우저는 창 없이 실행하며 화면 기록 권한이나 일반 브라우저 조작이 필요 없습니다. 실제 CLI는 네트워크·기존 Copilot 이용 권한을 사용합니다. 녹화기 내부 서버는 loopback 전용입니다. 모델을 바꾸거나 전역 설정·인증을 수정하지 않습니다.

```sh
# 먼저 무해한 fixture 기반 녹화기·초기화·장애 처리 검증
npm run test:recorder

# 실제 촬영: 사용자 승인된 제작 세션에서 백그라운드로 유지
node scripts/record-ghcp-live.mjs --run-dir production/ghcp-live
```

실제 촬영을 시작하면 보존된 기준 앱·요청서·역할을 해시 검증한 뒤 새 `$HOME/ghcp-headless-demo-*` 작업공간에 복사합니다. 이미 열어 둔 Terminal과 그 작업공간은 건드리지 않습니다. 기능을 미리 구현하지 않으며 완성본이나 과거 diff·로그를 새 CLI에 제공하지 않습니다.

브라우저·녹화·출력 연결이 준비된 뒤 **CLI 프로세스 하나**를 시작합니다. 새 take는 `evidence/ghcp-live/raw/headless.*` 아래에 저장되며 `ready` 출력에 제어 파일 경로가 나옵니다. 제어 파일에는 로컬 비밀값이 있으므로 내용을 출력하거나 공유하지 않습니다.

## 현재 화면 확인 및 사람의 응답

아래 명령의 `<control.json>`은 `ready`가 알려 준 경로입니다. 비밀값을 명령줄에 넣지 않습니다.

```sh
node scripts/record-ghcp-live.mjs inspect --control <control.json>
```

같은 녹화 페이지의 PNG, 현재 활성 터미널 버퍼, snapshot ID와 sequence가 반환됩니다. 화면은 미리 작성한 텍스트가 아니라 실제 CLI 출력입니다. `Read`로 PNG를 열어 **현재 요청·선택지·선택 상태**를 확인합니다.

1. 실제 승인 요청과 범위, 전달할 선택을 사용자에게 이 대화에서 제시합니다.
2. 사용자의 명시적인 응답을 기다립니다. 과거 승인·제작 계획 승인·무응답을 대신 사용하지 않습니다.
3. 화면 상태가 일치할 때만 해당 키를 한 번 전달합니다. 화면이 바뀌면 재확인합니다.
4. 거절·취소는 그대로 전달하며 다른 경로나 세션으로 우회하지 않습니다.

```sh
# 실제 사용자가 현재 화면의 해당 선택을 승인한 경우에만 실행
node scripts/record-ghcp-live.mjs respond --control <control.json> \
  --snapshot <snapshot-id> --key ENTER --kind user-decision \
  --decision '<실제 사용자 응답과 그 적용 범위>'
```

지원 키는 `ENTER`, `ESCAPE`, `UP`, `DOWN`, `TAB`, `SHIFT_TAB`, `CTRL_C`, 단일 ASCII 문자입니다. 메뉴를 옮기는 키도 현재 화면에 맞는 snapshot을 사용합니다. 오래된 snapshot과 이미 사용한 snapshot은 거절합니다. 응답의 전송 확인이 없으면 자동으로 재전송하지 않습니다.

승인 아닌 화면 탐색에는 `--kind navigation`과 실제 목적을, 업무 요청에는 `--kind task-request`를 사용합니다. 이 분류는 기록용이며 동의의 독립적 증명이 아닙니다. 업무 요청 텍스트는 `--text-file production/ghcp-live/initial-prompt.txt`로 bracketed paste하며 **자동 제출하지 않습니다.** 실제 입력 필드에 안전하게 들어갔는지 새 캡처에서 확인한 뒤 Enter를 별도로 보냅니다. 신뢰·권한 메뉴에서는 텍스트를 붙여 넣지 않습니다.

렌더러는 입력 권한이 없으며 키보드 이벤트를 CLI에 연결하지 않습니다. 터미널 장치·커서 질의에 대한 제한된 프로토콜 응답은 사용자 승인과 분리됩니다. 화면에 표시되는 기록 문구:

> 브라우저 터미널 실시간 중계 · 사용자 결정은 대화에서 확인 후 전달

비밀번호·토큰 입력이 필요하면 촬영을 중단하고 별도로 인증합니다. 비밀값을 이 대화나 녹화 페이지에 입력하지 않습니다. 원본에는 계정·개인 경로가 들어갈 수 있으므로 검수 전에는 공유하지 않습니다.

## 실제 조사·승인·구현

- 기본 에이전트가 `cxo-server-analysis`, `cxo-ui-analysis`에 조사 작업을 위임합니다. 역할 도구는 read/search뿐이며, 실제 로딩과 실행 여부를 `/agent`, `/tasks` 등 실제 화면에서 확인합니다.
- 현재 사용 중인 모델을 화면에서 기록합니다. 병렬 실행은 실행 구간이 실제 겹친 경우에만 그렇게 설명합니다.
- 통합 계획을 사용자에게 제시하고 승인받기 전에는 앱 코드나 회귀 테스트를 추가하지 않습니다.
- 승인 후 동일한 작업공간에서 업무 규칙 때문에 실패하는 회귀 테스트 → 코드 변경 → 전체 테스트를 촬영합니다. 실패 원인이 import·문법 오류이면 업무 기준의 실패로 설명하지 않습니다.
- 실제 도구 실행 허가는 계획 동의와 별개입니다. `-p`, `--no-ask-user`, 전체 사전 허용, Autopilot, 원격 PR을 만드는 `/delegate`는 사용하지 않습니다.
- 기존 런처의 `COPILOT_ALLOW_ALL`, `COPILOT_ASSISTED_APPROVAL` 점검과 `--mode plan`, `--no-auto-update`, `--no-remote`, `--no-remote-export`, `--disable-builtin-mcps`를 유지합니다. 저장된 권한과 사용자 정의 MCP까지 없다는 의미는 아니므로 실제 화면에서 확인합니다.
- 작성자는 하나이며 `cxo-review`가 실제 코드·diff·테스트 결과로 읽기 전용 검토를 합니다. 이 검토는 독립 감사나 사람의 최종 채택 승인이 아닙니다.

준비 당시 시작본 테스트 22개와 11.11% 견적의 HTTP 200을 제작 세션에서 확인했습니다. 이 수치는 촬영 중 Copilot 결과로 재사용하지 않습니다. 루트 `npm test`는 **과거** `demo/run`을 검사하므로 새 take의 실제 작업공간에서 테스트합니다.

## 종료·오류·근거

CLI가 정상 종료하면 마지막 출력의 화면 반영을 확인한 뒤 영상을 **자동으로 확정**합니다. 종료된 제어 서버에 다시 finish를 호출할 필요는 없습니다. 실행 중 촬영을 중단할 때만 다음 명령을 사용합니다.

```sh
node scripts/record-ghcp-live.mjs finish --control <control.json>
```

CLI가 살아 있을 때 finish하면 해당 CLI만 정리하고 **incomplete**로 기록합니다. 장애가 발생해도 입력을 즉시 닫고 제한 시간 안에 녹화·브라우저·제어 서버를 정리합니다. 실제 녹화 명령은 불완전한 촬영을 종료 코드 1로 보고합니다. 정상 종료와 마지막 출력 렌더링 확인 후 context를 닫고 원본 WebM을 저장하며, 원본 길이에 5분을 강제하지 않습니다.

- `capture-start.json`: take·작업공간·원본 영상 위치
- `workspace-baseline.json`: 실제 시작 코드·요청서·역할 해시
- `terminal.ansi`, `pty-events.jsonl`: 원시 출력과 PTY 사건
- `snapshots/`: 실제 응답 판단에 사용한 PNG·활성 버퍼
- `capture-events.jsonl`: 상태·입력 중계·오류 기록
- `capture.json`: 정상/불완전 상태·종료 코드·출력/렌더 sequence·영상 해시
- `control.json`: 비공개 로컬 제어 정보; 공유 대상 아님

스트림 유실·브라우저/CLI 장애·저장 공간 부족은 입력 중단과 불완전 상태로 남깁니다. 자동 재시작하거나 로그 재생으로 빈 장면을 채우지 않습니다. `--fixture`는 무해한 녹화기 검사 전용이며 영상에도 **FIXTURE / Copilot 실행 아님**을 표시합니다. 정상 저장은 녹화가 완료됐다는 뜻이지 업무 구현의 성공을 뜻하지 않습니다.

실제 근거 확보 후 새 최종 코드로 UI·API·정상·경계값을 확인하고 약 5분 한국어 영상과 편집 가능한 12장 PPT를 만듭니다. 기존 발표본은 유지하고 새 결과는 `deliverables/ghcp-live/`에 저장합니다. 승인 요청→응답 전달→후속 실행은 연속·정속으로 남깁니다. 다른 구간의 배속·생략은 표시합니다. 5분은 개발 시간이나 생산성 지표가 아닙니다.

## CXO 고객 메시지

**“사업의 요청을 작동하는 변화로. 검증 근거를 남기고, 결정은 사람에게.”**

최소 이익률 정책 적용, 정상 업무 유지, 직접 API에도 같은 정책 적용을 실제 동작으로 설명합니다. 모든 견적은 합성이고 전송은 모의 처리입니다. 측정 없는 절감률·생산성 배수는 넣지 않으며 후속 파일럿에서 리드타임·검토 시간·재작업을 기존 유사 업무와 비교합니다.

[에이전트 호출](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/invoke-custom-agents) · [에이전트 설정](https://docs.github.com/en/copilot/reference/custom-agents-configuration) · [대화형 사용](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/overview)
