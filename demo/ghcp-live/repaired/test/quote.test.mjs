import test from 'node:test';
import assert from 'node:assert/strict';
import { QUOTES, calculateQuote, parseQuoteInput, InvalidInputError } from '../src/quote.mjs';

const normal = QUOTES[0];

test('the prepared fixtures contain only named synthetic customers', () => {
  assert.deepEqual(QUOTES.map(({ id, listPriceWon, costWon }) => ({ id, listPriceWon, costWon })), [
    { id: 'Q-1001', listPriceWon: 10_000_000, costWon: 8_000_000 },
    { id: 'Q-1500', listPriceWon: 10_000_000, costWon: 8_500_000 },
    { id: 'Q-1499', listPriceWon: 10_000_000, costWon: 8_500_001 },
    { id: 'Q-1501', listPriceWon: 10_000_000, costWon: 8_499_999 },
  ]);
  assert.equal(normal.title, '업무 시스템 구축');
  assert.equal(normal.customer, '가상 고객 A');
  assert.ok(QUOTES.every((quote) => quote.customer.startsWith('가상 고객 ')));
});

test('calculates the complete undiscounted quotation without changing its source', () => {
  const source = { ...normal };
  assert.deepEqual(calculateQuote(source, 0), {
    quoteId: 'Q-1001', title: '업무 시스템 구축', customer: '가상 고객 A',
    listPriceWon: 10_000_000, costWon: 8_000_000, discountBps: 0,
    netWon: 10_000_000, profitWon: 2_000_000, marginPercent: 20,
  });
  assert.deepEqual(source, normal);
});

test('calculates the editable discount and descriptive margin', () => {
  const result = calculateQuote(normal, 1000);
  assert.equal(result.netWon, 9_000_000);
  assert.equal(result.profitWon, 1_000_000);
  assert.ok(Math.abs(result.marginPercent - 100 / 9) < 1e-10);
});

test('rounds exact half-won values upward and values below a half downward', () => {
  const quote = { ...normal, listPriceWon: 101, costWon: 40 };
  assert.equal(calculateQuote(quote, 5000).netWon, 51);
  assert.equal(calculateQuote(quote, 5001).netWon, 50);
  assert.equal(calculateQuote(quote, 4999).netWon, 51);
});

test('supports the documented upper money bound and negative calculated profit', () => {
  const result = calculateQuote({ ...normal, listPriceWon: 1_000_000_000, costWon: 1_000_000_000 }, 1);
  assert.equal(result.netWon, 999_900_000);
  assert.equal(result.profitWon, -100_000);
  assert.ok(result.marginPercent < 0);
});

test('rejects a zero net amount including values rounded down to zero', () => {
  assert.throws(() => calculateQuote(normal, 10_000), InvalidInputError);
  assert.throws(() => calculateQuote({ ...normal, listPriceWon: 1, costWon: 1 }, 6000), InvalidInputError);
  assert.equal(calculateQuote({ ...normal, listPriceWon: 1, costWon: 1 }, 5000).netWon, 1);
});

test('requires positive bounded safe integer money for both amounts', () => {
  const badValues = [0, -1, 0.5, NaN, Infinity, 1_000_000_001, Number.MAX_SAFE_INTEGER + 1, '100', null, undefined];
  for (const key of ['listPriceWon', 'costWon']) {
    for (const value of badValues) {
      assert.throws(() => calculateQuote({ ...normal, [key]: value }, 0), InvalidInputError);
    }
  }
});

test('rejects invalid discount types and ranges in both input and calculation', () => {
  for (const value of [-1, 10_001, 0.1, '1000', NaN, Infinity, null, undefined, true]) {
    assert.throws(() => calculateQuote(normal, value), InvalidInputError);
    assert.throws(() => parseQuoteInput({ quoteId: normal.id, discountBps: value }), InvalidInputError);
  }
});

test('parses only the exact two-field request schema and copies the input', () => {
  const body = { quoteId: normal.id, discountBps: 1000 };
  assert.deepEqual(parseQuoteInput(body), body);
  assert.notEqual(parseQuoteInput(body), body);
  assert.equal(parseQuoteInput({ quoteId: normal.id, discountBps: 10_000 }).discountBps, 10_000);
  for (const invalid of [
    null, [], 'text', 7, true, new Date(), {}, { quoteId: normal.id }, { discountBps: 0 },
    { ...body, costWon: 1 }, { ...body, extra: undefined },
    { ...body, [Symbol('extra')]: true },
    { quoteId: '', discountBps: 0 }, { quoteId: 1001, discountBps: 0 },
    { quoteId: null, discountBps: 0 }, { quoteId: 'x'.repeat(101), discountBps: 0 },
  ]) {
    assert.throws(() => parseQuoteInput(invalid), InvalidInputError);
  }
});

test('requires an actual quote with string identity fields', () => {
  for (const quote of [null, [], 1, {}, { ...normal, id: '' }, { ...normal, title: null }, { ...normal, customer: 1 }]) {
    assert.throws(() => calculateQuote(quote, 0), InvalidInputError);
  }
});
