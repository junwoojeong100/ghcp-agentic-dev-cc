# GitHub Copilot · Agentic Development in Action

CXO 대상 한국어 발표 자료입니다. 작은 견적 기능 하나를 실제 Copilot CLI로 변경한 과정과 검증 근거를 담았습니다.

## 최신 제작본 · 실제 재촬영 반영

**[5분 영상](deliverables/ghcp-live/final/ghcp-cxo-demo-ko.mp4)과 [16장 PPT](deliverables/ghcp-live/final/ghcp-agentic-development.pptx)를 사용하세요.** Copilot 개요·사용 서피스·IDE와 CLI의 모드·에이전트 기능 4장을 서두에 넣고, 새 실제 촬영 결과를 반영했습니다. 소개의 공식 출처와 확인일(2026-09-26)은 발표자 노트에 있습니다.

| 파일 | 내용 |
| --- | --- |
| [영상 MP4](deliverables/ghcp-live/final/ghcp-cxo-demo-ko.mp4) | 5분 · 1080p · 한국어 합성 내레이션·화면 자막 |
| [편집 가능한 PPT](deliverables/ghcp-live/final/ghcp-agentic-development.pptx) | 소개 4장 + 실제 데모 12장 · 발표자 노트 |
| [PDF](deliverables/ghcp-live/final/ghcp-agentic-development.pdf) | 16페이지 열람본 |
| [SRT 자막](deliverables/ghcp-live/final/ghcp-cxo-demo-ko.srt) | 39개 자막 · 본편 자막과 중복 표시하지 않기 |
| [계획 승인 보충 영상](deliverables/ghcp-live/final/ghcp-plan-approval-continuous.mp4) | 6분 20초 · 대기 포함 연속·정속 · 음성 없음 |
| [발표·검수 안내](deliverables/ghcp-live/final/READ-ME-FIRST.txt) | 파일 사용법·검증 범위·한계·재생성 |

PPT와 본편 MP4는 같은 폴더에 두세요. **PPT·PDF는 최신 한 쌍만 남겼습니다.** 이전 영상과 원본 근거는 보존했습니다.

- 새 Copilot 실행: 변경 전 회귀 **17건 실패** → 변경 후 신규 33건 통과, 기존 22건을 포함한 **전체 55건 통과**. 실패·건너뜀 0.
- Chromium: 6개 화면 폭의 **36개 상태** 확인. 촬영 후 별도 실제 브라우저 검사에서 422 JSON 오류·본문 단절·타임아웃의 잠금 유지와 복구도 확인했습니다.
- 읽기 전용 검토: 확인 범위 내 확정 결함 0건. 검토자의 TAP 미열람 한계는 기록에 남겼으며 실행 수치는 같은 세션의 원시 도구 반환에서 별도 확인했습니다.
- 녹화기 저장 오류를 수정하고 시험 44건을 통과했습니다. 새 실제 녹화는 정상 저장됐으며 원본·본편·보충 영상 전체 디코딩을 확인했습니다.
- 본편의 권한 장면은 **사전 일괄 승인 범위에서 제작자가 대행**한 구간입니다. 계획 승인 전체는 보충 영상에 보존했습니다. 정지 캡처·별도 앱 녹화·설명 카드는 구분해 표시합니다.
- 파일 해시·자막 타이밍·PPT/PDF·음소거 재생·대표 프레임 검사는 완료했습니다. **사람의 전체 시청·청취와 실제 PowerPoint 링크 재생은 미실시**이며 상영 전 확인이 필요합니다.

[최종 검사](production/ghcp-live/retake-20260926/final-retake-verification.json) · [사실·출처](production/ghcp-live/retake-20260926/facts.json) · [최종 코드](demo/ghcp-live/final/) · [촬영 상태](production/ghcp-live/retake-20260926/run.json) · [촬영 절차](docs/ghcp-live-recording.md)

합성 견적·모의 전송이며 운영 채택·배포는 수행하지 않았습니다. 제작 당시에는 로컬 전달만 했으며, 이후 사용자 요청으로 비공개 GitHub 저장소에 커밋·푸시하는 범위를 승인받았습니다. 로컬 경로가 보이는 터미널 화면이 있으므로 공개 공유 전 개인정보 검수를 하세요. 영상 길이는 개발 소요시간이나 ROI 지표가 아닙니다.

