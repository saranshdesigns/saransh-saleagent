// ============================================================
// SaranshDesigns Dashboard — app.js
// Vanilla JS SPA. No framework, no build step.
// ============================================================

let token = localStorage.getItem("dash_token") || null;
let currentPhone = null;
let ws = null;
let conversations = [];
let pricingData = null;
let kbEntries = [];
let tabsLoaded = { conversations: true, services: false, "knowledge-base": false, "custom-instructions": false };

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

  // Tab navigation listeners
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
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
// TAB SYSTEM
// ============================================================

function switchTab(tabName) {
  // Update tab buttons
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });

  // Update tab content panels
  document.querySelectorAll(".tab-content").forEach(panel => {
    panel.classList.toggle("active", panel.id === `tab-${tabName}`);
  });

  // Load data on first visit
  if (!tabsLoaded[tabName]) {
    tabsLoaded[tabName] = true;
    if (tabName === "services") loadPricing();
    if (tabName === "knowledge-base") loadKnowledgeBase();
    if (tabName === "custom-instructions") loadCustomInstructions();
  }
}

// ============================================================
// SERVICES & PRICING
// ============================================================

function toggleServiceCard(service) {
  const body = document.getElementById(`body-${service}`);
  const arrow = document.getElementById(`arrow-${service}`);
  if (!body) return;
  const isOpen = body.classList.contains("open");
  body.classList.toggle("open");
  if (arrow) arrow.textContent = isOpen ? "\u25B6" : "\u25BC";
}

async function loadPricing() {
  try {
    pricingData = await apiFetch("/api/pricing");
    populatePricingFields(pricingData);
  } catch (e) {
    console.warn("Failed to load pricing", e);
    showToast("Failed to load pricing data", "error");
  }
}

function populatePricingFields(data) {
  if (!data) return;

  // Logo
  const logo = data.logo || {};
  const lp = logo.logo_package || {};
  const bp = logo.branding_package || {};
  setVal("logo-logo_package-price", lp.price);
  setVal("logo-logo_package-min_price", lp.min_price);
  setVal("logo-branding_package-price", bp.price);
  setVal("logo-branding_package-min_price", bp.min_price);
  setVal("logo-advance_percent", logo.advance_percent);

  // Packaging
  const pkg = data.packaging || {};
  const pb = pkg.pouch_box || {};
  const lb = pkg.label || {};
  for (const type of ["pouch_box", "label"]) {
    const prefix = type === "pouch_box" ? "pkg-pouch_box" : "pkg-label";
    const src = type === "pouch_box" ? pb : lb;
    for (const tier of ["master", "variant", "size_change"]) {
      const t = src[tier] || {};
      setVal(`${prefix}-${tier}-price`, t.price);
      setVal(`${prefix}-${tier}-min_price`, t.min_price);
    }
  }
  setVal("pkg-advance_percent", pkg.advance_percent);

  // Website
  const web = data.website || {};
  const packages = web.packages || {};
  for (const pkg of ["starter", "business", "premium", "ecommerce"]) {
    const p = packages[pkg] || {};
    setVal(`web-${pkg}-price_min`, p.price_min);
    setVal(`web-${pkg}-price_max`, p.price_max);
    setVal(`web-${pkg}-negotiated_min`, p.negotiated_min);
    setVal(`web-${pkg}-advance`, p.advance);
    setVal(`web-${pkg}-advance_min`, p.advance_min);
  }
}

function setVal(id, value) {
  const el = document.getElementById(id);
  if (el && value !== undefined && value !== null) el.value = value;
}

function getVal(id) {
  const el = document.getElementById(id);
  return el ? Number(el.value) || 0 : 0;
}

