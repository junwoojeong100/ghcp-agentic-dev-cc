// Every quote is invented for this demo. No real customers or transactions.
export const QUOTES = Object.freeze([
  Object.freeze({
    id: 'Q-1001',
    title: '업무 시스템 구축',
    customer: '가상 고객 A',
    listPriceWon: 10_000_000,
    costWon: 8_000_000,
  }),
  Object.freeze({
    id: 'Q-1500',
    title: '사내 포털 구축',
    customer: '가상 고객 B',
    listPriceWon: 10_000_000,
    costWon: 8_500_000,
  }),
  Object.freeze({
    id: 'Q-1499',
    title: '문서 관리 시스템 구축',
    customer: '가상 고객 C',
    listPriceWon: 10_000_000,
    costWon: 8_500_001,
  }),
  Object.freeze({
    id: 'Q-1501',
    title: '협업 시스템 구축',
    customer: '가상 고객 D',
    listPriceWon: 10_000_000,
    costWon: 8_499_999,
  }),
]);

export const MINIMUM_MARGIN_BPS = 1_500;
const MARGIN_BELOW_MINIMUM_MESSAGE = '최저 매출총이익률 15%에 미달하여 모의 전송할 수 없습니다.';

export class InvalidInputError extends Error {
  constructor(message) {
    super(message);
    this.name = 'InvalidInputError';
  }
}

function validateDiscount(discountBps) {
  if (!Number.isInteger(discountBps) || discountBps < 0 || discountBps > 10_000) {
    throw new InvalidInputError('할인율은 0%부터 100%까지 0.01% 단위로 입력해 주세요.');
  }
}

function validateMoney(value, label) {
  if (!Number.isSafeInteger(value) || value <= 0 || value > 1_000_000_000) {
    throw new InvalidInputError(`${label}은 1원부터 10억 원까지의 정수여야 합니다.`);
  }
}

export function parseQuoteInput(body) {
  if (
    body === null || typeof body !== 'object' || Array.isArray(body) ||
    Object.getPrototypeOf(body) !== Object.prototype
  ) {
    throw new InvalidInputError('견적 번호와 할인율을 JSON 객체로 보내 주세요.');
  }

  const keys = Reflect.ownKeys(body);
  if (keys.length !== 2 || !keys.includes('quoteId') || !keys.includes('discountBps')) {
    throw new InvalidInputError('quoteId와 discountBps 두 필드만 보내 주세요.');
  }
  if (typeof body.quoteId !== 'string' || body.quoteId.length === 0 || body.quoteId.length > 100) {
    throw new InvalidInputError('올바른 견적 번호를 입력해 주세요.');
  }
  validateDiscount(body.discountBps);
  return { quoteId: body.quoteId, discountBps: body.discountBps };
}

export function calculateQuote(quote, discountBps) {
  if (quote === null || typeof quote !== 'object' || Array.isArray(quote)) {
    throw new InvalidInputError('올바른 견적을 선택해 주세요.');
  }
  for (const key of ['id', 'title', 'customer']) {
    if (typeof quote[key] !== 'string' || quote[key].length === 0) {
      throw new InvalidInputError('견적 번호, 제목, 고객 이름이 필요합니다.');
    }
  }
  validateMoney(quote.listPriceWon, '기준 견적 금액');
  validateMoney(quote.costWon, '원가');
  validateDiscount(discountBps);

  // The input bounds keep this multiplication within Number's safe integer range.
  // Round to the nearest whole won, with an exact half-won rounded upward.
  const netWon = Math.floor((quote.listPriceWon * (10_000 - discountBps) + 5_000) / 10_000);
  if (netWon <= 0) {
    throw new InvalidInputError('최종 견적 금액이 1원 이상이 되도록 할인율을 낮춰 주세요.');
  }
  const profitWon = netWon - quote.costWon;
  return {
    quoteId: quote.id,
    title: quote.title,
    customer: quote.customer,
    listPriceWon: quote.listPriceWon,
    costWon: quote.costWon,
    discountBps,
    netWon,
    profitWon,
    // Display only. The baseline does not apply any business policy to this value.
    marginPercent: profitWon / netWon * 100,
  };
}

export function evaluateSendPolicy(calculation) {
  if (
    calculation === null || typeof calculation !== 'object' || Array.isArray(calculation) ||
    !Number.isSafeInteger(calculation.netWon) || calculation.netWon <= 0 ||
    !Number.isSafeInteger(calculation.profitWon)
  ) {
    throw new InvalidInputError('올바른 견적 계산 결과가 필요합니다.');
  }

  const sendAllowed = calculation.profitWon * 10_000 >= calculation.netWon * MINIMUM_MARGIN_BPS;
  return {
    sendAllowed,
    minimumMarginBps: MINIMUM_MARGIN_BPS,
    blockReason: sendAllowed ? null : MARGIN_BELOW_MINIMUM_MESSAGE,
  };
}
