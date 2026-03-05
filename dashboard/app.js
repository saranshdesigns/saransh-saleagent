// ============================================================
// SaranshDesigns Dashboard — app.js
// Vanilla JS SPA. No framework, no build step.
// ============================================================

let token = localStorage.getItem("dash_token") || null;
let currentPhone = null;
let ws = null;
let conversations = [];

// ---- STAGE / SERVICE COLOR MAPS ----
const STAGE_BADGE = {
  new:                  "badge-new",
  identifying_service:  "badge-identifying",
  collecting_details:   "badge-collecting",
  confirming_details:   "badge-confirming",
  presenting_pricing:   "badge-pricing",
  handling_objection:   "badge-negotiating",
  negotiating:          "badge-negotiating",
  pricing_confirmed:    "badge-pricing",
  handoff:              "badge-handoff",
  escalated:            "badge-escalated"
};

const SERVICE_BADGE = {
  logo:      "badge-logo",
  packaging: "badge-packaging",
  website:   "badge-website",
  unknown:   "badge-unknown"
};

// ============================================================
// INIT
// ============================================================

document.addEventListener("DOMContentLoaded", () => {
  if (token) {
    showApp();
  }
  document.getElementById("login-btn").addEventListener("click", doLogin);
  document.getElementById("login-password").addEventListener("keydown", e => {
    if (e.key === "Enter") doLogin();
  });
  updateClock();
  setInterval(updateClock, 30000);
});

function updateClock() {
  const now = new Date();
  const tz = "Asia/Kolkata";
  document.getElementById("current-time").textContent =
    now.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: tz }) + "  " +
    now.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: tz }) + " IST";
}

// ============================================================
// AUTH
// ============================================================

async function doLogin() {
  const pw = document.getElementById("login-password").value;
  const errEl = document.getElementById("login-error");
  const btn = document.getElementById("login-btn");
  errEl.style.display = "none";
  btn.textContent = "Signing in...";
  btn.disabled = true;

  try {
    const resp = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw })
    });

    if (!resp.ok) {
      errEl.style.display = "block";
      errEl.textContent = "Incorrect password. Try again.";
      return;
    }

    const data = await resp.json();
    token = data.access_token;
    localStorage.setItem("dash_token", token);
    showApp();
  } catch (e) {
    errEl.textContent = "Cannot connect. Is the agent server running?";
    errEl.style.display = "block";
  } finally {
    btn.textContent = "Sign In";
    btn.disabled = false;
  }
}

function logout() {
  localStorage.removeItem("dash_token");
  token = null;
  currentPhone = null;
  if (ws) ws.close();
  document.getElementById("app").classList.remove("visible");
  document.getElementById("app").style.display = "none";
  document.getElementById("login-screen").style.display = "flex";
  document.getElementById("login-password").value = "";
}

// ============================================================
// APP SHOW
// ============================================================

function showApp() {
  document.getElementById("login-screen").style.display = "none";
  const appEl = document.getElementById("app");
  appEl.style.display = "flex";
  appEl.classList.add("visible");

  loadAnalytics();
  loadConversations();
  connectWebSocket();
}

// ============================================================
// ANALYTICS
// ============================================================

async function loadAnalytics() {
  try {
    const data = await apiFetch("/api/analytics");
    document.getElementById("stat-total").textContent = data.total_conversations || 0;
    document.getElementById("stat-today").textContent = data.active_today || 0;
    document.getElementById("stat-handoffs").textContent = data.handoffs || 0;

    // Stage breakdown badges
    const container = document.getElementById("stage-breakdown");
    container.innerHTML = "";
    const stages = data.stage_breakdown || {};
    for (const [stage, count] of Object.entries(stages)) {
      const cls = STAGE_BADGE[stage] || "badge-new";
      const label = stage.replace(/_/g, " ");
      container.innerHTML +=
        `<span class="badge ${cls}">${label}: ${count}</span>`;
    }
  } catch (e) {
    console.warn("Analytics load failed", e);
  }
}

