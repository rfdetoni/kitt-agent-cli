const state = {
  csrf: "",
  sessions: [],
  sessionId: "",
  sessionTitle: "",
  source: null,
  lastSequence: 0,
  events: [],
  approvals: new Map(),
  tools: [],
  children: new Map(),
  activeTurnId: "",
  streamMessage: null,
  inspectorTab: "events",
  status: {},
  extensions: {},
  artifacts: [],
  diff: {loaded: false, available: false, content: ""},
  messagesNextBefore: "",
  messagesHasMore: false,
};

const $ = (id) => document.getElementById(id);
const els = {
  pairingOverlay: $("pairingOverlay"), pairingForm: $("pairingForm"), pairingCode: $("pairingCode"), pairingError: $("pairingError"),
  sessions: $("sessions"), newSessionButton: $("newSessionButton"), conversation: $("conversation"), composer: $("composer"),
  promptInput: $("promptInput"), sendButton: $("sendButton"), cancelButton: $("cancelButton"), sessionName: $("sessionName"), modelName: $("modelName"),
  contextValue: $("contextValue"), connectionDot: $("connectionDot"), connectionText: $("connectionText"), workspacePath: $("workspacePath"),
  daemonState: $("daemonState"), children: $("children"), activity: $("activity"), inspectorContent: $("inspectorContent"),
  logoutButton: $("logoutButton"), navToggle: $("navToggle"), inspectorToggle: $("inspectorToggle"), sidebar: $("sidebar"),
  inspector: document.querySelector(".inspector"), toast: $("toast"),
};

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = String(text);
  return el;
}

function formatTime(value) {
  const d = new Date((Number(value) || Date.now() / 1000) * 1000);
  return d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
}

function toast(message, kind = "") {
  els.toast.textContent = message;
  els.toast.className = `toast ${kind}`.trim();
  setTimeout(() => els.toast.classList.add("hidden"), 3200);
}

function setConnected(connected) {
  els.connectionDot.className = `dot ${connected ? "online" : "offline"}`;
  els.connectionText.textContent = connected ? "CONNECTED" : "OFFLINE";
  els.daemonState.textContent = connected ? "connected" : "offline";
  els.daemonState.className = connected ? "good" : "bad";
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET" && state.csrf) headers.set("X-KITT-CSRF", state.csrf);
  const response = await fetch(path, {...options, headers, credentials: "same-origin"});
  let payload = {};
  try { payload = await response.json(); } catch (_) {}
  if (response.status === 401) {
    showPairing();
    throw new Error(payload.error || "Authentication required");
  }
  if (!response.ok) throw new Error(payload.error || `${response.status} ${response.statusText}`);
  return payload;
}

function showPairing() {
  state.csrf = "";
  els.pairingOverlay.classList.remove("hidden");
  setConnected(false);
  setTimeout(() => els.pairingCode.focus(), 50);
}

function hidePairing() {
  els.pairingOverlay.classList.add("hidden");
  els.pairingError.textContent = "";
}

async function bootstrap() {
  try {
    const me = await api("/api/me");
    state.csrf = me.csrf || "";
    hidePairing();
    await Promise.all([loadStatus(), loadSessions()]);
    setConnected(true);
  } catch (_) {
    showPairing();
  }
}

async function loadStatus() {
  try {
    const [payload, extensions] = await Promise.all([api("/api/status"), api("/api/extensions")]);
    state.status = payload;
    state.extensions = extensions;
    const status = payload.runtime || payload.snapshot || payload;
    els.workspacePath.textContent = payload.workspace_root || status.workspace_root || "workspace";
    renderInspector();
  } catch (err) {
    setConnected(false);
    toast(err.message, "bad");
  }
}

async function loadSessions(preferId = "") {
  const payload = await api("/api/sessions");
  state.sessions = payload.sessions || [];
  renderSessions();
  const target = preferId || state.sessionId || payload.active_session_id || state.sessions[0]?.id || "";
  if (target && target !== state.sessionId) await selectSession(target);
  if (!target) clearConversation("Nenhuma sessão. Crie uma para começar.");
}

