/* CogniSphere Labs — pipeline cockpit
   Talks to the FastAPI backend only through /api/*. No build step, no framework. */

const $ = (sel, root = document) => root.querySelector(sel);

const ORDER = [
  "script_writing",
  "video_generation",
  "video_editing",
  "ebook_writing",
  "lead_generation",
  "seo_agent",
];

const STATUS_LABEL = {
  idle: "IDLE",
  working: "RUNNING",
  orchestrating: "ORCHESTRATING",
  awaiting_modules: "BLOCKED",
  offline: "PENDING MODULE",
  online: "ONLINE",
  done: "DONE",
  error: "ERROR",
  planned: "PLANNED",
  running: "RUNNING",
  review: "REVIEW",
  completed: "COMPLETE",
  partial: "PARTIAL",
  blocked: "BLOCKED",
  failed: "FAILED",
  cancelled: "CANCELLED",
};

const ACTIVE_STATUSES = new Set(["working", "orchestrating", "queued", "running"]);

const state = {
  hub: null,
  agents: [],
  org: [],
  missions: [],
  settings: { configured: [], assignments: {} },
};

let lastSeq = 0;
let activeAgent = null;
let built = false;
let ticking = false;
let pipeRaf = null;
const nodeEls = new Map();

/* ---------------- helpers ---------------- */
async function fetchJSON(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`Bad response from ${url}`);
  }
  if (!res.ok) {
    const detail = (data && (data.detail || data.message)) || res.statusText;
    throw new Error(typeof detail === "string" ? detail : "Request failed");
  }
  return data;
}

function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", !!isError);
  el.classList.add("show");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove("show"), 3200);
}

function statusLabel(status) {
  return STATUS_LABEL[status] || String(status || "idle").toUpperCase();
}

function agentById(id) {
  if (id === "ceo_core") return state.hub;
  return state.agents.find((a) => a.id === id) || null;
}

function orgById(id) {
  return state.org.find((o) => o.id === id) || {};
}

function missionsActiveFor(agentId) {
  const mission = state.missions[state.missions.length - 1];
  if (!mission) return false;
  return (mission.tasks || []).some(
    (t) => t.agent === agentId && ["queued", "awaiting_module", "running"].includes(t.status)
  );
}

/* ---------------- nodes ---------------- */
function buildNodes() {
  if (built) return;
  ORDER.forEach((id, index) => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "node is-pending";
    el.dataset.id = id;
    el.innerHTML = `
      <span class="node-dot"></span>
      <span class="node-body">
        <span class="node-head">
          <span class="node-name"></span>
          <span class="badge">INIT</span>
        </span>
        <span class="node-role"></span>
        <span class="bar"><i></i></span>
        <span class="node-task">loading…</span>
      </span>`;
    el.addEventListener("click", () => openChat(id));
    const col = index < 3 ? $("#colLeft") : $("#colRight");
    col.appendChild(el);
    nodeEls.set(id, el);
  });
  built = true;
}

function updateNode(agent) {
  const el = nodeEls.get(agent.id);
  if (!el) return;
  const meta = orgById(agent.id);
  el.style.setProperty("--accent", agent.accent || "#39d3ff");
  $(".node-name", el).textContent = meta.name || agent.name;
  $(".node-role", el).textContent = meta.role || agent.role;

  const badge = $(".badge", el);
  const pending = agent.module_status === "pending";
  let badgeClass = "";
  if (pending) badgeClass = "pending";
  else if (agent.status === "working" || agent.status === "orchestrating") badgeClass = "working";
  else if (agent.status === "error") badgeClass = "error";
  badge.className = `badge ${badgeClass}`.trim();
  badge.textContent = statusLabel(agent.status);
  el.classList.toggle("is-pending", pending);
  el.classList.toggle(
    "is-working",
    ACTIVE_STATUSES.has(agent.status) || (!pending && missionsActiveFor(agent.id))
  );

  $(".bar i", el).style.width = `${agent.progress || 0}%`;
  $(".node-task", el).textContent = pending
    ? "module pending — Phase 2"
    : agent.current_task || "idle — no directive";

  const keyOk = agent.key && agent.key.configured;
  el.title = pending
    ? "Module not built yet — chat still records directives"
    : `${meta.role || ""} · ${keyOk ? `key: ${agent.key.provider}` : "no key assigned"}`;
}