// ============================================================
// CONVERSATION LIST
// ============================================================

async function loadConversations() {
  try {
    conversations = await apiFetch("/api/conversations");
    renderConvList(conversations);
    document.getElementById("conv-count").textContent = conversations.length;
  } catch (e) {
    console.warn("Conv list load failed", e);
  }
}

function renderConvList(convs) {
  const list = document.getElementById("conv-list");

  if (!convs || !convs.length) {
    list.innerHTML = `<div class="empty-state">No conversations yet</div>`;
    return;
  }

  list.innerHTML = convs.map(conv => {
    const stageCls = STAGE_BADGE[conv.stage] || "badge-new";
    const svcCls   = SERVICE_BADGE[conv.service] || "badge-unknown";
    const score    = conv.seriousness_score || 0;
    const scoreCls = score >= 65 ? "score-high" : score >= 35 ? "score-med" : "score-low";
    const timeStr  = formatTime(conv.last_updated);
    const lastMsg  = escHtml((conv.last_message || "").slice(0, 65));
    const active   = conv.phone === currentPhone ? " active" : "";

    return `
      <div class="conv-item${active}" onclick="openConversation('${escAttr(conv.phone)}')">
        <div class="conv-item-top">
          <span class="conv-phone">${formatPhone(conv.phone)}</span>
          <span class="conv-time">${timeStr}</span>
        </div>
        <div class="conv-item-mid">
          <span class="badge ${svcCls}">${conv.service || "unknown"}</span>
          <span class="badge ${stageCls}">${(conv.stage || "new").replace(/_/g, " ")}</span>
          <span class="score-chip ${scoreCls}">${score}</span>
        </div>
        <div class="conv-last-msg">${lastMsg || "<em style='color:#ccc'>No messages</em>"}</div>
      </div>`;
  }).join("");
}

// ============================================================
// OPEN CONVERSATION
// ============================================================

async function openConversation(phone) {
  currentPhone = phone;

  // Update active state in sidebar
  document.querySelectorAll(".conv-item").forEach(el => el.classList.remove("active"));
  // Re-render list to reflect new active state
  renderConvList(conversations);

  try {
    const conv = await apiFetch(`/api/conversations/${encodeURIComponent(phone)}`);
    renderChatPanel(conv);
  } catch (e) {
    console.error("Failed to load conversation", e);
  }
}

function renderChatPanel(conv) {
  document.getElementById("no-chat").classList.add("hidden");
  const panel = document.getElementById("chat-panel");
  panel.classList.remove("hidden");
  panel.style.display = "flex";

  // Header info
  document.getElementById("chat-phone").textContent = formatPhone(conv.phone);
  const msgCount = (conv.messages || []).length;
  document.getElementById("chat-subtitle").textContent =
    `${msgCount} messages  •  Score: ${conv.seriousness_score || 0}/100` +
    (conv.agreed_price ? `  •  Agreed: ₹${conv.agreed_price}` : "");

  // Header badges
  const stageCls = STAGE_BADGE[conv.stage] || "badge-new";
  const svcCls   = SERVICE_BADGE[conv.service] || "badge-unknown";
  document.getElementById("chat-badges").innerHTML = `
    <span class="badge ${svcCls}">${conv.service || "unknown"}</span>
    <span class="badge ${stageCls}">${(conv.stage || "new").replace(/_/g, " ")}</span>
    ${conv.handoff_triggered ? '<span class="badge badge-handoff">Handoff</span>' : ""}
  `;

  renderMessages(conv.messages || []);

  // Focus input
  document.getElementById("msg-input").focus();
}