function renderSessions() {
  els.sessions.replaceChildren();
  for (const session of state.sessions) {
    const button = node("button", `session-item ${session.id === state.sessionId ? "active" : ""}`);
    button.type = "button";
    const title = node("span", "session-title", session.title || session.id.slice(0, 10));
    const status = node("span", "session-status", session.status || "");
    button.append(title, status);
    button.addEventListener("click", () => selectSession(session.id));
    els.sessions.append(button);
  }
}

function clearConversation(message = "") {
  els.conversation.replaceChildren();
  state.streamMessage = null;
  if (message) els.conversation.append(node("div", "empty-state", message));
}

async function selectSession(sessionId) {
  closeEventSource();
  state.sessionId = sessionId;
  state.lastSequence = 0;
  state.events = [];
  state.approvals.clear();
  state.tools = [];
  state.children.clear();
  state.artifacts = [];
  state.diff = {loaded: false, available: false, content: ""};
  state.messagesNextBefore = "";
  state.messagesHasMore = false;
  clearConversation();
  renderSessions();
  try {
    const detail = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
    state.sessionTitle = detail.conversation?.title || sessionId.slice(0, 10);
    els.sessionName.textContent = state.sessionTitle;
    state.lastSequence = Number(detail.last_sequence || 0);
    for (const message of detail.messages || []) renderHistoryMessage(message);
    state.messagesNextBefore = detail.messages_next_before || "";
    state.messagesHasMore = Boolean(detail.messages_has_more);
    renderLoadOlderButton();
    for (const evt of detail.recent_events || []) hydrateHistoricalEvent(evt);
    for (const approval of detail.approvals || []) state.approvals.set(approval.approval_id, approval);
    renderApprovalsInConversation();
    await loadArtifacts();
    renderInspector();
    openEventSource();
    els.sidebar.classList.remove("open");
  } catch (err) {
    toast(err.message, "bad");
  }
}

function renderHistoryMessage(message, prepend = false) {
  const role = message.role === "user" ? "user" : "assistant";
  const card = node("article", `message ${role}`);
  const head = node("div", "message-head");
  head.append(node("span", "message-role", role === "user" ? "YOU" : "K.I.T.T."), node("span", "message-time", formatTime(message.created_at)));
  const body = node("div", "message-body", message.content || "");
  card.append(head, body);
  if (prepend) els.conversation.prepend(card);
  else els.conversation.append(card);
}

function renderLoadOlderButton() {
  document.getElementById("loadOlderMessages")?.remove();
  if (!state.messagesHasMore || !state.messagesNextBefore) return;
  const button = node("button", "small-button load-older", "LOAD OLDER MESSAGES");
  button.id = "loadOlderMessages";
  button.type = "button";
  button.addEventListener("click", loadOlderMessages);
  els.conversation.prepend(button);
}

async function loadOlderMessages() {
  if (!state.sessionId || !state.messagesNextBefore) return;
  const button = document.getElementById("loadOlderMessages");
  if (button) button.disabled = true;
  try {
    const detail = await api(
      `/api/sessions/${encodeURIComponent(state.sessionId)}?before=${encodeURIComponent(state.messagesNextBefore)}&limit=50&include_events=0`
    );
    const older = detail.messages || [];
    for (const message of [...older].reverse()) renderHistoryMessage(message, true);
    state.messagesNextBefore = detail.messages_next_before || "";
    state.messagesHasMore = Boolean(detail.messages_has_more);
    renderLoadOlderButton();
  } catch (err) {
    toast(err.message, "bad");
    if (button) button.disabled = false;
  }
}

function appendAssistantDelta(delta) {
  if (!state.streamMessage) {
    const card = node("article", "message assistant");
    const head = node("div", "message-head");
    head.append(node("span", "message-role", "K.I.T.T."), node("span", "message-time", formatTime(Date.now()/1000)));
    const body = node("div", "message-body", "");
    card.append(head, body);
    els.conversation.append(card);
    state.streamMessage = {card, body, text: ""};
  }
  state.streamMessage.text += delta || "";
  state.streamMessage.body.textContent = state.streamMessage.text;
  scrollConversation();
}