async function savePricing() {
  // Build pricing object from all input fields
  const updated = {
    logo: {
      logo_package: {
        price: getVal("logo-logo_package-price"),
        min_price: getVal("logo-logo_package-min_price")
      },
      branding_package: {
        price: getVal("logo-branding_package-price"),
        min_price: getVal("logo-branding_package-min_price")
      },
      advance_percent: getVal("logo-advance_percent")
    },
    packaging: {
      pouch_box: {
        master: { price: getVal("pkg-pouch_box-master-price"), min_price: getVal("pkg-pouch_box-master-min_price") },
        variant: { price: getVal("pkg-pouch_box-variant-price"), min_price: getVal("pkg-pouch_box-variant-min_price") },
        size_change: { price: getVal("pkg-pouch_box-size_change-price"), min_price: getVal("pkg-pouch_box-size_change-min_price") }
      },
      label: {
        master: { price: getVal("pkg-label-master-price"), min_price: getVal("pkg-label-master-min_price") },
        variant: { price: getVal("pkg-label-variant-price"), min_price: getVal("pkg-label-variant-min_price") },
        size_change: { price: getVal("pkg-label-size_change-price"), min_price: getVal("pkg-label-size_change-min_price") }
      },
      advance_percent: getVal("pkg-advance_percent")
    },
    website: {
      packages: {
        starter: {
          price_min: getVal("web-starter-price_min"), price_max: getVal("web-starter-price_max"),
          negotiated_min: getVal("web-starter-negotiated_min"), advance: getVal("web-starter-advance"),
          advance_min: getVal("web-starter-advance_min")
        },
        business: {
          price_min: getVal("web-business-price_min"), price_max: getVal("web-business-price_max"),
          negotiated_min: getVal("web-business-negotiated_min"), advance: getVal("web-business-advance"),
          advance_min: getVal("web-business-advance_min")
        },
        premium: {
          price_min: getVal("web-premium-price_min"), price_max: getVal("web-premium-price_max"),
          negotiated_min: getVal("web-premium-negotiated_min"), advance: getVal("web-premium-advance"),
          advance_min: getVal("web-premium-advance_min")
        },
        ecommerce: {
          price_min: getVal("web-ecommerce-price_min"), price_max: getVal("web-ecommerce-price_max"),
          negotiated_min: getVal("web-ecommerce-negotiated_min"), advance: getVal("web-ecommerce-advance"),
          advance_min: getVal("web-ecommerce-advance_min")
        }
      }
    }
  };

  // Preserve any extra keys from the original data
  if (pricingData) {
    for (const key of Object.keys(pricingData)) {
      if (!updated[key]) updated[key] = pricingData[key];
    }
    // Preserve extra keys within each service too
    if (pricingData.logo) {
      for (const key of Object.keys(pricingData.logo)) {
        if (!updated.logo[key]) updated.logo[key] = pricingData.logo[key];
      }
    }
    if (pricingData.packaging) {
      for (const key of Object.keys(pricingData.packaging)) {
        if (!updated.packaging[key]) updated.packaging[key] = pricingData.packaging[key];
      }
    }
    if (pricingData.website) {
      for (const key of Object.keys(pricingData.website)) {
        if (!updated.website[key]) updated.website[key] = pricingData.website[key];
      }
    }
  }

  try {
    await apiFetch("/api/pricing", {
      method: "PUT",
      body: JSON.stringify(updated)
    });
    pricingData = updated;
    showToast("Pricing saved successfully!", "success");
  } catch (e) {
    console.error("Failed to save pricing", e);
    showToast("Failed to save pricing. Please try again.", "error");
  }
}

// ============================================================
// KNOWLEDGE BASE
// ============================================================

async function loadKnowledgeBase() {
  try {
    kbEntries = await apiFetch("/api/settings/knowledge-base");
    renderKBList(kbEntries);
  } catch (e) {
    console.warn("Failed to load knowledge base", e);
    document.getElementById("kb-list").innerHTML = `<div class="empty-state">Failed to load knowledge base</div>`;
  }
}

function renderKBList(entries) {
  const list = document.getElementById("kb-list");
  if (!entries || !entries.length) {
    list.innerHTML = `<div class="empty-state">No knowledge base entries yet. Add your first one above.</div>`;
    return;
  }

  list.innerHTML = entries.map(entry => `
    <div class="kb-entry" id="kb-entry-${escAttr(entry.id)}">
      <div class="kb-entry-display" id="kb-display-${escAttr(entry.id)}">
        <div class="kb-question"><strong>Q:</strong> ${escHtml(entry.question)}</div>
        <div class="kb-answer"><strong>A:</strong> ${escHtml(entry.answer)}</div>
        <div class="kb-entry-actions">
          <button class="btn-edit" onclick="editKBEntry('${escAttr(entry.id)}')">Edit</button>
          <button class="btn-danger" onclick="deleteKBEntry('${escAttr(entry.id)}')">Delete</button>
        </div>
      </div>
      <div class="kb-entry-edit hidden" id="kb-edit-${escAttr(entry.id)}">
        <input type="text" class="kb-input" id="kb-eq-${escAttr(entry.id)}" value="${escAttr(entry.question)}" />
        <textarea class="kb-textarea" id="kb-ea-${escAttr(entry.id)}" rows="3">${escHtml(entry.answer).replace(/<br>/g, '\n')}</textarea>
        <div class="kb-entry-actions">
          <button class="btn-save" onclick="saveKBEntry('${escAttr(entry.id)}')">Save</button>
          <button class="btn-cancel" onclick="cancelKBEdit('${escAttr(entry.id)}')">Cancel</button>
        </div>
      </div>
    </div>
  `).join("");
}