function updateHub(hub) {
  if (!hub) return;
  const statusEl = $("#hubStatus");
  statusEl.textContent = statusLabel(hub.status);
  statusEl.className =
    "hub-status" +
    (hub.status === "awaiting_modules" ? " blocked" : hub.status === "idle" ? "" : " busy");
  $("#hubBar").style.width = `${hub.progress || 0}%`;
  $("#hubTask").textContent = hub.current_task || "awaiting directives";
  $("#hub").title = `CEO CORE · ${hub.status} · ${hub.live_departments || 0}/${hub.total_departments || 0} departments live`;
}

/* ---------------- pipes ---------------- */
function schedulePipes() {
  if (pipeRaf) return;
  pipeRaf = requestAnimationFrame(() => {
    pipeRaf = null;
    drawPipes();
  });
}

function drawPipes() {
  const stage = $("#stage");
  const svg = $("#pipes");
  const hubEl = $("#hub");
  if (!stage || !svg || !hubEl) return;

  const sr = stage.getBoundingClientRect();
  if (!sr.width || !sr.height) return;
  svg.setAttribute("viewBox", `0 0 ${sr.width} ${sr.height}`);

  while (svg.firstChild) svg.removeChild(svg.firstChild);

  const hubRect = hubEl.getBoundingClientRect();
  const hubCenterX = hubRect.left - sr.left + hubRect.width / 2;
  const hubY = hubRect.top - sr.top + hubRect.height / 2;

  nodeEls.forEach((el, id) => {
    const agent = agentById(id);
    if (!agent) return;
    const r = el.getBoundingClientRect();
    if (!r.width) return;

    const onLeft = r.left - sr.left + r.width / 2 < hubCenterX;
    const x1 = onLeft ? hubRect.left - sr.left : hubRect.left - sr.left + hubRect.width;
    const y1 = hubY;
    const x2 = onLeft ? r.right - sr.left : r.left - sr.left;
    const y2 = r.top - sr.top + r.height / 2;
    const midX = (x1 + x2) / 2;
    const d = `M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`;
    const accent = agent.accent || "#39d3ff";
    const active = ACTIVE_STATUSES.has(agent.status) || missionsActiveFor(id);

    const base = document.createElementNS("http://www.w3.org/2000/svg", "path");
    base.setAttribute("d", d);
    base.setAttribute("class", "pipe-base");
    base.setAttribute("stroke", accent);
    svg.appendChild(base);

    const flow = document.createElementNS("http://www.w3.org/2000/svg", "path");
    flow.setAttribute("d", d);
    flow.setAttribute("class", `pipe-flow${active ? " active" : ""}`);
    flow.setAttribute("stroke", accent);
    flow.style.color = accent;
    svg.appendChild(flow);
  });
}

/* ---------------- rendering ---------------- */
function updateStats() {
  const hub = state.hub || {};
  $("#liveCount").textContent = `${hub.live_departments ?? 0}/${hub.total_departments ?? ORDER.length}`;
  $("#keyCount").textContent = String((state.settings.configured || []).length);
  $("#missionCount").textContent = String((state.missions || []).length);
}

