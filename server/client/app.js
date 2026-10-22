const params = new URLSearchParams(window.location.search);
const debug = params.get('debug') === '1';
const textInput = document.getElementById('textInput');
const touchpad = document.getElementById('touchpad');
const mouseTab = document.getElementById('mouseTab');
const keyboardTab = document.getElementById('keyboardTab');
const app = document.querySelector('.app');
const controlPage = document.getElementById('controlPage');
const settingsPage = document.getElementById('settingsPage');
const sensitivity = document.getElementById('sensitivity');
const sensitivityValue = document.getElementById('sensitivityValue');
const scrollSensitivity = document.getElementById('scrollSensitivity');
const scrollSensitivityValue = document.getElementById('scrollSensitivityValue');
const invertScroll = document.getElementById('invertScroll');
const sideScroll = document.getElementById('sideScroll');
const sideScrollEnabled = document.getElementById('sideScrollEnabled');
const touchIndicator = document.getElementById('touchIndicator');
const connectionStatus = document.getElementById('connectionStatus');
const savedSensitivity = Number(localStorage.getItem('pocketmouse-sensitivity') || '1');
const savedScrollSensitivity = Number(localStorage.getItem('pocketmouse-scroll-sensitivity') || '1');
const scrollEnabled = localStorage.getItem('pocketmouse-side-scroll') === 'true';
const scrollInverted = localStorage.getItem('pocketmouse-invert-scroll') === 'true';

const state = {
  socket: null,
  serverAddress: window.location.origin,
  awaitingApproval: false,
  serverRejected: false,
  sensitivity: Number.isFinite(savedSensitivity)
    ? Math.min(3, Math.max(0.5, savedSensitivity))
    : 1,
  scrollSensitivity: Number.isFinite(savedScrollSensitivity)
    ? Math.min(3, Math.max(0.1, savedScrollSensitivity))
    : 1,
  invertScroll: scrollInverted,
  sideScrollEnabled: scrollEnabled,
  disconnectRequested: false,
  pointerMap: new Map(),
  touchPointerId: null,
  longPressTimer: null,
  lastTapAt: 0,
  lastPointerMove: { x: 0, y: 0 },
  isDragging: false,
  dragButton: null,
  lastSendAt: 0,
  moveAccum: { dx: 0, dy: 0 },
  scrollAccum: { dx: 0, dy: 0 },
  mode: 'mouse',
  isComposing: false,
  ignoreCompositionInput: false,
  scrollMotionTimer: null,
  scrollLineOffset: 0,
  viewportHeight: window.visualViewport ? window.visualViewport.height : window.innerHeight,
};

sensitivity.value = String(state.sensitivity);
sensitivityValue.textContent = `${state.sensitivity.toFixed(1)}×`;
scrollSensitivity.value = String(state.scrollSensitivity);
scrollSensitivityValue.textContent = `${state.scrollSensitivity.toFixed(1)}×`;
invertScroll.checked = state.invertScroll;
sideScrollEnabled.checked = state.sideScrollEnabled;
sideScroll.classList.toggle('active', state.sideScrollEnabled);

function log(...values) {
  if (debug) console.debug('[Nudge]', ...values);
}

function setDisconnected(message = '') {
  app.classList.add('disconnected');
  connectionStatus.textContent = message;
  connectionStatus.classList.toggle('hidden', !message);
  log('Disconnected:', message);
}

function sendMessage(payload) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    log('Event ignored: WebSocket is unavailable', payload.t);
    return;
  }
  state.socket.send(JSON.stringify(payload));
  if (payload.t !== 'move') log('Sent:', payload.t);
}

function flushMove() {
  const { dx, dy } = state.moveAccum;
  if (dx || dy) {
    sendMessage({ t: 'move', dx: Math.round(dx), dy: Math.round(dy) });
    state.moveAccum = { dx: 0, dy: 0 };
  }
}