async function addKBEntry() {
  const qEl = document.getElementById("kb-new-question");
  const aEl = document.getElementById("kb-new-answer");
  const question = qEl.value.trim();
  const answer = aEl.value.trim();

  if (!question || !answer) {
    showToast("Please fill in both question and answer", "error");
    return;
  }

  try {
    const created = await apiFetch("/api/settings/knowledge-base", {
      method: "POST",
      body: JSON.stringify({ question, answer })
    });
    kbEntries.push(created);
    renderKBList(kbEntries);
    qEl.value = "";
    aEl.value = "";
    showToast("Knowledge base entry added!", "success");
  } catch (e) {
    console.error("Failed to add KB entry", e);
    showToast("Failed to add entry. Please try again.", "error");
  }
}

function editKBEntry(id) {
  const display = document.getElementById(`kb-display-${id}`);
  const edit = document.getElementById(`kb-edit-${id}`);
  if (display) display.classList.add("hidden");
  if (edit) edit.classList.remove("hidden");
}

async function saveKBEntry(id) {
  const question = document.getElementById(`kb-eq-${id}`).value.trim();
  const answer = document.getElementById(`kb-ea-${id}`).value.trim();

  if (!question || !answer) {
    showToast("Please fill in both question and answer", "error");
    return;
  }

  try {
    await apiFetch(`/api/settings/knowledge-base/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ question, answer })
    });
    // Update local data
    const entry = kbEntries.find(e => e.id === id);
    if (entry) { entry.question = question; entry.answer = answer; }
    renderKBList(kbEntries);
    showToast("Entry updated!", "success");
  } catch (e) {
    console.error("Failed to update KB entry", e);
    showToast("Failed to update entry. Please try again.", "error");
  }
}

function cancelKBEdit(id) {
  const display = document.getElementById(`kb-display-${id}`);
  const edit = document.getElementById(`kb-edit-${id}`);
  if (display) display.classList.remove("hidden");
  if (edit) edit.classList.add("hidden");
}

async function deleteKBEntry(id) {
  if (!confirm("Are you sure you want to delete this knowledge base entry?")) return;

  try {
    await apiFetch(`/api/settings/knowledge-base/${encodeURIComponent(id)}`, {
      method: "DELETE"
    });
    kbEntries = kbEntries.filter(e => e.id !== id);
    renderKBList(kbEntries);
    showToast("Entry deleted", "success");
  } catch (e) {
    console.error("Failed to delete KB entry", e);
    showToast("Failed to delete entry. Please try again.", "error");
  }
}

// ============================================================
// CUSTOM INSTRUCTIONS
// ============================================================

async function loadCustomInstructions() {
  try {
    const data = await apiFetch("/api/settings/custom-instructions");
    if (data.logo) document.getElementById("ci-logo").value = data.logo;
    if (data.packaging) document.getElementById("ci-packaging").value = data.packaging;
    if (data.website) document.getElementById("ci-website").value = data.website;
    if (data.general) document.getElementById("ci-general").value = data.general;
  } catch (e) {
    console.warn("Failed to load custom instructions", e);
    showToast("Failed to load custom instructions", "error");
  }
}

async function saveCustomInstructions() {
  const payload = {
    logo: document.getElementById("ci-logo").value,
    packaging: document.getElementById("ci-packaging").value,
    website: document.getElementById("ci-website").value,
    general: document.getElementById("ci-general").value
  };

  try {
    await apiFetch("/api/settings/custom-instructions", {
      method: "PUT",
      body: JSON.stringify(payload)
    });
    showToast("Custom instructions saved!", "success");
  } catch (e) {
    console.error("Failed to save custom instructions", e);
    showToast("Failed to save instructions. Please try again.", "error");
  }
}

// ============================================================
// TOAST NOTIFICATIONS
// ============================================================

function showToast(message, type = "success") {
  // Remove existing toast if any
  const existing = document.querySelector(".toast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);

  // Trigger animation
  requestAnimationFrame(() => toast.classList.add("show"));

  // Auto-hide after 3 seconds
  setTimeout(() => {
    toast.classList.remove("show");
    setTimeout(() => toast.remove(), 300);
  }, 3000);
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