**저장소 포함 범위:** 소스·테스트·최종 산출물·편집 입력·검사 요약을 포함합니다. 제어 토큰, 전체 원본 녹화와 Copilot 원본 세션/도구 로그(`raw/`, `native-session/`), 임시 브라우저 파일은 로컬에만 보존합니다. `facts.json`·`timeline.json`의 원본 참조와 해시는 변경하지 않았으므로, 일부 참조는 클론에 존재하지 않습니다. 최종 파일 열람과 앱 테스트는 가능하지만 원본 기반 재생성·전체 근거 검증은 해당 로컬 원본이 필요합니다.

## 이전 작업 기록

첫 headless 촬영은 저장 실패와 UI 예외 경로 1건으로 초안을 남겼습니다. [당시 기록](production/ghcp-live/run.json)과 [별도 수정본 검증](evidence/ghcp-live/repair-verification/resolution.json)은 역사적 자료입니다. 별도 수정본의 62개 테스트와 새 재촬영의 55개 테스트는 서로 다른 실행이며 합산하지 않습니다.

아래는 **이전 제작본**의 안내와 검증 결과입니다. 최신 파일은 위 표를 사용하고, 과거 수치·근거와 섞지 않습니다.

## 이전 제작본 발표하기

**[5분 영상 MP4](deliverables/ghcp-cxo-demo-ko.mp4)를 다운로드해 전체 화면으로 재생하세요.** 한국어 합성 내레이션과 화면 자막이 포함돼 있어 인터넷 없이 상영할 수 있습니다. GitHub 미리보기가 지원되지 않으면 파일 페이지의 다운로드 버튼을 사용하세요.

| 파일 | 용도 |
| --- | --- |
| [발표 영상](deliverables/ghcp-cxo-demo-ko.mp4) | 정확히 5분 · 1080p · H.264/AAC |
| 이전 PPT·PDF | 사용자 요청으로 삭제. 위의 최신 16장 자료만 유지 |
| [SRT 자막](deliverables/ghcp-cxo-demo-ko.srt) | 별도 자막 파일 |
| [발표자 가이드](docs/presenter-guide.md) | 준비·화면별 멘트·라이브 시연·장애 대응 |
| [최종 검사와 한계](docs/production-status.md) | 실제 수행한 검사와 남은 확인 사항 |

자막이 이미 영상에 있으므로 기본 상영에서는 외부 SRT를 끄세요. 발표 전 사용할 컴퓨터에서 음량·한국어 발음·전체 화면을 확인하세요. PPT는 질의응답용으로 함께 사용할 수 있습니다.

## 데모의 핵심

> 이익률 15% 미만 견적의 모의 전송을 막아 주세요.

- 판매가 1,000만 원·원가 800만 원에서 10% 할인하면 이익률은 약 **11.1%**입니다.
- 변경 전에는 전송되지만 변경 후에는 이유를 표시하고 차단합니다.
- 할인하지 않은 **20%** 견적과 정확히 **15%**인 견적은 허용합니다.
- 원가가 1원 높아 기준을 밑돌면 표시가 15.0%여도 차단합니다.
- 버튼을 거치지 않은 직접 API 요청도 서버가 검사합니다.

모든 데이터는 합성이며 외부로 견적을 보내지 않습니다. 앱 실행 중에는 서버 코드가 규칙을 적용하며, 매번 AI가 견적을 판단하지 않습니다.

## 실제 실행과 검증

- **Copilot CLI:** 계획, 4개 파일의 실제 변경, 별도의 읽기 전용 검토.
- **별도 로컬 검증:** 사용자 승인 후 Node 테스트 **26개 통과**, 브라우저·API 확인 **22개 통과**.
- **시작 앱:** 미리 준비한 코드. 기존 테스트 **22개 통과**이며 변경 후 26개에 포함되는 테스트를 중복 합산하지 않습니다.

Copilot 내부의 `node --test`는 권한 거절로 실행되지 않았습니다. 이후 사용자가 승인한 별도 로컬 실행의 결과를 Copilot이 직접 테스트한 것으로 설명하지 않습니다. 검토 의견은 독립 감사·최종 채택·운영 배포 승인이 아닙니다.