function sendScrollDelta(dx, dy) {
  const direction = state.invertScroll ? -1 : 1;
  state.scrollAccum.dx += dx * state.scrollSensitivity * direction;
  state.scrollAccum.dy += dy * state.scrollSensitivity * direction;
  const wholeDx = Math.trunc(state.scrollAccum.dx);
  const wholeDy = Math.trunc(state.scrollAccum.dy);
  if (wholeDx || wholeDy) {
    sendMessage({ t: 'scroll', dx: wholeDx, dy: wholeDy });
    state.scrollAccum.dx -= wholeDx;
    state.scrollAccum.dy -= wholeDy;
  }
}

function showScrollMotion(deltaY) {
  if (deltaY === 0) return;
  state.scrollLineOffset = (state.scrollLineOffset + deltaY) % 26;
  sideScroll.style.setProperty('--scroll-offset', `${state.scrollLineOffset}px`);
  sideScroll.classList.add('scrolling');
  clearTimeout(state.scrollMotionTimer);
  state.scrollMotionTimer = setTimeout(() => {
    sideScroll.classList.remove('scrolling');
  }, 350);
}

function updateTouchIndicator(clientX, clientY) {
  const rect = touchpad.getBoundingClientRect();
  touchIndicator.style.left = `${clientX - rect.left}px`;
  touchIndicator.style.top = `${clientY - rect.top}px`;
  touchIndicator.classList.add('active');
}

function connectWebSocket(address) {
  let parsedAddress;
  try {
    parsedAddress = new URL(address.includes('://') ? address : `http://${address}`);
    if (!['http:', 'https:'].includes(parsedAddress.protocol)) throw new Error('Invalid protocol');
  } catch {
    setDisconnected('This server address is invalid.');
    return;
  }

  state.serverAddress = parsedAddress.origin;
  state.awaitingApproval = false;
  state.serverRejected = false;
  state.disconnectRequested = false;
  if (state.socket && state.socket.readyState < WebSocket.CLOSING) {
    state.socket.close();
  }
  log('Connecting:', state.serverAddress);
  const wsProtocol = parsedAddress.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${wsProtocol}//${parsedAddress.host}/ws`;
  const socket = new WebSocket(url);
  state.socket = socket;

  socket.addEventListener('open', async () => {
    const userAgent = navigator.userAgent;
    const androidModel = userAgent.match(/Android [^;]+;\s*([^;)]+?)(?:\s+Build\/|[;)])/i);
    const isIPad = /iPad/i.test(userAgent)
      || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const browserBrand = navigator.userAgentData?.brands
      ?.find(({ brand }) => !/Chromium|Not.A.Brand/i.test(brand))?.brand;
    const device = {
      model: navigator.userAgentData?.model
        || androidModel?.[1]
        || (userAgent.includes('iPhone') ? 'iPhone' : isIPad ? 'iPad' : ''),
      browser: browserBrand || (userAgent.includes('Firefox')
        ? 'Firefox'
        : userAgent.includes('Edg/')
          ? 'Edge'
          : userAgent.includes('Chrome')
            ? 'Chrome'
            : userAgent.includes('Safari')
              ? 'Safari'
              : 'Unknown browser'),
      platform: navigator.userAgentData?.platform || navigator.platform || 'Unknown platform',
      device_type: isIPad || /Tablet/i.test(userAgent)
        ? 'Tablet'
        : /Mobi|Android|iPhone/i.test(userAgent) ? 'Mobile device' : 'Desktop device',
      user_agent: userAgent,
    };
    if (navigator.userAgentData) {
      device.device_type = navigator.userAgentData.mobile ? 'Mobile device' : 'Desktop device';
      try {
        const hints = await navigator.userAgentData.getHighEntropyValues(['model', 'platform']);
        device.model = hints.model || '';
        device.platform = hints.platform || device.platform;
      } catch (error) {
        log('Detailed device information is unavailable', error);
      }
    }
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ t: 'hello', device }));
    }
  });

  socket.addEventListener('close', () => {
    if (state.socket !== socket) return;
    if (!state.disconnectRequested && !state.serverRejected) {
      if (state.awaitingApproval) {
        setDisconnected('Connection closed before approval.');
        return;
      }
      setDisconnected('Connection closed.');
    }
  });

  socket.addEventListener('error', () => {
    if (state.socket !== socket) return;
    setDisconnected('Could not connect to the server.');
  });

  socket.addEventListener('message', (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.t === 'ok') {
        state.awaitingApproval = false;
        app.classList.remove('disconnected');
        connectionStatus.classList.add('hidden');
        log('Connected');
      } else if (msg.t === 'approval_pending') {
        state.awaitingApproval = true;
        setDisconnected('Waiting for approval on the Nudge desktop app…');
      } else if (msg.t === 'error') {
        state.serverRejected = true;
        setDisconnected(msg.msg || 'The connection request was denied.');
      }
    } catch (error) {
      console.warn('Invalid server message', error);
    }
  });
}