function renderMessages(messages) {
  const scroll = document.getElementById("messages-scroll");
  if (!messages.length) {
    scroll.innerHTML = `<div class="empty-state">No messages yet</div>`;
    return;
  }

  scroll.innerHTML = messages.map((msg, index) => {
    const roleLabel = msg.role === "user" ? "Client"
                    : msg.role === "assistant" ? "AI Agent"
                    : "Owner (You)";
    const timeStr = msg.timestamp ? formatTime(msg.timestamp) : "";

    // Correction button only on AI Agent messages
    const correctionBtn = msg.role === "assistant"
      ? `<button class="correct-btn" onclick="showCorrectionPanel(${index})" title="Send correction to client">&#9888; Correct</button>`
      : "";

    return `
      <div class="msg ${escAttr(msg.role)}" data-index="${index}">
        <div style="max-width:100%">
          <div class="msg-role-label">${roleLabel} ${correctionBtn}</div>
          <div class="bubble">${escHtml(msg.content || "")}</div>
          <div class="msg-meta">${timeStr}</div>
          <div class="correction-panel hidden" id="cp-${index}">
            <div class="correction-label">Send correction to client:</div>
            <textarea id="ct-${index}" placeholder="Type the correct message..." rows="2"></textarea>
            <div class="correction-actions">
              <button class="btn-send-correction" onclick="sendCorrection(${index})">Send &amp; Remove from AI Memory</button>
              <button class="btn-cancel-correction" onclick="hideCorrectionPanel(${index})">Cancel</button>
            </div>
          </div>
        </div>
      </div>`;
  }).join("");

  scroll.scrollTop = scroll.scrollHeight;
}

// ============================================================
// CORRECTION FEATURE
// ============================================================

function showCorrectionPanel(index) {
  const panel = document.getElementById(`cp-${index}`);
  if (!panel) return;
  panel.classList.remove("hidden");
  const ta = document.getElementById(`ct-${index}`);
  if (ta) ta.focus();
}

function hideCorrectionPanel(index) {
  const panel = document.getElementById(`cp-${index}`);
  if (panel) panel.classList.add("hidden");
}

async function sendCorrection(index) {
  if (!currentPhone) return;

  const ta = document.getElementById(`ct-${index}`);
  const correctionText = ta ? ta.value.trim() : "";

  if (!correctionText) {
    if (ta) ta.focus();
    return;
  }

  const btn = document.querySelector(`#cp-${index} .btn-send-correction`);
  if (btn) { btn.disabled = true; btn.textContent = "Sending..."; }

  try {
    // Step 1: Delete the wrong AI message from context
    await apiFetch(`/api/conversations/${encodeURIComponent(currentPhone)}/messages/${index}`, {
      method: "DELETE"
    });

    // Step 2: Send the correction message to the client
    await apiFetch(`/api/conversations/${encodeURIComponent(currentPhone)}/send`, {
      method: "POST",
      body: JSON.stringify({ message: correctionText })
    });

    // Step 3: Reload the full conversation to reflect changes
    const conv = await apiFetch(`/api/conversations/${encodeURIComponent(currentPhone)}`);
    renderChatPanel(conv);
    loadConversations();

  } catch (e) {
    alert("Failed to send correction. Please try again.");
    if (btn) { btn.disabled = false; btn.textContent = "Send & Remove from AI Memory"; }
  }
}

function appendMessage(role, content, timestamp) {
  const scroll = document.getElementById("messages-scroll");
  // Remove empty state if present
  const empty = scroll.querySelector(".empty-state");
  if (empty) empty.remove();

  const roleLabel = role === "user" ? "Client"
                  : role === "assistant" ? "AI Agent"
                  : "Owner (You)";
  const timeStr = timestamp ? formatTime(timestamp) : formatTime(new Date().toISOString());

  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `
    <div style="max-width:100%">
      <div class="msg-role-label">${roleLabel}</div>
      <div class="bubble">${escHtml(content || "")}</div>
      <div class="msg-meta">${timeStr}</div>
    </div>`;
  scroll.appendChild(div);
  scroll.scrollTop = scroll.scrollHeight;
}

// ============================================================
// SEND OWNER MESSAGE
// ============================================================

