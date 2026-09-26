const form = document.querySelector('#quote-form');
const quoteSelect = document.querySelector('#quote-id');
const discountInput = document.querySelector('#discount');
const sendButton = document.querySelector('#send-quote');
const feedback = document.querySelector('#feedback');
const retryButton = document.querySelector('#retry-load');
const status = document.querySelector('#quote-status');
const summary = document.querySelector('#quote-summary');
const previewLabel = document.querySelector('#preview-label');
const money = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 2 });

let quotes = [];
let revision = 0;
let activeController;
let previewInput = null;
let preview = null;
let sending = false;

function formatMoney(value) {
  return `${money.format(value)}원`;
}

function setFeedback(message = '', tone = '') {
  feedback.textContent = message;
  feedback.dataset.tone = tone;
}

function beginRequest() {
  revision += 1;
  activeController?.abort();
  activeController = new AbortController();
  return { token: revision, controller: activeController };
}

async function requestJson(path, controller, body) {
  let responseStatus;
  const timeout = setTimeout(() => controller.abort(), 10_000);
  try {
    const response = await fetch(path, {
      method: body ? 'POST' : 'GET',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      cache: 'no-store',
      signal: controller.signal,
    });
    responseStatus = response.status;
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result?.error?.message ?? '요청을 처리하지 못했습니다. 다시 시도해 주세요.');
      error.status = response.status;
      error.code = result?.error?.code;
      throw error;
    }
    return result;
  } catch (error) {
    let failure = error;
    if (error.name === 'AbortError') {
      failure = new Error('응답을 받지 못했습니다. 입력값을 확인하고 다시 시도해 주세요.');
    } else if (error instanceof TypeError || error instanceof SyntaxError) {
      failure = new Error('서버에 연결할 수 없습니다. 서버 실행 상태를 확인해 주세요.');
    }
    // Headers remain authoritative even if reading the response body fails.
    if (responseStatus !== undefined) failure.status = responseStatus;
    throw failure;
  } finally {
    clearTimeout(timeout);
  }
}

function readInput() {
  const raw = discountInput.value;
  if (!discountInput.validity.valid || !/^\d+(?:\.\d{1,2})?$/.test(raw)) {
    throw new Error('할인율은 0%부터 100%까지 소수 둘째 자리 이내로 입력해 주세요.');
  }
  // Convert a decimal percentage to integer hundredths without floating-point drift.
  const [whole, fraction = ''] = raw.split('.');
  const discountBps = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isInteger(discountBps) || discountBps < 0 || discountBps > 10_000) {
    throw new Error('할인율은 0%부터 100%까지 입력해 주세요.');
  }
  if (!quotes.some((quote) => quote.id === quoteSelect.value)) {
    throw new Error('견적을 선택해 주세요.');
  }
  return { quoteId: quoteSelect.value, discountBps };
}

function updateIdentity() {
  const quote = quotes.find((item) => item.id === quoteSelect.value);
  if (!quote) return;
  document.querySelector('#quote-reference').textContent = quote.id;
  document.querySelector('#quote-title').textContent = quote.title;
  document.querySelector('#quote-customer').textContent = `${quote.customer} · 합성 고객`;
  document.querySelector('#list-amount').textContent = formatMoney(quote.listPriceWon);
  document.querySelector('#cost-amount').textContent = formatMoney(quote.costWon);
}

function displayCalculation(calculation) {
  document.querySelector('#net-amount').textContent = calculation ? formatMoney(calculation.netWon) : '—';
  document.querySelector('#profit-amount').textContent = calculation ? formatMoney(calculation.profitWon) : '—';
  document.querySelector('#margin-value').textContent = calculation ? `${percent.format(calculation.marginPercent)}%` : '—';
}

function getSendEligibility(calculation, input) {
  const eligibility = calculation?.sendEligibility;
  if (
    calculation?.quoteId !== input.quoteId || calculation?.discountBps !== input.discountBps ||
    !Number.isSafeInteger(calculation.netWon) || calculation.netWon <= 0 ||
    !Number.isSafeInteger(calculation.profitWon) || !Number.isFinite(calculation.marginPercent) ||
    typeof eligibility?.allowed !== 'boolean' ||
    !Number.isFinite(eligibility.minimumMarginPercent) ||
    eligibility.minimumMarginPercent < 0 || eligibility.minimumMarginPercent > 100 ||
    (eligibility.allowed ? eligibility.reason !== null : (
      eligibility.reason?.code !== 'MARGIN_BELOW_MINIMUM' ||
      typeof eligibility.reason?.message !== 'string' || eligibility.reason.message.trim().length === 0
    ))
  ) {
    throw new Error('서버의 견적 판정 결과를 확인할 수 없습니다. 입력값을 변경해 다시 확인해 주세요.');
  }
  return eligibility;
}

