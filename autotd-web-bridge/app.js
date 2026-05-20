const state = {
  socket: null,
  sessionId: null,
  serverStatus: "대기 중",
  lastEvent: "없음",
};

const STORAGE_KEY = "autotd-web-bridge.socket-url";
const DEFAULT_URL = "ws://127.0.0.1:9980/autotd";

const elements = {
  form: document.getElementById("generateForm"),
  promptInput: document.getElementById("promptInput"),
  moodSelect: document.getElementById("moodSelect"),
  motionSpeedSelect: document.getElementById("motionSpeedSelect"),
  effectDensitySelect: document.getElementById("effectDensitySelect"),
  durationInput: document.getElementById("durationInput"),
  templateHintInput: document.getElementById("templateHintInput"),
  socketUrlInput: document.getElementById("socketUrlInput"),
  connectButton: document.getElementById("connectButton"),
  disconnectButton: document.getElementById("disconnectButton"),
  pingButton: document.getElementById("pingButton"),
  generateButton: document.getElementById("generateButton"),
  connectionBadge: document.getElementById("connectionBadge"),
  sessionValue: document.getElementById("sessionValue"),
  tdStatusValue: document.getElementById("tdStatusValue"),
  lastEventValue: document.getElementById("lastEventValue"),
  deployNotice: document.getElementById("deployNotice"),
  mappingOutput: document.getElementById("mappingOutput"),
  previewSurface: document.getElementById("previewSurface"),
  logList: document.getElementById("logList"),
  clearLogButton: document.getElementById("clearLogButton"),
};

function nowLabel() {
  return new Date().toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function setConnectionBadge(kind, label) {
  elements.connectionBadge.textContent = label;
  elements.connectionBadge.className = `status-badge ${kind}`;
}

function updateStatusSummary() {
  elements.sessionValue.textContent = state.sessionId || "미연결";
  elements.tdStatusValue.textContent = state.serverStatus;
  elements.lastEventValue.textContent = state.lastEvent;
}

function logEvent(kind, text) {
  const item = document.createElement("li");
  item.className = `log-item ${kind}`;

  const timestamp = document.createElement("time");
  timestamp.dateTime = new Date().toISOString();
  timestamp.textContent = nowLabel();

  const body = document.createElement("p");
  body.textContent = text;

  item.append(timestamp, body);
  elements.logList.prepend(item);
}

function renderMapping(data) {
  elements.mappingOutput.textContent = JSON.stringify(data, null, 2);
}

function renderPreview(payload) {
  elements.previewSurface.innerHTML = "";

  if (payload.videoUrl) {
    const video = document.createElement("video");
    video.className = "preview-media";
    video.src = payload.videoUrl;
    video.controls = true;
    video.autoplay = true;
    video.loop = true;
    video.muted = true;
    elements.previewSurface.append(video);
    return;
  }

  if (payload.imageUrl || payload.previewUrl) {
    const image = document.createElement("img");
    image.className = "preview-media";
    image.src = payload.imageUrl || payload.previewUrl;
    image.alt = "TouchDesigner preview";
    elements.previewSurface.append(image);
    return;
  }

  const text = document.createElement("div");
  text.className = "preview-text";
  text.textContent = payload.previewText || "미리보기가 아직 전달되지 않았습니다.";
  elements.previewSurface.append(text);
}

function setInteractiveState(connected) {
  elements.connectButton.disabled = connected;
  elements.disconnectButton.disabled = !connected;
  elements.pingButton.disabled = !connected;
  elements.generateButton.disabled = !connected;
}

function safeParseJson(text) {
  try {
    return JSON.parse(text);
  } catch (error) {
    return null;
  }
}

function rememberSocketUrl() {
  const value = elements.socketUrlInput.value.trim() || DEFAULT_URL;
  localStorage.setItem(STORAGE_KEY, value);
}

function isHostedDemo() {
  const host = window.location.hostname;
  return host.endsWith("github.io") || host !== "127.0.0.1" && host !== "localhost" && window.location.protocol === "https:";
}

function sendJson(payload) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    logEvent("is-error", "메시지를 보낼 수 없습니다. TouchDesigner 연결이 열려 있지 않습니다.");
    return false;
  }

  const text = JSON.stringify(payload);
  state.socket.send(text);
  state.lastEvent = payload.type;
  updateStatusSummary();
  logEvent("is-send", `${payload.type} 전송`);
  return true;
}