async function sendOwnerMessage() {
  if (!currentPhone) return;

  const input = document.getElementById("msg-input");
  const message = input.value.trim();
  if (!message) return;

  // /reset command — clears conversation history for this number
  if (message.toLowerCase() === "/reset") {
    if (!confirm(`Reset entire conversation for ${currentPhone}? This cannot be undone.`)) return;
    input.value = "";
    input.style.height = "auto";
    try {
      await apiFetch(`/api/conversations/${encodeURIComponent(currentPhone)}`, { method: "DELETE" });
      document.getElementById("chat-messages").innerHTML =
        `<div style="text-align:center;color:#aaa;padding:40px 0;font-size:13px;">Conversation reset. Waiting for new message...</div>`;
      loadConversations();
      loadAnalytics();
    } catch (e) {
      alert("Reset failed. Please try again.");
    }
    return;
  }

  const btn = document.getElementById("send-btn");
  btn.disabled = true;
  input.value = "";
  input.style.height = "auto";

  try {
    await apiFetch(`/api/conversations/${encodeURIComponent(currentPhone)}/send`, {
      method: "POST",
      body: JSON.stringify({ message })
    });

    // Optimistically append bubble
    appendMessage("owner", message, new Date().toISOString());

    // Refresh sidebar
    loadConversations();
    loadAnalytics();
  } catch (e) {
    alert("Failed to send message. Please check your connection.");
    input.value = message; // restore text
  } finally {
    btn.disabled = false;
    input.focus();
  }
}

function handleInputKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendOwnerMessage();
  }
}

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 100) + "px";
}

// ============================================================
// WEBSOCKET
// ============================================================

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const wsUrl = `${proto}://${location.host}/ws`;

  try {
    ws = new WebSocket(wsUrl);
  } catch (e) {
    setWsStatus(false);
    setTimeout(connectWebSocket, 5000);
    return;
  }

  ws.onopen = () => {
    ws.send(token); // Auth token as first message
    setWsStatus(true);
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleWsEvent(data);
    } catch (e) {
      // Ignore non-JSON
    }
  };

  ws.onclose = () => {
    setWsStatus(false);
    setTimeout(connectWebSocket, 5000);
  };

  ws.onerror = () => {
    setWsStatus(false);
  };
}

function handleWsEvent(data) {
  if (data.type === "connected") return;
  if (data.type === "error") {
    logout();
    return;
  }

  // Always refresh sidebar list and analytics on any event
  loadConversations();
  loadAnalytics();

  // If this message belongs to the open conversation, append it live
  if (data.phone === currentPhone) {
    if (data.type === "new_message" && data.role !== "owner") {
      // Owner messages are already appended optimistically — skip duplicates
      appendMessage(data.role, data.content, data.timestamp);
    }
  }
}

function setWsStatus(connected) {
  const dot = document.getElementById("ws-dot");
  const label = document.getElementById("ws-label");
  if (!dot || !label) return;
  dot.style.background = connected ? "#4caf50" : "#ef5350";
  label.textContent = connected ? "Live" : "Reconnecting...";
}

// ============================================================
// API FETCH HELPER
// ============================================================

async function apiFetch(path, options = {}) {
  const resp = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`,
      ...(options.headers || {})
    }
  });

  if (resp.status === 401) {
    logout();
    throw new Error("Unauthorized");
  }

  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }

  return resp.json();
}

// ============================================================
// HELPERS
// ============================================================

function formatPhone(phone) {
  if (!phone) return "Unknown";
  const s = String(phone);
  if (s.length === 12 && s.startsWith("91")) {
    return `+91 ${s.slice(2, 7)} ${s.slice(7)}`;
  }
  return `+${s}`;
}

function formatTime(isoStr) {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    const now = new Date();
    const tz = "Asia/Kolkata";
    const sameDay = d.toLocaleDateString("en-IN", { timeZone: tz }) === now.toLocaleDateString("en-IN", { timeZone: tz });
    if (sameDay) {
      return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: tz });
    }
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: tz }) +
      " " + d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: tz });
  } catch (e) {
    return "";
  }
}

function escHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/\n/g, "<br>");
}

function escAttr(str) {
  if (!str) return "";
  return String(str).replace(/'/g, "\\'").replace(/"/g, "&quot;");
}