function appendUserMessage(text) {
  renderHistoryMessage({role: "user", content: text, created_at: Date.now()/1000});
  scrollConversation();
}

function scrollConversation() {
  requestAnimationFrame(() => { els.conversation.scrollTop = els.conversation.scrollHeight; });
}

function eventPayload(evt) { return evt?.payload || {}; }

function hydrateHistoricalEvent(evt) {
  state.events.push(evt);
  if (state.events.length > 300) state.events.splice(0, state.events.length - 300);
  const type = evt.event_type || "Event";
  const p = eventPayload(evt);
  if (type === "TurnStarted") {
    state.activeTurnId = p.turn_id || "";
    els.cancelButton.classList.toggle("hidden", !state.activeTurnId);
  } else if (["TurnCompleted", "TurnFailed", "TurnCancelled", "TurnBlocked"].includes(type)) {
    state.activeTurnId = "";
    els.cancelButton.classList.add("hidden");
  } else if (type === "ModelSelected") {
    els.modelName.textContent = p.model || p.profile_name || "—";
  } else if (type === "BudgetApplied") {
    const used = Number(p.total_input_tokens || 0);
    const total = Number(p.window_size || 0);
    els.contextValue.textContent = total ? `${used} / ${total}` : String(used || "—");
  } else if (type === "ToolStarted" || type === "ToolCompleted") {
    state.tools.unshift({type, ...p, timestamp: evt.created_at});
    state.tools = state.tools.slice(0, 80);
  } else if (type === "ChildAgentSpawned") {
    state.children.set(p.child_id, {name: p.name || p.child_id, status: "running", ...p});
  } else if (type === "ChildAgentProgress" || type === "ChildAgentFinished") {
    state.children.set(p.child_id, {...(state.children.get(p.child_id) || {}), ...p});
  }
  renderChildren();
}

function handleEvent(evt) {
  const seq = Number(evt.sequence_id || 0);
  if (seq && seq <= state.lastSequence) return;
  if (seq) state.lastSequence = seq;
  state.events.push(evt);
  if (state.events.length > 300) state.events.splice(0, state.events.length - 300);
  const type = evt.event_type || "Event";
  const p = eventPayload(evt);

  if (type === "TurnStarted") {
    state.activeTurnId = p.turn_id || "";
    els.cancelButton.classList.toggle("hidden", !state.activeTurnId);
    state.streamMessage = null;
    addActivity("Turn started");
  } else if (type === "TextDelta") {
    appendAssistantDelta(p.delta || "");
  } else if (type === "ModelSelected") {
    els.modelName.textContent = p.model || p.profile_name || "—";
  } else if (type === "BudgetApplied") {
    const used = Number(p.total_input_tokens || 0);
    const total = Number(p.window_size || 0);
    els.contextValue.textContent = total ? `${used} / ${total}` : String(used || "—");
  } else if (type === "ToolStarted") {
    state.tools.unshift({type, ...p, timestamp: evt.created_at});
    state.tools = state.tools.slice(0, 80);
    addToolCard("TOOL", p.tool_name || "tool", p.args || {});
  } else if (type === "ToolCompleted") {
    state.tools.unshift({type, ...p, timestamp: evt.created_at});
    state.tools = state.tools.slice(0, 80);
  } else if (type === "ApprovalRequired") {
    const approval = {
      approval_id: p.approval_request_id,
      turn_id: p.turn_id,
      conversation_id: p.conversation_id,
      tool_name: p.tool_name,
      args: p.args || {},
      action_hash: p.action_hash,
      workspace_id: p.workspace_id,
    };
    state.approvals.set(approval.approval_id, approval);
    addApprovalCard(approval);
    addActivity(`Approval: ${approval.tool_name || "tool"}`);
  } else if (["TurnCompleted", "TurnFailed", "TurnCancelled", "TurnBlocked"].includes(type)) {
    state.activeTurnId = "";
    els.cancelButton.classList.add("hidden");
    if (type === "TurnCompleted" && p.response && (!state.streamMessage || !state.streamMessage.text)) appendAssistantDelta(p.response);
    state.streamMessage = null;
    addActivity(type.replace("Turn", "Turn "));
    refreshApprovals();
    loadArtifacts();
    state.diff = {loaded: false, available: false, content: ""};
  } else if (type === "EditApplied") {
    state.diff = {loaded: false, available: false, content: ""};
    loadArtifacts();
  } else if (type === "ChildAgentSpawned") {
    state.children.set(p.child_id, {name: p.name || p.child_id, status: "running", ...p});
    renderChildren();
  } else if (type === "ChildAgentProgress") {
    state.children.set(p.child_id, {...(state.children.get(p.child_id) || {}), ...p});
    renderChildren();
  } else if (type === "ChildAgentFinished") {
    state.children.set(p.child_id, {...(state.children.get(p.child_id) || {}), ...p, status: p.status || "finished"});
    renderChildren();
  }
  renderInspector();
  scrollConversation();
}

