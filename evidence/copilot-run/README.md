# 실행 근거와 공유 사본

## 저장소에 포함된 것

- `baseline-snapshot/`: 구현 전 원본 스냅샷. 당시 경로별 SHA-256과 대조했다.
- `baseline-hashes.json`, `baseline-provenance.json`: 원본 식별 및 보존 경위.
- `baseline-tests.log`: 시작 앱의 실제 테스트 22개 통과 기록.
- `local-tests.log`: Copilot의 테스트 실행 거절 후 사용자가 승인한 별도 로컬 실행. 26개 통과.
- `changes.diff`: 보존된 원본과 실제 Copilot 작업본의 코드 차이.
- `browser-checks.json`, `api-responses.json`: 실제 UI·모바일·직접 API 확인.
- [공유용 기록](../shareable/): 계획·구현·검토 로그와 실행 기록, PPT/영상 QA 메타데이터의 식별 정보 가림 사본.

## 원본과 가림 사본

세션 UUID와 제작자 개인 절대 경로가 담긴 원본 로그·stage record·사용량 정보·Copilot 내부 로그는 로컬에만 보존한다. 가림 사본은 **원본 그대로가 아니다.** [변환 목록](../shareable/manifest.json)은 원본 경로·원본 SHA-256·사본 SHA-256·치환 규칙을 기록한다.

[영상 발췌 목록](../../production/excerpt-manifest.json)의 sourceSha256은 원본 로그를 가리킨다. 가림 사본의 해시로 이를 바꾸지 않았다. 공유 검증 스크립트는 변환 목록을 통해 관계를 확인하지만, 누락된 원본의 내용을 재구성하거나 독립적으로 진위 인증하지는 못한다.

**실행 전 복원 조건:** 아래 검증은 **초기 릴리스의 원래 산출물**을 대상으로 한다. 별도 복사본에서 [초기 릴리스 아카이브](https://github.com/junwoojeong100/ghcp-agentic-dev-cc/tree/fd819fb7183a3baab71c17e3cf4f82e06330852f/deliverables)의 구 MP4·SRT(`ghcp-cxo-demo-ko.mp4`, `.srt`)와 이미 삭제된 PPTX·PDF(`ghcp-agentic-development.pptx`, `.pdf`)를 `deliverables/` 아래 원래 경로에 복원한 뒤 실행한다. 현재 트리 그대로는 이 파일들이 없어 통과할 수 없다. 이후 headless 초안의 MP4·SRT는 이 검사의 대상이 아니다.

```sh
# 복원한 별도 복사본의 프로젝트 최상위에서, 표준 Python만 필요
python3 scripts/verify-shareable-evidence.py
```

이 검사는 변경·보관본의 기록된 코드 해시, 보존된 시작 앱, 공유 사본, 영상 발췌, 최종 파일을 확인한다. 테스트를 다시 실행하거나 승인 여부를 판단하지 않는다. 새 실행은 `npm --prefix demo/run test`로 별도로 수행한다.

## 재생성 경계

`assemble-evidence.py`와 `prepare-shareable-evidence.py`는 전체 원본이 있는 제작 환경 전용이다. 클론에는 가림 사본만 있으므로 이 두 스크립트로 원본 검증을 흉내 내지 않는다. 이미 확정된 `production/facts.json`, 화면 캡처, 편집된 소스 클립과 대본으로 PPT·영상은 다시 만들 수 있다. 제작 도구와 절차는 [루트 README](../../README.md)에 있다.

촬영 후 보고된 정적 검토 제약, 청취·네이티브 PPT 확인의 한계는 [제작 상태](../../docs/production-status.md)에 정리했다. 원본의 ‘결함 미발견’ 의견을 사후에 수정하지 않았다.
