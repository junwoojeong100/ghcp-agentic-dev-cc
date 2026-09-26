# 합성 견적 데모 시작점

한국어 견적 작성 화면과 로컬 API로 구성한 **사전 준비된 기준 앱**입니다. 이 시작점은 Copilot이 생성한 결과물이 아닙니다. 이후 작업과 비교할 수 있도록 준비한 데모이며, 모든 고객·견적은 합성 데이터입니다.

견적 선택, 할인율 조정, 금액 미리보기, 모의 전송만 제공합니다. 수익률은 계산 결과를 보여 주는 값이며, 이 기준 앱에는 수익률에 따른 전송 제한이 없습니다. 외부 발송, 결제, 고객 데이터 저장은 하지 않습니다.

## 실행

Node.js 22 이상이 필요합니다. 외부 패키지나 설치 단계는 없습니다.

```sh
cd demo/starter
npm start
```

브라우저에서 `http://127.0.0.1:4310`을 엽니다. 서버는 기본적으로 `127.0.0.1`에만 바인딩합니다. 포트가 사용 중이면 다음과 같이 변경합니다.

```sh
PORT=4311 npm start
```

`Ctrl+C`로 서버를 종료합니다. `src/server.mjs`를 import하면 자동으로 서버가 시작되지 않습니다.

## 화면 사용

- 처음에는 `Q-1001 · 업무 시스템 구축`, 할인율 `10%`가 선택됩니다.
- 견적이나 할인율을 변경하면 서버에서 금액을 다시 계산합니다.
- 계산 중이거나 입력이 유효하지 않으면 전송 버튼이 잠시 비활성화됩니다.
- 금액을 확인한 뒤 **견적 보내기**를 누르면 모의 전송 결과가 표시됩니다.
- 입력을 변경하면 이전 성공 메시지는 즉시 사라집니다. 늦게 도착한 이전 응답은 반영하지 않습니다.
- 모든 실제 발송은 생략되며, 성공 메시지에도 외부 발송이 없었음을 명시합니다.

1920×1080 녹화를 고려한 레이아웃이며 작은 화면에서는 세로로 배치됩니다. 촬영 시 색상이 달라지지 않도록 밝은 테마를 고정했습니다. 주요 조작 요소는 `#quote-id`, `#discount`, `#send-quote`, `#quote-form`이며, 출력 요소는 `#net-amount`, `#profit-amount`, `#margin-value`, `#feedback`입니다.

## API

모든 JSON 응답에는 `Cache-Control: no-store`가 적용됩니다.

### `GET /api/quotes`

```json
{ "quotes": [{ "id": "Q-1001", "title": "업무 시스템 구축", "customer": "가상 고객 A", "listPriceWon": 10000000, "costWon": 8000000 }] }
```

위 응답은 형식 예시입니다. 실제 준비 데이터에는 `Q-1001`, `Q-1500`, `Q-1499`, `Q-1501` 네 견적이 포함됩니다.

### `POST /api/preview`

요청은 다음 두 필드만 허용합니다. 금액·원가를 비롯한 추가 필드는 거부합니다.

```json
{ "quoteId": "Q-1001", "discountBps": 1000 }
```

- `quoteId`: 비어 있지 않은 문자열, 최대 100자
- `discountBps`: 정수 `0..10000`. `1000`은 화면의 `10%`에 해당합니다.
- 본문은 UTF-8 JSON 객체여야 하며 최대 16KB입니다.

응답 필드는 `quoteId`, `title`, `customer`, `listPriceWon`, `costWon`, `discountBps`, `netWon`, `profitWon`, `marginPercent`입니다. 견적 금액과 원가는 서버의 합성 데이터에서만 읽습니다.

### `POST /api/send`

동일한 두 필드를 받으며, 서버에서 다시 계산한 후 다음 모의 결과를 반환합니다.

```json
{
  "sent": true,
  "simulation": true,
  "message": "모의 전송 완료 — 외부 발송 없음",
  "quote": {
    "quoteId": "Q-1001",
    "title": "업무 시스템 구축",
    "customer": "가상 고객 A",
    "listPriceWon": 10000000,
    "costWon": 8000000,
    "discountBps": 0,
    "netWon": 10000000,
    "profitWon": 2000000,
    "marginPercent": 20
  }
}
```

### 오류

- `400`: 잘못된 JSON, 허용하지 않은 필드·타입·범위, 16KB 초과, 0원 이하인 최종 금액. `{ "error": { "code": "INVALID_INPUT", "message": "…" } }`
- `404`: 없는 견적은 `QUOTE_NOT_FOUND`, 없는 경로·지원하지 않는 메서드는 `NOT_FOUND`
- 예상하지 못한 서버 오류는 내부 정보를 노출하지 않는 `500 INTERNAL_ERROR`

정적 파일은 `/`, `/index.html`, `/app.js`, `/style.css`만 제공합니다.

## 계산 규칙과 코드 위치

`src/quote.mjs`는 `QUOTES`, `calculateQuote(quote, discountBps)`, `parseQuoteInput(body)`를 export합니다.

- 기준 금액·원가: 1원부터 10억 원까지의 안전한 정수
- 최종 금액: `Math.floor((listPriceWon * (10000 - discountBps) + 5000) / 10000)`
- 예상 수익: `netWon - costWon`
- 수익률: `profitWon / netWon * 100` (표시용)
- 원 단위 반올림 후 최종 금액이 0원 이하면 오류입니다. 따라서 100% 할인은 입력 범위에는 포함되지만 견적 계산은 거부됩니다.
- 화면의 수익률은 소수 둘째 자리까지 표시하므로 실제 계산값보다 정밀도가 낮습니다.

`src/server.mjs`의 `createAppServer({ quotes = QUOTES } = {})`로 테스트용 데이터를 주입할 수 있습니다. UI는 `public/index.html`, `public/app.js`, `public/style.css`에 있습니다.

## 테스트

```sh
npm test
```

`node:test`만 사용합니다. 정수 금액과 반올림, 입력 스키마, API 응답, 정적 파일 허용 목록, 요청 크기 제한, 업로드 연결 종료를 검증합니다. HTTP 테스트는 루프백의 임시 포트를 사용하고 각 서버와 연결을 종료합니다. 전송 API의 기본 회귀 테스트는 할인율 0%인 정상 견적을 사용합니다.

이 앱은 로컬 데모용이며 인증, 실서비스 운영, 외부 발송을 위한 구현이 아닙니다.