function openEventSource() {
  if (!state.sessionId) return;
  const url = `/api/sessions/${encodeURIComponent(state.sessionId)}/events?after=${state.lastSequence}`;
  const source = new EventSource(url, {withCredentials: true});
  state.source = source;
  source.addEventListener("open", () => setConnected(true));
  source.addEventListener("kitt", (event) => {
    try { handleEvent(JSON.parse(event.data)); } catch (err) { console.error(err); }
  });
  source.addEventListener("error", () => setConnected(false));
}

function closeEventSource() {
  if (state.source) state.source.close();
  state.source = null;
}

function addToolCard(label, title, payload) {
  const card = node("div", "event-card");
  const head = node("div", "event-title");
  head.append(node("strong", "", `${label} · ${title}`), node("span", "muted", formatTime(Date.now()/1000)));
  const pre = node("pre", "", JSON.stringify(payload, null, 2));
  card.append(head, pre);
  els.conversation.append(card);
}

function addApprovalCard(approval) {
  if (!approval?.approval_id || document.querySelector(`[data-approval-id="${CSS.escape(approval.approval_id)}"]`)) return;
  const card = node("div", "approval-card");
  card.dataset.approvalId = approval.approval_id;
  card.append(node("div", "approval-title", `⚠ APPROVAL REQUIRED · ${approval.tool_name || "tool"}`));
  const summary = node("pre", "", JSON.stringify(approval.args || approval.affected_paths || {}, null, 2));
  summary.className = "message-body muted";
  card.append(summary);
  const actions = node("div", "approval-actions");
  const approve = node("button", "approve-button", "✓ APROVAR");
  const deny = node("button", "deny-button", "× NEGAR");
  approve.type = deny.type = "button";
  approve.addEventListener("click", () => decideApproval(approval.approval_id, "approve", card));
  deny.addEventListener("click", () => decideApproval(approval.approval_id, "deny", card));
  actions.append(approve, deny);
  card.append(actions);
  els.conversation.append(card);
}

function renderApprovalsInConversation() {
  for (const approval of state.approvals.values()) addApprovalCard(approval);
}

async function refreshApprovals() {
  if (!state.sessionId) return;
  try {
    const payload = await api(`/api/approvals?session_id=${encodeURIComponent(state.sessionId)}`);
    state.approvals.clear();
    for (const approval of payload.approvals || []) state.approvals.set(approval.approval_id, approval);
    renderInspector();
  } catch (_) {}
}

async function decideApproval(id, decision, card) {
  try {
    await api(`/api/approvals/${encodeURIComponent(id)}/${decision}`, {
      method: "POST",
      body: JSON.stringify({session_id: state.sessionId}),
    });
    state.approvals.delete(id);
    card?.remove();
    toast(decision === "approve" ? "Ação aprovada" : "Ação negada", decision === "approve" ? "good" : "warn");
    renderInspector();
  } catch (err) { toast(err.message, "bad"); }
}