function handleIncomingMessage(rawText) {
  const payload = safeParseJson(rawText);
  if (!payload) {
    logEvent("is-recv", `텍스트 수신: ${rawText}`);
    return;
  }

  state.lastEvent = payload.type || "message";

  if (payload.type === "hello_ack") {
    state.sessionId = payload.sessionId || "connected";
    state.serverStatus = payload.statusLabel || "연결됨";
    setConnectionBadge("is-online", "online");
  } else if (payload.type === "status") {
    state.serverStatus = payload.stateLabel || payload.state || "업데이트";

    if (payload.level === "busy") {
      setConnectionBadge("is-busy", "busy");
    } else if (state.socket?.readyState === WebSocket.OPEN) {
      setConnectionBadge("is-online", "online");
    }
  } else if (payload.type === "mapping") {
    renderMapping(payload.parameters || payload);
  } else if (payload.type === "preview") {
    renderPreview(payload);
  } else if (payload.type === "error") {
    state.serverStatus = payload.message || "오류";
    setConnectionBadge("is-offline", "error");
  }

  updateStatusSummary();
  logEvent("is-recv", `${payload.type || "message"} 수신`);
}

function closeSocket() {
  const socket = state.socket;
  state.socket = null;

  if (socket) {
    socket.close();
  }
}

function connectSocket() {
  const url = elements.socketUrlInput.value.trim() || DEFAULT_URL;
  rememberSocketUrl();

  if (window.location.protocol === "https:" && url.startsWith("ws://")) {
    logEvent("is-error", "HTTPS 배포본에서는 ws:// localhost 연결이 브라우저에서 차단될 수 있습니다. 로컬 실행본을 사용하거나 별도 보안 프록시가 필요합니다.");
  }

  closeSocket();

  setConnectionBadge("is-busy", "connecting");
  state.serverStatus = "연결 시도 중";
  updateStatusSummary();

  const socket = new WebSocket(url);
  state.socket = socket;

  socket.addEventListener("open", () => {
    if (state.socket !== socket) {
      socket.close();
      return;
    }

    setConnectionBadge("is-online", "online");
    state.serverStatus = "연결됨";
    updateStatusSummary();
    setInteractiveState(true);
    logEvent("is-send", "WebSocket 연결 완료");
    sendJson({
      type: "hello",
      client: "autotd-web-bridge",
      version: 1,
      requestedAt: new Date().toISOString(),
    });
  });

  socket.addEventListener("message", (event) => {
    if (state.socket !== socket) {
      return;
    }

    handleIncomingMessage(event.data);
  });

  socket.addEventListener("close", () => {
    if (state.socket !== socket) {
      return;
    }

    state.socket = null;
    state.sessionId = null;
    state.serverStatus = "연결 종료";
    state.lastEvent = "socket-close";
    setConnectionBadge("is-offline", "offline");
    setInteractiveState(false);
    updateStatusSummary();
    logEvent("is-error", "WebSocket 연결이 종료되었습니다.");
  });

  socket.addEventListener("error", () => {
    if (state.socket !== socket) {
      return;
    }

    state.serverStatus = "연결 오류";
    state.lastEvent = "socket-error";
    setConnectionBadge("is-offline", "error");
    updateStatusSummary();
    logEvent("is-error", "TouchDesigner WebSocket 연결에 실패했습니다.");
  });
}

function buildGeneratePayload() {
  return {
    type: "generate",
    prompt: elements.promptInput.value.trim(),
    mood: elements.moodSelect.value,
    motionSpeed: elements.motionSpeedSelect.value,
    effectDensity: elements.effectDensitySelect.value,
    duration: Number(elements.durationInput.value),
    templateHint: elements.templateHintInput.value.trim(),
    requestedAt: new Date().toISOString(),
  };
}

function handleSubmit(event) {
  event.preventDefault();

  const payload = buildGeneratePayload();
  if (!payload.prompt) {
    elements.promptInput.focus();
    return;
  }

  sendJson(payload);
}

function bootstrap() {
  const storedUrl = localStorage.getItem(STORAGE_KEY);
  elements.socketUrlInput.value = storedUrl || DEFAULT_URL;
  setInteractiveState(false);
  updateStatusSummary();

  if (isHostedDemo()) {
    elements.deployNotice.hidden = false;
    logEvent("is-recv", "배포본 감지: 이 페이지는 외부 공유용 정적 UI입니다.");
  }

  renderPreview({
    previewText: "TouchDesigner가 연결되면 미리보기 요약 또는 이미지/비디오 URL을 여기에 표시합니다.",
  });

  elements.connectButton.addEventListener("click", connectSocket);
  elements.disconnectButton.addEventListener("click", closeSocket);
  elements.pingButton.addEventListener("click", () => {
    sendJson({
      type: "ping",
      requestedAt: new Date().toISOString(),
    });
  });
  elements.form.addEventListener("submit", handleSubmit);
  elements.clearLogButton.addEventListener("click", () => {
    elements.logList.innerHTML = "";
  });

  logEvent("is-recv", "브리지 준비 완료");
}

bootstrap();