function appendLogs(entries) {
  if (!entries || !entries.length) return;
  const container = $("#logs");
  const fresh = entries.filter((e) => e.seq > lastSeq).sort((a, b) => a.seq - b.seq);
  if (!fresh.length) return;
  const stick = container.scrollTop + container.clientHeight >= container.scrollHeight - 40;
  fresh.forEach((entry) => {
    lastSeq = Math.max(lastSeq, entry.seq);
    const line = document.createElement("div");
    line.className = `log-line ${entry.level || "info"}`;
    const time = new Date((entry.at || Date.now() / 1000) * 1000);
    const hhmmss = time.toTimeString().slice(0, 8);
    line.innerHTML = `<span class="log-time">${hhmmss}</span>
      <span class="log-src">${escapeHtml(entry.source)}</span>
      <span class="log-msg">${escapeHtml(entry.message)}</span>`;
    container.appendChild(line);
  });
  while (container.children.length > 300) container.removeChild(container.firstChild);
  if (stick) container.scrollTop = container.scrollHeight;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderMissions() {
  const box = $("#missions");
  const missions = state.missions || [];
  if (!missions.length) {
    box.innerHTML = `<p class="empty">No missions yet. Open CEO CORE and send a directive.</p>`;
    return;
  }
  box.innerHTML = "";
  [...missions].reverse().forEach((mission) => {
    const card = document.createElement("div");
    card.className = "mission";
    const chips = (mission.tasks || [])
      .map((task) => {
        const label = task.agent === "compliance" ? "COMPLIANCE" : task.agent.toUpperCase();
        const short = label.length > 16 ? label.slice(0, 15) + "…" : label;
        const detail = task.error || task.note || task.label;
        return `<span class="chip ${task.status}" title="${escapeHtml(detail)}">${escapeHtml(short)}</span>`;
      })
      .join("");
    const statusText =
      {
        planned: "PLANNED",
        in_progress: "RUNNING",
        completed: "COMPLETE",
        partial: "PARTIAL",
        blocked: "BLOCKED",
        failed: "FAILED",
        cancelled: "CANCELLED",
      }[mission.status] || String(mission.approval || "").toUpperCase();
    const badgeClass =
      {
        in_progress: "running",
        completed: "working",
        partial: "error",
        blocked: "error",
        failed: "error",
      }[mission.status] || "pending";
    const progress = Math.max(0, Math.min(100, Number(mission.progress || 0)));
    card.innerHTML = `
      <div class="mission-top">
        <span class="mission-id">${escapeHtml(mission.id)}</span>
        <span class="badge ${badgeClass}">${escapeHtml(statusText)}</span>
      </div>
      <p class="mission-obj">${escapeHtml(mission.objective)}</p>
      <div class="task-chips">${chips}</div>
      <div class="mission-bar"><i style="width:${progress}%"></i></div>`;
    box.appendChild(card);
  });
}

function applyState(data) {
  if (!data) return;
  state.hub = data.hub || state.hub;
  state.agents = data.agents || [];
  state.org = data.org || [];
  state.missions = data.missions || [];
  state.settings = data.settings || state.settings;

  buildNodes();
  if (state.hub) updateHub(state.hub);
  state.agents.forEach(updateNode);
  updateStats();
  appendLogs(data.logs);
  renderMissions();
  schedulePipes();
  if (activeAgent) refreshDrawerMeta();
}

/* ---------------- polling ---------------- */
async function tick(initial = false) {
  if (ticking) return;
  ticking = true;
  try {
    const data = await fetchJSON(`/api/state?since_log=${initial ? 0 : lastSeq}`);
    applyState(data);
  } catch (err) {
    // keep the UI alive; surface once in the monitor
    appendLogs([
      {
        seq: Date.now(),
        level: "error",
        source: "ui",
        message: `backend unreachable: ${err.message}`,
        at: Date.now() / 1000,
      },
    ]);
  } finally {
    ticking = false;
  }
}

/* ---------------- chat drawer ---------------- */
function refreshDrawerMeta() {
  const agent = agentById(activeAgent);
  if (!agent) return;
  const keyText = agent.key && agent.key.configured
    ? `KEY · ${agent.key.provider}`
    : "KEY · not set";
  const meta = $("#drawerMeta");
  const quick = activeAgent === "ceo_core"
    ? `<span class="chip" data-quick="Executive status report">status report</span>
       <span class="chip" data-quick="Produce a 3-part video series about autonomous AI agents">sample directive</span>`
    : "";
  meta.innerHTML = `
    <span class="chip ${agent.module_status === "pending" ? "awaiting_module" : "queued"}">
      ${escapeHtml(statusLabel(agent.status))}
    </span>
    <span class="chip">${escapeHtml(keyText)}</span>
    ${agent.module_status === "pending" ? `<span class="chip awaiting_module">module pending</span>` : ""}
    ${quick}`;
  meta.querySelectorAll("[data-quick]").forEach((chip) => {
    chip.addEventListener("click", () => {
      $("#chatInput").value = chip.dataset.quick;
      $("#chatInput").focus();
    });
  });
}

function renderChat(messages) {
  const log = $("#chatLog");
  log.innerHTML = "";
  if (!messages || !messages.length) {
    const agent = agentById(activeAgent);
    const hint = document.createElement("div");
    hint.className = "msg agent";
    hint.textContent =
      activeAgent === "ceo_core"
        ? "CEO CORE online. Send a strategic directive and I will break it into department tasks, or ask for a status report."
        : `${agent ? agent.name : "Agent"} ready. Send a direct instruction — context here is isolated from every other agent.`;
    log.appendChild(hint);
    return;
  }
  messages.forEach((msg) => appendMsg(msg.role, displayName(msg.role), msg.text, false));
  log.scrollTop = log.scrollHeight;
}

function displayName(role) {
  if (role === "user") return "YOU";
  const agent = agentById(activeAgent);
  return agent ? agent.name : "AGENT";
}

function appendMsg(role, label, text, scroll = true) {
  const log = $("#chatLog");
  const el = document.createElement("div");
  el.className = `msg ${role === "user" ? "user" : "agent"}`;
  el.innerHTML = `<span class="msg-label">${escapeHtml(label)}</span>${escapeHtml(text)}`;
  log.appendChild(el);
  if (scroll) log.scrollTop = log.scrollHeight;
}

async function openChat(id) {
  activeAgent = id;
  const agent = agentById(id);
  if (!agent) return;
  const drawer = $("#drawer");
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  $("#scrim").classList.add("open");
  $("#drawerAccent").style.background = agent.accent || "#34e0ff";
  $("#drawerAccent").style.color = agent.accent || "#34e0ff";
  $("#drawerName").textContent = agent.name;
  $("#drawerRole").textContent = agent.role;
  refreshDrawerMeta();
  $("#chatLog").innerHTML = `<div class="msg pending">SYNCING CONTEXT…</div>`;
  try {
    const data = await fetchJSON(`/api/agents/${id}/chat`);
    renderChat(data.messages);
  } catch (err) {
    $("#chatLog").innerHTML = `<div class="msg agent">Could not load history: ${escapeHtml(err.message)}</div>`;
  }
  $("#chatInput").focus();
}

function closeChat() {
  activeAgent = null;
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").classList.remove("open");
}

async function sendMessage(event) {
  if (event) event.preventDefault();
  const input = $("#chatInput");
  const text = input.value.trim();
  if (!text || !activeAgent) return;
  const agentId = activeAgent;
  const agent = agentById(agentId);
  const btn = $("#sendBtn");
  btn.disabled = true;
  appendMsg("user", "YOU", text);
  input.value = "";
  try {
    const res = await fetchJSON(`/api/agents/${agentId}/chat`, {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    appendMsg("agent", agent ? agent.name : "AGENT", res.reply);
    if (res.state) {
      res.state.logs = res.state.logs || [];
      applyState(res.state);
    }
    if (agentId === "ceo_core") setTimeout(() => tick(), 400);
  } catch (err) {
    appendMsg("agent", "SYSTEM", `Request failed: ${err.message}`);
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    input.focus();
  }
}

/* ---------------- settings ---------------- */
async function openSettings() {
  $("#settings").classList.add("open");
  $("#settings").setAttribute("aria-hidden", "false");
  await refreshSettings();
}

function closeSettings() {
  $("#settings").classList.remove("open");
  $("#settings").setAttribute("aria-hidden", "true");
}

async function refreshSettings() {
  try {
    const data = await fetchJSON("/api/settings");
    state.settings = {
      configured: data.configured || [],
      assignments: data.assignments || {},
    };
    renderProviders(data.providers || []);
    renderAssignments(data);
    $("#storePath").textContent = data.store || "";
    updateStats();
  } catch (err) {
    toast(`Settings unavailable: ${err.message}`, true);
  }
}

function renderProviders(providers) {
  const box = $("#providerList");
  box.innerHTML = "";
  providers.forEach((p) => {
    const row = document.createElement("div");
    row.className = "provider";
    row.innerHTML = `
      <div class="provider-name">
        <strong>${escapeHtml(p.label)}
          <em class="provider-status ${p.configured ? "" : "missing"}">${p.configured ? "● set" : "○ missing"}</em>
        </strong>
        <span>${escapeHtml(p.env)} · ${p.configured ? escapeHtml(p.masked) : escapeHtml(p.category)}</span>
      </div>
      <input class="key-input" type="password" autocomplete="off" spellcheck="false"
             placeholder="${p.configured ? "replace stored key…" : "paste key…"}"
             aria-label="${escapeHtml(p.label)} API key" />
      <div class="key-actions">
        <button class="btn tiny primary" data-save type="button">Save</button>
        <button class="btn tiny ghost" data-clear type="button" ${p.configured ? "" : "disabled"}>Clear</button>
      </div>`;

    const input = $(".key-input", row);
    $("[data-save]", row).addEventListener("click", async () => {
      const value = input.value.trim();
      if (!value) {
        toast("Paste a key first", true);
        return;
      }
      try {
        const res = await fetchJSON("/api/settings/keys", {
          method: "POST",
          body: JSON.stringify({ provider: p.slug, value }),
        });
        toast(`${p.label} saved${res.result.warnings.length ? " — " + res.result.warnings[0] : ""}`);
        await refreshSettings();
        tick();
      } catch (err) {
        toast(err.message, true);
      }
    });

    $("[data-clear]", row).addEventListener("click", async () => {
      try {
        await fetchJSON(`/api/settings/keys/${p.slug}`, { method: "DELETE" });
        toast(`${p.label} removed`);
        await refreshSettings();
        tick();
      } catch (err) {
        toast(err.message, true);
      }
    });

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        $("[data-save]", row).click();
      }
    });

    box.appendChild(row);
  });
}