async function updatePreview() {
  const { token, controller } = beginRequest();
  preview = null;
  previewInput = null;
  sending = false;
  sendButton.disabled = true;
  sendButton.firstElementChild.textContent = '견적 보내기';
  setFeedback();
  discountInput.removeAttribute('aria-invalid');
  displayCalculation(null);
  updateIdentity();
  summary.setAttribute('aria-busy', 'true');
  previewLabel.textContent = '계산 중';
  status.textContent = '입력한 할인율로 금액을 계산하고 있습니다.';

  try {
    const input = readInput();
    const calculation = await requestJson('/api/preview', controller, input);
    if (token !== revision) return;
    const eligibility = getSendEligibility(calculation, input);
    preview = calculation;
    previewInput = input;
    displayCalculation(calculation);
    previewLabel.textContent = eligibility.allowed ? '현재 입력 기준' : '전송 차단';
    status.textContent = eligibility.allowed
      ? `매출총이익률 ${eligibility.minimumMarginPercent}% 기준을 충족했습니다. 금액을 확인한 뒤 모의 전송하세요.`
      : '매출총이익률 기준 미달로 모의 전송이 차단되었습니다.';
    if (!eligibility.allowed) setFeedback(eligibility.reason.message, 'error');
    sendButton.disabled = !eligibility.allowed;
  } catch (error) {
    if (token !== revision) return;
    previewLabel.textContent = '확인 필요';
    status.textContent = '견적 계산을 완료한 뒤 전송할 수 있습니다.';
    setFeedback(error.message, 'error');
    // Validation errors affect the field; network failures do not make it invalid.
    try {
      readInput();
    } catch {
      discountInput.setAttribute('aria-invalid', 'true');
    }
  } finally {
    if (token === revision) summary.setAttribute('aria-busy', 'false');
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (sending || preview?.sendEligibility?.allowed !== true || sendButton.disabled) return;
  let input;
  try {
    input = readInput();
  } catch {
    updatePreview();
    return;
  }
  if (input.quoteId !== previewInput?.quoteId || input.discountBps !== previewInput?.discountBps) {
    updatePreview();
    return;
  }

  const { token, controller } = beginRequest();
  sending = true;
  sendButton.disabled = true;
  sendButton.firstElementChild.textContent = '모의 전송 중';
  summary.setAttribute('aria-busy', 'true');
  status.textContent = '현재 견적으로 모의 전송을 실행하고 있습니다.';
  setFeedback();
  try {
    const result = await requestJson('/api/send', controller, input);
    if (token !== revision) return;
    if (
      result?.sent !== true || result.simulation !== true ||
      typeof result.message !== 'string' || result.message.trim().length === 0
    ) {
      throw new Error('모의 전송 결과를 확인할 수 없습니다. 다시 시도해 주세요.');
    }
    if (!getSendEligibility(result.quote, input).allowed) {
      throw new Error('서버의 전송 판정과 결과가 일치하지 않습니다. 입력값을 변경해 다시 확인해 주세요.');
    }
    preview = result.quote;
    previewInput = input;
    displayCalculation(result.quote);
    status.textContent = '현재 입력값으로 모의 전송을 완료했습니다.';
    setFeedback(result.message, 'success');
  } catch (error) {
    if (token !== revision) return;
    if (error.status === 422) {
      preview = null;
      previewInput = null;
      displayCalculation(null);
      previewLabel.textContent = error.code === 'MARGIN_BELOW_MINIMUM' ? '전송 차단' : '확인 필요';
    }
    status.textContent = '모의 전송을 완료하지 못했습니다.';
    setFeedback(error.message, 'error');
  } finally {
    if (token === revision) {
      sending = false;
      sendButton.disabled = preview?.sendEligibility?.allowed !== true;
      sendButton.firstElementChild.textContent = '견적 보내기';
      summary.setAttribute('aria-busy', 'false');
    }
  }
});

async function loadQuotes() {
  const { token, controller } = beginRequest();
  preview = null;
  previewInput = null;
  quoteSelect.disabled = true;
  discountInput.disabled = true;
  sendButton.disabled = true;
  retryButton.hidden = true;
  summary.setAttribute('aria-busy', 'true');
  setFeedback();
  status.textContent = '견적 정보를 불러오고 있습니다.';
  try {
    const result = await requestJson('/api/quotes', controller);
    if (token !== revision) return;
    if (!Array.isArray(result.quotes) || result.quotes.length === 0) {
      throw new Error('사용할 수 있는 견적이 없습니다. 데모 데이터를 확인해 주세요.');
    }
    quotes = result.quotes;
    quoteSelect.replaceChildren(...quotes.map((quote) => {
      const option = document.createElement('option');
      option.value = quote.id;
      option.textContent = `${quote.id} · ${quote.title}`;
      return option;
    }));
    quoteSelect.value = quotes.some((quote) => quote.id === 'Q-1001') ? 'Q-1001' : quotes[0].id;
    quoteSelect.disabled = false;
    discountInput.disabled = false;
    updatePreview();
  } catch (error) {
    if (token !== revision) return;
    summary.setAttribute('aria-busy', 'false');
    previewLabel.textContent = '연결 확인';
    status.textContent = '견적 정보를 불러오지 못했습니다.';
    setFeedback(error.message, 'error');
    retryButton.hidden = false;
  }
}

quoteSelect.addEventListener('change', updatePreview);
discountInput.addEventListener('input', updatePreview);
retryButton.addEventListener('click', loadQuotes);
loadQuotes();