function renderChildren() {
  els.children.replaceChildren();
  if (!state.children.size) {
    els.children.className = "child-list empty-state";
    els.children.textContent = "Nenhum child ativo";
    return;
  }
  els.children.className = "child-list";
  for (const child of state.children.values()) {
    const row = node("div", "child-row");
    row.append(node("span", "child-indicator"), node("span", "", child.name || child.child_id), node("span", "muted", child.status || ""));
    els.children.append(row);
  }
}

function addActivity(text) {
  const row = node("div", "activity-row");
  row.append(node("span", "muted", formatTime(Date.now()/1000)), node("span", "", text));
  els.activity.prepend(row);
  while (els.activity.children.length > 12) els.activity.lastElementChild?.remove();
}

async function loadArtifacts() {
  if (!state.sessionId) { state.artifacts = []; return; }
  try {
    const payload = await api(`/api/artifacts?session_id=${encodeURIComponent(state.sessionId)}`);
    state.artifacts = payload.artifacts || [];
  } catch (_) { state.artifacts = []; }
}

async function loadDiff() {
  if (state.diff.loaded) return;
  try {
    const payload = await api("/api/diff");
    state.diff = {loaded: true, ...payload};
  } catch (err) {
    state.diff = {loaded: true, available: false, content: "", error: err.message};
  }
  renderInspector();
}

async function openArtifact(artifact, card) {
  try {
    const payload = await api(`/api/artifacts/${encodeURIComponent(artifact.id)}?session_id=${encodeURIComponent(state.sessionId)}&offset=0`);
    const existing = card.querySelector("pre");
    const pre = existing || node("pre", "");
    pre.textContent = payload.content || "(empty artifact)";
    if (!existing) card.append(pre);
    if (payload.has_more) card.append(node("div", "muted", `Preview limited to ${payload.bytes_returned} / ${payload.total_bytes} bytes`));
  } catch (err) { toast(err.message, "bad"); }
}

function renderInspector() {
  els.inspectorContent.replaceChildren();
  if (state.inspectorTab === "events") {
    for (const evt of state.events.slice(-40).reverse()) {
      const card = node("div", "inspector-card");
      card.append(node("h3", "", `${evt.event_type || "Event"} · #${evt.sequence_id || "?"}`));
      card.append(node("pre", "", JSON.stringify(evt.payload || {}, null, 2)));
      els.inspectorContent.append(card);
    }
  } else if (state.inspectorTab === "output") {
    for (const tool of state.tools.slice(0, 40)) {
      const card = node("div", "inspector-card");
      card.append(node("h3", "", `${tool.tool_name || "tool"} · ${tool.success === false ? "failed" : tool.type}`));
      card.append(node("pre", "", tool.output || tool.error || JSON.stringify(tool.args || {}, null, 2)));
      els.inspectorContent.append(card);
    }
  } else if (state.inspectorTab === "approvals") {
    if (!state.approvals.size) els.inspectorContent.append(node("div", "empty-state", "Nenhuma aprovação pendente"));
    for (const approval of state.approvals.values()) {
      const card = node("div", "inspector-card");
      card.append(node("h3", "", approval.tool_name || "Approval"));
      card.append(node("pre", "", JSON.stringify(approval, null, 2)));
      els.inspectorContent.append(card);
    }
  } else if (state.inspectorTab === "diff") {
    if (!state.diff.loaded) {
      els.inspectorContent.append(node("div", "empty-state", "Carregando diff…"));
      loadDiff();
      return;
    }
    const card = node("div", "inspector-card diff-card");
    card.append(node("h3", "", state.diff.available ? "WORKSPACE DIFF" : "DIFF UNAVAILABLE"));
    card.append(node("pre", "", state.diff.content || state.diff.error || "Working tree sem alterações rastreadas."));
    if (state.diff.truncated) card.append(node("div", "empty-state", "Diff truncado no limite seguro de 256 KiB."));
    els.inspectorContent.append(card);
  } else if (state.inspectorTab === "artifacts") {
    if (!state.artifacts.length) els.inspectorContent.append(node("div", "empty-state", "Nenhum artefato nesta sessão"));
    for (const artifact of state.artifacts) {
      const card = node("div", "inspector-card artifact-card");
      card.append(node("h3", "", `${artifact.artifact_type || "artifact"} · ${artifact.id}`));
      const meta = node("div", "content", `${artifact.summary || "Sem resumo"}\n${artifact.size_bytes || 0} bytes · ${artifact.sensitivity || "NORMAL"}`);
      const button = node("button", "small-button", "OPEN PREVIEW");
      button.type = "button";
      button.addEventListener("click", () => openArtifact(artifact, card));
      card.append(meta, button);
      els.inspectorContent.append(card);
    }
  } else {
    const status = state.status.runtime || state.status.snapshot || state.status;
    const card = node("div", "inspector-card");
    card.append(node("h3", "", "RUNTIME STATUS"));
    const content = node("div", "content");
    for (const [key, value] of Object.entries(status || {})) {
      const row = node("div", "status-row");
      row.append(node("span", "muted", key), node("span", "", typeof value === "object" ? JSON.stringify(value) : value));
      content.append(row);
    }
    card.append(content);
    els.inspectorContent.append(card);
    const ext = node("div", "inspector-card");
    ext.append(node("h3", "", "EXTENSIONS / MCP"));
    ext.append(node("pre", "", JSON.stringify(state.extensions || {}, null, 2)));
    els.inspectorContent.append(ext);
  }
}