function renderAssignments(data) {
  const box = $("#assignmentList");
  box.innerHTML = "";
  const agents = [
    { id: "ceo_core", name: "CEO CORE" },
    ...state.agents.map((a) => ({ id: a.id, name: a.name })),
  ];
  const providers = data.providers || [];
  agents.forEach((agent) => {
    const row = document.createElement("div");
    row.className = "assignment";
    const current = (data.assignments || {})[agent.id] || "";
    const options = providers
      .map((p) => {
        const suffix = p.configured ? "" : " (no key)";
        return `<option value="${p.slug}" ${p.slug === current ? "selected" : ""}>${escapeHtml(
          p.label + suffix
        )}</option>`;
      })
      .join("");
    row.innerHTML = `<label for="assign-${agent.id}">${escapeHtml(agent.name)}</label>
      <select id="assign-${agent.id}">${options}</select>`;
    const select = $("select", row);
    select.addEventListener("change", async () => {
      try {
        await fetchJSON("/api/settings/assignments", {
          method: "POST",
          body: JSON.stringify({ agent_id: agent.id, provider: select.value }),
        });
        toast(`${agent.name} → ${select.value}`);
        tick();
      } catch (err) {
        toast(err.message, true);
      }
    });
    box.appendChild(row);
  });
}

/* ---------------- reset ---------------- */
async function resetSession() {
  if (!window.confirm("Clear all chats, missions and monitor logs? API keys are kept.")) return;
  try {
    await fetchJSON("/api/system/reset", { method: "POST" });
    lastSeq = 0;
    $("#logs").innerHTML = "";
    $("#missions").innerHTML = "";
    if (activeAgent) renderChat([]);
    await tick(true);
    toast("Session reset — keys preserved");
  } catch (err) {
    toast(err.message, true);
  }
}

