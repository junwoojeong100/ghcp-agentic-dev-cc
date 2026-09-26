/* Read-only live renderer: browser events never carry operator decisions. */
(async () => {
  const response = await fetch('/status');
  if (!response.ok) throw Error(`Renderer configuration HTTP ${response.status}`);
  const config = await response.json();
  const terminal = new Terminal({
    cols: config.cols, rows: config.rows, fontFamily: 'Menlo, monospace',
    fontSize: 24, lineHeight: 1.1, cursorBlink: false, scrollback: 4000,
    // disableStdin also disables device-query responses. Block DOM input instead.
    disableStdin: false, allowProposedApi: false,
    theme: { background: '#11161d', foreground: '#e3eaf2', cursor: '#e3eaf2' },
  });
  for (const name of ['keydown', 'keyup', 'keypress', 'beforeinput', 'input',
    'paste', 'drop', 'compositionstart', 'compositionupdate', 'compositionend',
    'pointerdown', 'pointerup', 'pointermove', 'mousedown', 'mouseup', 'mousemove',
    'click', 'contextmenu', 'wheel', 'touchstart', 'touchmove', 'touchend']) {
    document.addEventListener(name, event => {
      event.preventDefault();
      event.stopImmediatePropagation();
    }, { capture: true, passive: false });
  }
  terminal.attachCustomKeyEventHandler(() => false);
  terminal.open(document.querySelector('#terminal'));
  terminal.textarea.readOnly = true;
  terminal.textarea.tabIndex = -1;
  terminal.blur();
  const state = { seq: 0, renderedSeq: -1, connected: false, ended: false, error: null };
  let parsing = false;
  if (config.fixture) {
    document.querySelector('#title').textContent = '검증용 FIXTURE · Copilot 실행 아님';
    document.querySelector('#disclosure').textContent = '녹화 도구 검증 전용 · 실제 시연 근거에 사용하지 않음';
  }
  window.captureState = () => {
    const buffer = terminal.buffer.active;
    const lines = [];
    for (let i = 0; i < terminal.rows; i++) {
      lines.push(buffer.getLine(buffer.viewportY + i)?.translateToString(true) ?? '');
    }
    return { ...state, text: lines.join('\n'), cursorX: buffer.cursorX, cursorY: buffer.cursorY,
      bufferType: buffer.type, cols: terminal.cols, rows: terminal.rows,
      syncPending: parsing || terminal.modes.synchronizedOutputMode,
      bracketedPasteMode: terminal.modes.bracketedPasteMode };
  };
  // Parser callbacks do not prove that a new prompt has been painted. Force a
  // full render, then cross two animation frames without any intervening output.
  window.captureSettled = () => new Promise((resolve, reject) => {
    const expectedSeq = state.seq;
    const unstable = () => parsing || terminal.modes.synchronizedOutputMode || state.seq !== expectedSeq;
    if (state.error || unstable()) { reject(Error(state.error ?? 'Terminal synchronized output is not settled')); return; }
    let subscription, frame1, frame2;
    const cleanup = () => {
      clearTimeout(timeout);
      subscription?.dispose();
      cancelAnimationFrame(frame1);
      cancelAnimationFrame(frame2);
    };
    const timeout = setTimeout(() => { cleanup(); reject(Error('Terminal paint acknowledgement timed out')); }, 2000);
    terminal.scrollToBottom();
    subscription = terminal.onRender(({ start, end }) => {
      if (start !== 0 || end !== terminal.rows - 1) return;
      subscription.dispose();
      frame1 = requestAnimationFrame(() => {
        frame2 = requestAnimationFrame(() => {
          cleanup();
          if (state.error || unstable()) { reject(Error(state.error ?? 'Terminal changed during painting')); return; }
          state.renderedSeq = expectedSeq;
          resolve(window.captureState());
        });
      });
    });
    terminal.refresh(0, terminal.rows - 1);
  });
  const fail = async message => {
    if (state.error) return;
    state.error = message;
    document.querySelector('#state').textContent = '촬영 불완전 · 입력 중단';
    document.querySelector('#failure').hidden = false;
    document.querySelector('#failure').textContent = message;
    await window.rendererFailure(message);
  };
  // The host accepts only a narrow response matching an outstanding PTY query.
  // This is a device-protocol channel, not a route for keys, paste or approval.
  terminal.onData(data => {
    Promise.resolve(window.terminalProtocol(data)).catch(error => fail(error.message));
  });
  terminal.onBinary(() => { void fail('Unexpected binary terminal response'); });
  try {
    await document.fonts.ready;
    await window.captureSettled();
    const response = await fetch('/events');
    if (!response.ok) throw Error(`Output stream HTTP ${response.status}`);
    state.connected = true;
    document.querySelector('#state').textContent = '실시간 연결 · 녹화 중';
    await window.rendererReady();
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8', { fatal: true });
    let pending = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) { pending += decoder.decode(); break; }
      pending += decoder.decode(value, { stream: true });
      if (pending.length > 2 * 1024 * 1024) throw Error('Terminal event exceeds buffer limit');
      let end;
      while ((end = pending.indexOf('\n\n')) !== -1) {
        const packet = pending.slice(0, end); pending = pending.slice(end + 2);
        if (!packet.startsWith('data: ')) continue;
        const event = JSON.parse(packet.slice(6));
        if (event.type === 'output') {
          if (state.ended || event.seq !== state.seq + 1) throw Error('Terminal output sequence gap');
          const bytes = Uint8Array.from(atob(event.data), c => c.charCodeAt(0));
          parsing = true;
          state.renderedSeq = -1;
          await new Promise(resolve => terminal.write(bytes, resolve));
          state.seq = event.seq;
          parsing = false;
          // ACK means parsed only. Do not await painting here: a sync-output
          // transaction may finish in a later packet which must keep flowing.
          await window.rendererAck(state.seq);
        } else if (event.type === 'exited') {
          if (event.seq !== state.seq) throw Error('CLI exit did not match final output');
          state.ended = true;
          document.querySelector('#state').textContent = `CLI 종료 · 코드 ${event.exitCode ?? event.signal}`;
        } else if (event.type === 'error') throw Error(event.message);
      }
    }
    state.connected = false;
    if (pending.trim()) throw Error('Incomplete terminal event at stream end');
    if (!state.ended) throw Error('Live stream disconnected; no replay attempted');
  } catch (error) { await fail(error.message); }
})().catch(error => window.rendererFailure(error.message));