els.pairingForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.pairingError.textContent = "";
  try {
    const payload = await api("/api/pair", {method: "POST", body: JSON.stringify({code: els.pairingCode.value.trim()})});
    state.csrf = payload.csrf || "";
    els.pairingCode.value = "";
    hidePairing();
    await Promise.all([loadStatus(), loadSessions()]);
    setConnected(true);
  } catch (err) { els.pairingError.textContent = err.message; }
});

els.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = els.promptInput.value;
  if (!state.sessionId || !text.trim()) return;
  els.sendButton.disabled = true;
  els.promptInput.value = "";
  appendUserMessage(text);
  state.streamMessage = null;
  try {
    const payload = await api(`/api/sessions/${encodeURIComponent(state.sessionId)}/input`, {method: "POST", body: JSON.stringify({text, mode: "auto"})});
    state.activeTurnId = payload.turn_id || "";
    els.cancelButton.classList.toggle("hidden", !state.activeTurnId);
  } catch (err) {
    toast(err.message, "bad");
    els.promptInput.value = text;
  } finally { els.sendButton.disabled = false; els.promptInput.focus(); }
});

els.cancelButton.addEventListener("click", async () => {
  if (!state.activeTurnId || !state.sessionId) return;
  const turnId = state.activeTurnId;
  els.cancelButton.disabled = true;
  try {
    await api(`/api/turns/${encodeURIComponent(turnId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({session_id: state.sessionId}),
    });
    toast("Cancelamento solicitado", "warn");
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    els.cancelButton.disabled = false;
  }
});

els.promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.composer.requestSubmit();
  }
});
els.promptInput.addEventListener("input", () => {
  els.promptInput.style.height = "auto";
  els.promptInput.style.height = `${Math.min(160, els.promptInput.scrollHeight)}px`;
});

els.newSessionButton.addEventListener("click", async () => {
  try {
    const created = await api("/api/sessions", {method: "POST", body: JSON.stringify({title: "New Session"})});
    await loadSessions(created.session_id || "");
  } catch (err) { toast(err.message, "bad"); }
});

els.logoutButton.addEventListener("click", async () => {
  try { await api("/api/logout", {method: "POST", body: "{}"}); } catch (_) {}
  closeEventSource();
  showPairing();
});

els.navToggle.addEventListener("click", () => els.sidebar.classList.toggle("open"));
els.inspectorToggle.addEventListener("click", () => els.inspector.classList.toggle("open"));
for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    state.inspectorTab = tab.dataset.tab || "events";
    if (state.inspectorTab === "diff") state.diff = {loaded: false, available: false, content: ""};
    if (state.inspectorTab === "artifacts") loadArtifacts().then(renderInspector);
    else renderInspector();
  });
}

window.addEventListener("beforeunload", closeEventSource);
bootstrap();