function setMode(mode) {
  state.mode = mode;
  controlPage.classList.remove('hidden');
  settingsPage.classList.remove('active');
  document.getElementById('menuButton').setAttribute('aria-selected', 'false');
  const keyboardActive = mode === 'keyboard';
  mouseTab.setAttribute('aria-selected', String(!keyboardActive));
  keyboardTab.setAttribute('aria-selected', String(keyboardActive));
  log('Mode:', mode);
  if (keyboardActive) {
    state.viewportHeight = window.visualViewport ? window.visualViewport.height : window.innerHeight;
    textInput.focus({ preventScroll: true });
  } else {
    textInput.blur();
    textInput.value = '';
  }
}

mouseTab.addEventListener('click', () => setMode('mouse'));
keyboardTab.addEventListener('click', () => setMode('keyboard'));

function updateSensitivity() {
  state.sensitivity = Number(sensitivity.value);
  sensitivityValue.textContent = `${state.sensitivity.toFixed(1)}×`;
  localStorage.setItem('pocketmouse-sensitivity', String(state.sensitivity));
}

sensitivity.addEventListener('input', updateSensitivity);

function updateScrollSensitivity() {
  state.scrollSensitivity = Number(scrollSensitivity.value);
  scrollSensitivityValue.textContent = `${state.scrollSensitivity.toFixed(1)}×`;
  state.scrollAccum = { dx: 0, dy: 0 };
  localStorage.setItem('pocketmouse-scroll-sensitivity', String(state.scrollSensitivity));
}

scrollSensitivity.addEventListener('input', updateScrollSensitivity);

invertScroll.addEventListener('change', () => {
  state.invertScroll = invertScroll.checked;
  state.scrollAccum = { dx: 0, dy: 0 };
  localStorage.setItem('pocketmouse-invert-scroll', String(state.invertScroll));
});

sideScrollEnabled.addEventListener('change', () => {
  state.sideScrollEnabled = sideScrollEnabled.checked;
  localStorage.setItem('pocketmouse-side-scroll', String(state.sideScrollEnabled));
  sideScroll.classList.toggle('active', state.sideScrollEnabled);
});

document.getElementById('menuButton').addEventListener('click', (event) => {
  const settingsOpen = settingsPage.classList.contains('active');
  if (settingsOpen) {
    settingsPage.classList.remove('active');
    controlPage.classList.remove('hidden');
    event.currentTarget.setAttribute('aria-selected', 'false');
  } else {
    setMode('mouse');
    controlPage.classList.add('hidden');
    settingsPage.classList.add('active');
    event.currentTarget.setAttribute('aria-selected', 'true');
  }
});

document.getElementById('disconnectButton').addEventListener('click', () => {
  state.disconnectRequested = true;
  if (state.socket && state.socket.readyState < WebSocket.CLOSING) state.socket.close();
  state.socket = null;
  setDisconnected('Disconnected. Scan the QR code to reconnect.');
});

async function pasteFromClipboard() {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      const clip = await navigator.clipboard.readText();
      if (clip) {
        sendMessage({ t: 'text', value: clip });
        log('Clipboard text sent');
        return;
      }
    } catch (error) {
      log('Direct clipboard access is unavailable; using the mobile keyboard clipboard', error);
    }
  }
  setMode('keyboard');
  textInput.focus({ preventScroll: true });
}

document.getElementById('pasteButton').addEventListener('click', pasteFromClipboard);

localStorage.removeItem('pocketmouse-token');
setDisconnected('Connecting to the server…');
const server = window.location.origin;
connectWebSocket(server);