수치와 촬영 당시 한계는 [facts.json](production/facts.json), 공유용 Copilot 기록은 [식별 정보를 가린 사본](evidence/shareable/), 테스트·API·diff는 [실행 근거](evidence/copilot-run/)에 있습니다. [발췌 목록](production/excerpt-manifest.json)과 [사본 변환 목록](evidence/shareable/manifest.json)으로 출처를 연결합니다. 원본 로그의 해시와 가림 처리한 사본의 해시는 서로 다릅니다. 원본은 제작 환경에만 보존했습니다.

영상은 실제 앱 녹화와 실제 실행 로그 발췌를 편집한 것입니다. **5분은 개발 소요시간이 아닙니다.** VS Code UI, Copilot Studio, A2A 통신 프로토콜, PR·병합·운영 배포는 시연하지 않았습니다. 비공개 GitHub 업로드는 제작 완료 후 별도로 요청된 결과물 전달 작업입니다.

## 앱 실행

Node.js 22 이상에서 프로젝트 최상위 폴더 기준:

```sh
# 변경 후 앱 — 다른 프로세스가 사용 중이면 PORT를 바꾸세요
PORT=4411 npm --prefix demo/run start
```

`http://127.0.0.1:4411`을 엽니다. 종료는 해당 터미널에서 Ctrl+C입니다. 변경 전 앱은 `demo/starter`, 보존된 변경 후 앱은 `demo/fallback`입니다. 앱 자체에는 외부 패키지가 필요 없습니다. [작업본 사용 안내](demo/run/USAGE.md)를 참고하세요.

```sh
npm --prefix demo/run test
python3 scripts/verify-shareable-evidence.py
```

첫 명령은 현재 코드에서 테스트를 **새로 실행**하고, 두 번째는 기록된 코드·발췌·공유 사본·최종 파일의 해시를 **읽기 전용으로 확인**합니다. 과거의 실행 기록을 덮어쓰지 않습니다.

촬영에는 [원본 스냅샷](evidence/copilot-run/baseline-snapshot/)을 사용했습니다. 현재 `demo/starter`에는 이후의 favicon CSP 수정이 있어 정확한 구현 diff의 기준은 스냅샷입니다. [보존 경위](evidence/copilot-run/baseline-provenance.json)를 함께 남겼습니다.

## 클론에서 자료 재생성

최종 파일·화면 캡처·편집된 원본 클립·스크립트가 포함돼 있습니다. 음성 중간 파일과 원본 세션 로그는 포함하지 않았습니다. 아래 명령은 같은 이름의 산출물을 덮어쓰므로 수작업 편집본은 먼저 다른 이름으로 보존하세요.

```sh
# 기존 근거와 캡처로 PPT 재생성
python3 scripts/build-deck.py --require-screenshots

# 보존된 녹화 클립·대본·발췌로 영상 재생성 (macOS)
python3 scripts/build-video.py
python3 scripts/verify-video.py
```

- PPT: Python, `python-pptx==1.0.2`, Pillow. 글꼴이 다르면 `--font "Malgun Gothic"`을 지정하고 줄바꿈을 확인하세요.
- 영상: macOS 한국어 Yuna 음성·Apple SD Gothic Neo, FFmpeg(H.264/AAC), Pillow. 환경별로 다시 만든 영상의 해시는 달라질 수 있습니다.
- 녹화 또는 브라우저 재생 검사를 다시 할 때: `npm ci`, `npx playwright install chromium` 후 해당 스크립트를 실행하세요. `capture-demo.mjs`는 기존 캡처와 브라우저·API 기록을 덮어쓰므로 별도 복사본에서 실행합니다. 새 녹화는 과거 실행 자체의 재현 증거가 아닙니다.
- `assemble-evidence.py`와 `prepare-shareable-evidence.py`는 **원본 로그가 있는 제작 환경 전용**입니다. 클론에서 누락된 원본을 가림 처리한 사본으로 대체해 실행하지 마세요. 클론 확인에는 `verify-shareable-evidence.py`를 사용합니다.

[공유 근거 안내](evidence/copilot-run/README.md)에 포함·제외 파일과 해시 검증의 한계를 설명했습니다. 영상과 PPT를 열어 보는 데는 제작 도구가 필요 없습니다.
