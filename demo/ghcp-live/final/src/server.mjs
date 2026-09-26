import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { QUOTES, calculateQuote, getSendEligibility, parseQuoteInput, InvalidInputError } from './quote.mjs';

const MAX_BODY_BYTES = 16 * 1024;
const STATIC_FILES = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/index.html', ['index.html', 'text/html; charset=utf-8']],
  ['/app.js', ['app.js', 'text/javascript; charset=utf-8']],
  ['/style.css', ['style.css', 'text/css; charset=utf-8']],
]);

function sendJson(response, status, body) {
  if (response.destroyed || response.writableEnded) return;
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  response.end(JSON.stringify(body));
}

function readJson(request) {
  return new Promise((resolveBody, reject) => {
    let settled = false;
    let bytes = 0;
    const chunks = [];
    const fail = (message, closeConnection = false) => {
      if (settled) return;
      settled = true;
      chunks.length = 0;
      const error = new InvalidInputError(message);
      error.closeConnection = closeConnection;
      reject(error);
    };

    request.on('data', (chunk) => {
      if (settled) return;
      bytes += chunk.length;
      if (bytes > MAX_BODY_BYTES) {
        fail('요청은 16KB 이하여야 합니다.', true);
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => {
      if (settled) return;
      try {
        // Fatal decoding also rejects invalid UTF-8 instead of silently replacing it.
        const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
        const body = JSON.parse(text);
        settled = true;
        resolveBody(body);
      } catch {
        fail('유효한 JSON 본문을 보내 주세요.');
      }
      chunks.length = 0;
    });
    // Keep these handlers through connection teardown, including oversized uploads.
    request.on('aborted', () => fail('요청이 완료되기 전에 연결이 종료되었습니다.'));
    request.on('error', () => fail('요청 본문을 읽을 수 없습니다.'));
  });
}

export function createAppServer({ quotes = QUOTES } = {}) {
  return createServer({ requestTimeout: 10_000, headersTimeout: 10_000 }, async (request, response) => {
    try {
      const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
      if (request.method === 'GET' && pathname === '/api/quotes') {
        sendJson(response, 200, { quotes });
        return;
      }

      if (request.method === 'POST' && (pathname === '/api/preview' || pathname === '/api/send')) {
        const { quoteId, discountBps } = parseQuoteInput(await readJson(request));
        const quote = quotes.find((item) => item.id === quoteId);
        if (!quote) {
          sendJson(response, 404, { error: { code: 'QUOTE_NOT_FOUND', message: '선택한 견적을 찾을 수 없습니다.' } });
          return;
        }
        // Always calculate from the server's quote data; never trust client amounts.
        const calculation = calculateQuote(quote, discountBps);
        const evaluatedQuote = { ...calculation, eligibility: getSendEligibility(calculation) };
        if (pathname === '/api/send' && !evaluatedQuote.eligibility.canSend) {
          sendJson(response, 422, {
            error: { code: 'MARGIN_BELOW_MINIMUM', message: evaluatedQuote.eligibility.reason },
            quote: evaluatedQuote,
          });
          return;
        }
        sendJson(response, 200, pathname === '/api/preview' ? evaluatedQuote : {
          sent: true,
          simulation: true,
          message: '모의 전송 완료 — 외부 발송 없음',
          quote: evaluatedQuote,
        });
        return;
      }

      const staticFile = request.method === 'GET' ? STATIC_FILES.get(pathname) : undefined;
      if (staticFile) {
        const [name, type] = staticFile;
        const contents = await readFile(new URL(`../public/${name}`, import.meta.url));
        response.writeHead(200, {
          'Content-Type': type,
          'Cache-Control': 'no-store',
          'X-Content-Type-Options': 'nosniff',
          'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
          'Referrer-Policy': 'no-referrer',
        });
        response.end(contents);
        return;
      }

      request.resume();
      sendJson(response, 404, { error: { code: 'NOT_FOUND', message: '요청한 경로를 찾을 수 없습니다.' } });
    } catch (error) {
      if (error instanceof InvalidInputError) {
        if (error.closeConnection && !response.headersSent && !response.destroyed) {
          response.setHeader('Connection', 'close');
        }
        sendJson(response, 400, { error: { code: 'INVALID_INPUT', message: error.message } });
        return;
      }
      sendJson(response, 500, { error: { code: 'INTERNAL_ERROR', message: '요청을 처리하지 못했습니다. 다시 시도해 주세요.' } });
    }
  });
}

// Importing this module creates no listener; tests choose ephemeral loopback ports.
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const rawPort = process.env.PORT ?? '4310';
  if (!/^\d+$/.test(rawPort) || Number(rawPort) < 1 || Number(rawPort) > 65535) {
    console.error('PORT는 1부터 65535까지의 정수여야 합니다.');
    process.exitCode = 1;
  } else {
    const server = createAppServer();
    server.on('error', (error) => {
      console.error(`서버를 시작하지 못했습니다: ${error.message}`);
      process.exitCode = 1;
    });
    server.listen(Number(rawPort), '127.0.0.1', () => {
      console.log(`합성 견적 데모: http://127.0.0.1:${server.address().port}`);
      console.log('합성 데이터만 사용하며 외부로 견적을 발송하지 않습니다.');
    });
    const shutdown = () => {
      server.close();
      server.closeAllConnections();
    };
    process.once('SIGINT', shutdown);
    process.once('SIGTERM', shutdown);
  }
}