function handlePointerDown(event) {
  touchpad.setPointerCapture(event.pointerId);
  state.pointerMap.set(event.pointerId, {
    x: event.clientX,
    y: event.clientY,
    startX: event.clientX,
    startY: event.clientY,
  });

  if (state.pointerMap.size === 1) {
    state.touchPointerId = event.pointerId;
    updateTouchIndicator(event.clientX, event.clientY);
    state.lastPointerMove = { x: event.clientX, y: event.clientY };
    state.longPressTimer = setTimeout(() => {
      const pointer = state.pointerMap.get(event.pointerId);
      if (pointer && !pointer.moved && state.pointerMap.size === 1) {
        state.isDragging = true;
        state.dragButton = 'left';
        sendMessage({ t: 'down', button: 'left' });
      }
    }, 400);
  }

  if (state.pointerMap.size === 2) {
    clearTimeout(state.longPressTimer);
    state.longPressTimer = null;
    state.isDragging = false;
    state.dragButton = null;
    const points = Array.from(state.pointerMap.values());
    state.lastPointerMove = {
      x: points.reduce((sum, point) => sum + point.x, 0) / points.length,
      y: points.reduce((sum, point) => sum + point.y, 0) / points.length,
    };
  }
}

function handlePointerMove(event) {
  if (!state.pointerMap.has(event.pointerId)) return;
  const previous = state.pointerMap.get(event.pointerId);
  const dx = event.clientX - previous.x;
  const dy = event.clientY - previous.y;
  previous.x = event.clientX;
  previous.y = event.clientY;
  if (state.touchPointerId === event.pointerId) {
    updateTouchIndicator(event.clientX, event.clientY);
  }

  if (state.pointerMap.size === 1) {
    const fromStartX = event.clientX - previous.startX;
    const fromStartY = event.clientY - previous.startY;
    if (!previous.moved && Math.hypot(fromStartX, fromStartY) > 6) {
      previous.moved = true;
      clearTimeout(state.longPressTimer);
      state.longPressTimer = null;
    }
    const acceleration = 1 + Math.min(Math.hypot(dx, dy) / 40, 0.5);
    state.moveAccum.dx += dx * state.sensitivity * acceleration;
    state.moveAccum.dy += dy * state.sensitivity * acceleration;
    if (performance.now() - state.lastSendAt >= 8) {
      flushMove();
      state.lastSendAt = performance.now();
    }
    return;
  }

  if (state.pointerMap.size >= 2) {
    const points = Array.from(state.pointerMap.values());
    const x = points.reduce((sum, point) => sum + point.x, 0) / points.length;
    const y = points.reduce((sum, point) => sum + point.y, 0) / points.length;
    const scrollDeltaY = y - state.lastPointerMove.y;
    showScrollMotion(scrollDeltaY);
    sendScrollDelta((x - state.lastPointerMove.x) / 8, scrollDeltaY / 8);
    state.lastPointerMove = { x, y };
  }
}

sideScroll.addEventListener('pointerdown', (event) => {
  if (!state.sideScrollEnabled) return;
  event.preventDefault();
  event.stopPropagation();
  sideScroll.setPointerCapture(event.pointerId);
  state.sideScrollY = event.clientY;
});

sideScroll.addEventListener('pointermove', (event) => {
  if (!state.sideScrollEnabled || state.sideScrollY === undefined) return;
  event.preventDefault();
  event.stopPropagation();
  const deltaY = event.clientY - state.sideScrollY;
  state.sideScrollY = event.clientY;
  showScrollMotion(deltaY);
  sendScrollDelta(0, deltaY / 12);
});

function endSideScroll(event) {
  if (state.sideScrollY === undefined) return;
  event.preventDefault();
  event.stopPropagation();
  state.sideScrollY = undefined;
}

sideScroll.addEventListener('pointerup', endSideScroll);
sideScroll.addEventListener('pointercancel', endSideScroll);