/* ---------------- wiring ---------------- */
function wire() {
  $("#hub").addEventListener("click", () => openChat("ceo_core"));
  $("#closeDrawer").addEventListener("click", closeChat);
  $("#scrim").addEventListener("click", closeChat);
  $("#chatForm").addEventListener("submit", sendMessage);
  $("#chatInput").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      sendMessage();
    }
  });
  $("#settingsBtn").addEventListener("click", openSettings);
  $("#closeSettings").addEventListener("click", closeSettings);
  $("#doneSettings").addEventListener("click", closeSettings);
  $("#resetBtn").addEventListener("click", resetSession);
  $("#settings").addEventListener("click", (e) => {
    if (e.target === $("#settings")) closeSettings();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if ($("#settings").classList.contains("open")) closeSettings();
    else if (activeAgent) closeChat();
  });

  const ro = new ResizeObserver(() => schedulePipes());
  ro.observe($("#stage"));
  window.addEventListener("resize", schedulePipes);
  window.addEventListener("load", schedulePipes);
}

let pollTimer = null;

function schedulePoll() {
  clearTimeout(pollTimer);
  const busy = state.hub && ["orchestrating", "working"].includes(state.hub.status);
  pollTimer = setTimeout(async () => {
    await tick();
    schedulePoll();
  }, busy ? 500 : 1200);
}

wire();
tick(true).then(() => schedulePoll());