function handlePointerUp(event) {
  const pointerCount = state.pointerMap.size;
  const hadLongPress = state.isDragging && state.dragButton === 'left';

  if (pointerCount === 1 && !hadLongPress && state.longPressTimer) {
    clearTimeout(state.longPressTimer);
    state.longPressTimer = null;
    const now = Date.now();
    if (now - state.lastTapAt < 260) sendMessage({ t: 'dblclick' });
    else sendMessage({ t: 'click', button: 'left' });
    state.lastTapAt = now;
  } else if (pointerCount === 2) {
    const otherPointer = Array.from(state.pointerMap.entries())
      .find(([pointerId]) => pointerId !== event.pointerId);
    if (otherPointer
        && Math.abs(otherPointer[1].x - event.clientX) < 12
        && Math.abs(otherPointer[1].y - event.clientY) < 12) {
      sendMessage({ t: 'click', button: 'right' });
    }
  }

  state.pointerMap.delete(event.pointerId);
  if (hadLongPress) sendMessage({ t: 'up', button: 'left' });

  if (state.pointerMap.size === 0) {
    clearTimeout(state.longPressTimer);
    state.longPressTimer = null;
    state.isDragging = false;
    state.dragButton = null;
    state.lastPointerMove = { x: 0, y: 0 };
    state.touchPointerId = null;
    touchIndicator.classList.remove('active');
    flushMove();
  } else if (state.touchPointerId === event.pointerId) {
    const [nextPointerId, nextPointer] = state.pointerMap.entries().next().value;
    state.touchPointerId = nextPointerId;
    updateTouchIndicator(nextPointer.x, nextPointer.y);
  }
}

touchpad.addEventListener('pointerdown', handlePointerDown);
touchpad.addEventListener('pointermove', handlePointerMove);
touchpad.addEventListener('pointerup', handlePointerUp);
touchpad.addEventListener('pointercancel', handlePointerUp);
touchpad.addEventListener('pointerleave', (event) => {
  if (state.pointerMap.has(event.pointerId)) handlePointerUp(event);
});

document.getElementById('leftButton').addEventListener('click', () => sendMessage({ t: 'click', button: 'left' }));
document.getElementById('middleButton').addEventListener('click', () => sendMessage({ t: 'click', button: 'middle' }));
document.getElementById('rightButton').addEventListener('click', () => sendMessage({ t: 'click', button: 'right' }));

document.querySelectorAll('[data-key]').forEach((button) => {
  button.addEventListener('pointerdown', (event) => event.preventDefault());
  button.addEventListener('click', () => sendMessage({ t: 'key', key: button.dataset.key }));
});

textInput.addEventListener('keydown', (event) => {
  if (state.isComposing) return;
  const keyMap = {
    Backspace: 'backspace',
    Delete: 'delete',
    Enter: 'enter',
    Tab: 'tab',
    Escape: 'esc',
  };
  const key = keyMap[event.key];
  if (!key) return;
  event.preventDefault();
  sendMessage({ t: 'key', key });
});

textInput.addEventListener('compositionstart', () => {
  state.isComposing = true;
});

textInput.addEventListener('compositionend', (event) => {
  state.isComposing = false;
  const committedText = event.data || textInput.value;
  if (committedText) sendMessage({ t: 'text', value: committedText });
  textInput.value = '';
  state.ignoreCompositionInput = true;
  setTimeout(() => {
    state.ignoreCompositionInput = false;
  }, 0);
});

textInput.addEventListener('input', (event) => {
  if (state.isComposing || event.isComposing) return;
  if (state.ignoreCompositionInput) {
    state.ignoreCompositionInput = false;
    textInput.value = '';
    return;
  }

  const typedText = textInput.value;
  if (event.inputType && event.inputType.startsWith('delete')) {
    const key = event.inputType.includes('Forward') ? 'delete' : 'backspace';
    sendMessage({ t: 'key', key });
  } else if (typedText) {
    sendMessage({ t: 'text', value: typedText });
  }
  textInput.value = '';
  log('Entrada de teclado enviada');
});

textInput.addEventListener('blur', () => {
  if (state.mode === 'keyboard') setMode('mouse');
});

function handleViewportResize() {
  if (state.mode !== 'keyboard' || !window.visualViewport) return;
  const height = window.visualViewport.height;
  if (height > state.viewportHeight * 0.82) setMode('mouse');
}

if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', handleViewportResize);
}
