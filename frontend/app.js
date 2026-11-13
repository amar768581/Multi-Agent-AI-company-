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

const GOLD = { bright: "#FFD700", deep: "#F59E0B", metal: "#D4AF37", pale: "#FFE9A8" };

const DEPTS = [
  { id: "script_writing", name: "SCRIPT WRITING", color: "#38BDF8", icon: "✎", bay: '<span class="bay-paper"></span>' },
  { id: "video_generation", name: "VIDEO GENERATION", color: "#F97316", icon: "◉", bay: '<span class="bay-frame"></span><span class="bay-frame"></span><span class="bay-frame"></span>' },
  { id: "video_editing", name: "VIDEO EDITING", color: "#14B8A6", icon: "▶", bay: '<span class="bay-clip"></span><span class="bay-timeline"></span>' },
  { id: "ebook_writing", name: "E-BOOK WRITING", color: "#A855F7", icon: "▤", bay: '<span class="bay-scroll"></span>' },
  { id: "lead_generation", name: "LEAD GENERATION", color: "#22C55E", icon: "▽", bay: '<span class="bay-avatars"><i></i><i></i><i></i><i></i><i></i></span>' },
  { id: "seo_agent", name: "SEO", color: "#EAB308", icon: "⌕", bay: '<span class="bay-chart"><i></i><i></i><i></i><i></i><i></i></span>' },
];

function deptColor(id) {
  const dept = DEPTS.find((item) => item.id === id);
  return dept ? dept.color : GOLD.bright;
}

function deptName(id) {
  const dept = DEPTS.find((item) => item.id === id);
  return dept ? dept.name : String(id || "").toUpperCase();
}

function agentAccent(id) {
  return deptColor(id);
}

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
  settings: { configured: [], assignments: {}, agent_keys: { counts: {}, total: 0 } },
  providerCatalog: [],
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

function pushToast({ title = "SYSTEM", message = "", agentId = null, accent = GOLD.bright, error = false }) {
  const stack = $("#toastStack");
  if (!stack) return;
  while (stack.children.length >= 5) stack.removeChild(stack.firstChild);
  const card = document.createElement("div");
  card.className = `toast-card${error ? " error" : ""}${agentId ? " clickable" : ""}`;
  card.style.setProperty("--toast-accent", error ? "#ff7a6b" : accent);
  const time = new Date().toTimeString().slice(0, 8);
  card.innerHTML = `
    <div class="toast-head">
      <span class="toast-dot"></span>
      <span class="toast-title">${escapeHtml(title)}</span>
      <span class="toast-time">${time}</span>
    </div>
    <p class="toast-msg">${escapeHtml(message)}</p>`;
  const dismiss = () => {
    if (!card.isConnected) return;
    card.classList.add("leaving");
    setTimeout(() => card.remove(), 320);
  };
  if (agentId) card.addEventListener("click", () => { openChat(agentId); dismiss(); });
  stack.appendChild(card);
  setTimeout(dismiss, 6000);
}

function toast(message, isError = false) {
  pushToast({ title: isError ? "SYSTEM ERROR" : "SYSTEM", message, error: !!isError });
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
  const wrap = $("#towers");
  if (!wrap) return;
  DEPTS.forEach((dept) => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "tower";
    el.dataset.id = dept.id;
    el.style.setProperty("--dept", dept.color);
    el.innerHTML = `
      <span class="tower-emblem">${dept.icon}</span>
      <span class="tower-nameplate">${dept.name}</span>
      <span class="tower-status">INIT</span>
      <span class="tower-bay">${dept.bay}</span>
      <span class="tower-bar"><i></i></span>
      <span class="tower-task">loading…</span>`;
    el.addEventListener("click", () => openChat(dept.id));
    wrap.appendChild(el);
    nodeEls.set(dept.id, el);
  });
  built = true;
}

function updateNode(agent) {
  const el = nodeEls.get(agent.id);
  if (!el) return;
  const meta = orgById(agent.id);
  el.style.setProperty("--dept", deptColor(agent.id));
  $(".tower-nameplate", el).textContent = deptName(agent.id);
  $(".tower-status", el).textContent = `${statusLabel(agent.status)} · ${meta.role || agent.role || ""}`;

  const pending = agent.module_status === "pending";
  el.classList.toggle(
    "is-working",
    ACTIVE_STATUSES.has(agent.status) || (!pending && missionsActiveFor(agent.id))
  );

  $(".tower-bar i", el).style.width = `${agent.progress || 0}%`;
  $(".tower-task", el).textContent = pending
    ? "module pending"
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
    "orb-status" +
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

let pipeLayoutKey = "";
const pipeEls = new Map();

function svgEl(name, attrs = {}) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
  return el;
}

function pipeActive(agent, id) {
  return ACTIVE_STATUSES.has(agent.status) || missionsActiveFor(id);
}

function elbowPath(x1, y1, x2, y2, midY) {
  const r = 14;
  const dx = Math.sign(x2 - x1) || 1;
  const dy2 = Math.sign(y2 - midY) || 1;
  const dy1 = Math.sign(midY - y1) || 1;
  return [
    `M ${x1} ${y1}`,
    `L ${x1} ${midY - r * dy1}`,
    `Q ${x1} ${midY} ${x1 + r * dx} ${midY}`,
    `L ${x2 - r * dx} ${midY}`,
    `Q ${x2} ${midY} ${x2} ${midY + r * dy2}`,
    `L ${x2} ${y2}`,
  ].join(" ");
}

function drawPipes() {
  const stage = $("#stage");
  const svg = $("#pipes");
  const hubEl = $("#hub");
  if (!stage || !svg || !hubEl) return;

  const sr = stage.getBoundingClientRect();
  if (!sr.width || !sr.height) return;
  svg.setAttribute("viewBox", `0 0 ${sr.width} ${sr.height}`);

  const orb = hubEl.getBoundingClientRect();
  const sphere = $(".orb-sphere", hubEl);
  const sphereRect = sphere ? sphere.getBoundingClientRect() : orb;
  const orbX = orb.left - sr.left + orb.width / 2;
  const orbBottom = sphereRect.bottom - sr.top - 4;

  const pipes = [];
  nodeEls.forEach((el, id) => {
    const agent = agentById(id);
    const rect = el.getBoundingClientRect();
    if (!agent || !rect.width) return;
    const color = deptColor(id);
    const active = pipeActive(agent, id);
    const topX = rect.left - sr.left + rect.width / 2;
    const topY = rect.top - sr.top;
    pipes.push({
      key: `in-${id}`,
      d: elbowPath(orbX, orbBottom, topX, topY, orbBottom + (topY - orbBottom) * 0.45),
      color,
      active,
    });

    const bottomX = rect.left - sr.left + rect.width / 2;
    const bottomY = rect.bottom - sr.top - 2;
    const subs = Array.from(document.querySelectorAll(`.iso-sub[data-parent="${id}"]`));
    if (["video_editing", "ebook_writing", "lead_generation"].includes(id)) {
      const monitor = $("#monitorSub");
      if (monitor && !subs.includes(monitor)) subs.push(monitor);
    }
    subs.forEach((sub, index) => {
      const subRect = sub.getBoundingClientRect();
      if (!subRect.width) return;
      const subX = subRect.left - sr.left + subRect.width / 2;
      const subY = subRect.top - sr.top;
      pipes.push({
        key: `out-${id}-${index}`,
        d: elbowPath(bottomX, bottomY, subX, subY, bottomY + Math.max(20, (subY - bottomY) * 0.5)),
        color,
        active,
        thin: true,
      });
    });
  });

  const monitor = $("#monitorSub");
  const sensor = $("#sensorNode");
  if (monitor && sensor) {
    const mr = monitor.getBoundingClientRect();
    const sr2 = sensor.getBoundingClientRect();
    const editing = agentById("video_editing");
    if (mr.width && sr2.width) {
      pipes.push({
        key: "sensor",
        d: elbowPath(
          mr.left - sr.left + mr.width / 2,
          mr.top - sr.top,
          sr2.left - sr.left,
          sr2.bottom - sr.top,
          sr2.bottom - sr.top + 18
        ),
        color: "#38BDF8",
        active: editing ? pipeActive(editing, "video_editing") : false,
        thin: true,
      });
    }
  }

  const layoutKey = pipes.map((pipe) => `${pipe.d}|${pipe.color}`).join("~");
  if (layoutKey !== pipeLayoutKey) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    pipeEls.clear();

    const defs = svgEl("defs");
    const glow = svgEl("filter", { id: "pipeGlow", x: "-80%", y: "-80%", width: "260%", height: "260%" });
    glow.appendChild(svgEl("feGaussianBlur", { stdDeviation: "2.2", result: "blur" }));
    const merge = svgEl("feMerge");
    merge.appendChild(svgEl("feMergeNode", { in: "blur" }));
    merge.appendChild(svgEl("feMergeNode", { in: "SourceGraphic" }));
    glow.appendChild(merge);
    defs.appendChild(glow);
    svg.appendChild(defs);

    pipes.forEach((pipe) => {
      const pathId = `pipe-path-${pipe.key}`;
      const glass = svgEl("path", {
        id: pathId,
        d: pipe.d,
        fill: "none",
        stroke: pipe.color,
        "stroke-width": pipe.thin ? 9 : 13,
        "stroke-linecap": "round",
        opacity: pipe.active ? 0.16 : 0.09,
      });
      const shine = svgEl("path", {
        d: pipe.d,
        fill: "none",
        stroke: "#ffffff",
        "stroke-width": pipe.thin ? 3 : 4,
        "stroke-linecap": "round",
        opacity: 0.06,
      });
      const core = svgEl("path", {
        d: pipe.d,
        fill: "none",
        stroke: pipe.color,
        "stroke-width": pipe.thin ? 1.4 : 2,
        "stroke-linecap": "round",
        opacity: 0.4,
      });
      const flow = svgEl("path", {
        d: pipe.d,
        class: `pipe-flow${pipe.active ? " active" : ""}`,
        stroke: pipe.color,
      });
      flow.style.color = pipe.color;
      svg.appendChild(glass);
      svg.appendChild(shine);
      svg.appendChild(core);
      svg.appendChild(flow);

      const packets = [];
      const count = pipe.thin ? 1 : 2;
      for (let i = 0; i < count; i += 1) {
        const group = svgEl("g", { class: "pipe-packet" });
        const halo = svgEl("circle", {
          r: pipe.thin ? "5" : "7",
          fill: pipe.color,
          opacity: pipe.active ? "0.25" : "0.08",
          filter: "url(#pipeGlow)",
        });
        const dot = svgEl("circle", {
          r: pipe.thin ? "2" : "2.8",
          fill: "#fffdf2",
          opacity: pipe.active ? "1" : "0.3",
        });
        const motion = svgEl("animateMotion", {
          dur: pipe.active ? "2.2s" : "7.5s",
          begin: `${-i * 1.5}s`,
          repeatCount: "indefinite",
          rotate: "auto",
        });
        motion.appendChild(svgEl("mpath", { href: `#${pathId}` }));
        dot.appendChild(motion);
        group.appendChild(halo);
        group.appendChild(dot);
        svg.appendChild(group);
        packets.push({ halo, dot, motion });
      }
      pipeEls.set(pipe.key, { glass, core, flow, packets });
    });
    pipeLayoutKey = layoutKey;
  } else {
    pipes.forEach((pipe) => {
      const entry = pipeEls.get(pipe.key);
      if (!entry) return;
      entry.glass.setAttribute("stroke", pipe.color);
      entry.glass.setAttribute("opacity", pipe.active ? "0.16" : "0.09");
      entry.core.setAttribute("stroke", pipe.color);
      entry.flow.setAttribute("stroke", pipe.color);
      entry.flow.style.color = pipe.color;
      entry.flow.setAttribute("class", `pipe-flow${pipe.active ? " active" : ""}`);
      entry.packets.forEach((packet) => {
        packet.halo.setAttribute("fill", pipe.color);
        packet.halo.setAttribute("opacity", pipe.active ? "0.25" : "0.08");
        packet.dot.setAttribute("opacity", pipe.active ? "1" : "0.3");
        packet.motion.setAttribute("dur", pipe.active ? "2.2s" : "7.5s");
      });
    });
  }
}

/* ---------------- 4K preview studio ---------------- */
const PREVIEW_RES = { "2160": [3840, 2160], "1440": [2560, 1440], "1080": [1920, 1080] };
let previewOpen = false;

function shortText(value, max) {
  const text = String(value || "");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function pvColor(status, pending = false) {
  if (pending) return "#8a7554";
  if (status === "error" || status === "failed") return "#ff7a6b";
  if (status === "working" || status === "orchestrating" || status === "running") return GOLD.bright;
  if (["done", "completed", "review", "online", "partial"].includes(status)) return GOLD.deep;
  if (status === "awaiting_modules" || status === "blocked") return "#FFC94D";
  return "#a08a5f";
}

function pvCard(card) {
  const { id, agent, meta, x, y, w, h } = card;
  const pending = agent.module_status === "pending";
  const color = pvColor(agent.status, pending);
  const pct = Math.max(0, Math.min(100, Number(agent.progress || 0)));
  const name = escapeHtml(shortText(meta.name || agent.name || id, 30));
  const role = escapeHtml(shortText(meta.role || agent.role || "", 54));
  const task = escapeHtml(shortText(agent.current_task || (pending ? "module pending" : "idle — no directive"), 60));
  const label = escapeHtml(statusLabel(agent.status));
  return `
    <g>
      <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="28" fill="#120f09" stroke="#3a2f1a" stroke-width="2"/>
      <rect id="pvEdge-${id}" x="${x}" y="${y + 24}" width="8" height="${h - 48}" rx="4" fill="${color}"/>
      <text x="${x + 48}" y="${y + 82}" fill="#fff6dc" font-size="40" font-weight="700" letter-spacing="2">${name}</text>
      <text x="${x + 48}" y="${y + 128}" fill="#bfa878" font-size="24">${role}</text>
      <rect x="${x + w - 276}" y="${y + 44}" width="236" height="50" rx="25" fill="${color}" fill-opacity="0.12" stroke="${color}" stroke-opacity="0.5" stroke-width="2"/>
      <circle id="pvDot-${id}" cx="${x + w - 244}" cy="${y + 69}" r="7" fill="${color}"/>
      <text id="pvStatus-${id}" x="${x + w - 222}" y="${y + 78}" fill="${color}" font-size="21" font-family="ui-monospace, monospace" letter-spacing="2">${label}</text>
      <text id="pvTask-${id}" x="${x + 48}" y="${y + 200}" fill="#b3a077" font-size="22" font-family="ui-monospace, monospace">${task}</text>
      <rect x="${x + 48}" y="${y + 240}" width="${w - 96}" height="10" rx="5" fill="#2a2213"/>
      <rect id="pvBar-${id}" x="${x + 48}" y="${y + 240}" width="${((w - 96) * pct) / 100}" height="10" rx="5" fill="${color}"/>
    </g>`;
}

function pvPipe(card) {
  const { id, agent, x, y, h } = card;
  const color = pvColor(agent.status, agent.module_status === "pending");
  const active = ACTIVE_STATUSES.has(agent.status) || missionsActiveFor(id);
  const left = card.side === "left";
  const x1 = left ? 1646 : 2194;
  const y1 = 1130;
  const x2 = left ? 1020 : 2820;
  const y2 = y + h / 2;
  const c1 = left ? 1400 : 2440;
  const c2 = left ? 1220 : 2620;
  const d = `M ${x1} ${y1} C ${c1} ${y1}, ${c2} ${y2}, ${x2} ${y2}`;
  const pathId = `pv-path-${id}`;
  const flow = active
    ? `<path d="${d}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round" stroke-dasharray="18 34" opacity="0.9">
         <animate attributeName="stroke-dashoffset" values="0;-104" dur="1.7s" repeatCount="indefinite"/>
       </path>`
    : `<path d="${d}" fill="none" stroke="${color}" stroke-width="4" stroke-linecap="round" stroke-dasharray="6 22" opacity="0.16"/>`;
  const packets = active
    ? [0, 1]
        .map(
          (i) => `
        <circle r="8" fill="${color}" opacity="0.22" filter="url(#pvGlow)">
          <animateMotion dur="2.6s" begin="${-i * 1.3}s" repeatCount="indefinite"><mpath href="#${pathId}"/></animateMotion>
        </circle>
        <circle r="4" fill="#f2fdff" opacity="0.95">
          <animateMotion dur="2.6s" begin="${-i * 1.3}s" repeatCount="indefinite"><mpath href="#${pathId}"/></animateMotion>
        </circle>`
        )
        .join("")
    : "";
  return `
    <path id="${pathId}" d="${d}" fill="none" stroke="${color}" stroke-width="2" opacity="0.2"/>
    ${flow}
    <g id="pvPackets-${id}" opacity="${active ? 1 : 0.3}">${packets}</g>`;
}

function buildPreviewSvg() {
  const hub = state.hub || {};
  const hubColor = pvColor(hub.status);
  const hubPct = Math.max(0, Math.min(100, Number(hub.progress || 0)));
  const live = hub.live_departments ?? 0;
  const total = hub.total_departments ?? DEPTS.length;
  const centers = [430, 1026, 1622, 2218, 2814, 3410];
  const towerY = 820;
  const towerW = 380;
  const towerH = 600;
  const stamp = new Date().toLocaleString("en-GB", { hour12: false });

  const towerGroups = DEPTS.map((dept, index) => {
    const agent = agentById(dept.id) || { id: dept.id, status: "idle" };
    const color = pvColor(agent.status, agent.module_status === "pending");
    const pct = Math.max(0, Math.min(100, Number(agent.progress || 0)));
    const cx = centers[index];
    const x = cx - towerW / 2;
    const task = escapeHtml(shortText(agent.current_task || "idle — no directive", 44));
    const label = escapeHtml(statusLabel(agent.status));
    let bay = "";
    if (dept.id === "script_writing") {
      bay = `<rect x="${cx - 70}" y="1060" width="140" height="170" rx="4" fill="#f2f7ff" opacity="0.92"/>
        ${[0, 1, 2, 3, 4, 5].map((i) => `<rect x="${cx - 54}" y="${1082 + i * 22}" width="${100 - (i % 3) * 14}" height="5" rx="2" fill="#8fa3c0"/>`).join("")}`;
    } else if (dept.id === "video_generation") {
      bay = [0, 1, 2].map((i) => `<rect x="${cx - 100}" y="${1050 + i * 54}" width="200" height="42" rx="4" fill="#3a1c08" stroke="#F97316" stroke-opacity="0.55"/>`).join("");
    } else if (dept.id === "video_editing") {
      bay = `<rect x="${cx - 120}" y="1100" width="240" height="8" rx="4" fill="#0f2a28"/>
        <rect x="${cx - 120}" y="1100" width="150" height="8" rx="4" fill="#14B8A6"/>
        ${[0, 1, 2].map((i) => `<rect x="${cx - 120}" y="${1130 + i * 26}" width="${220 - i * 45}" height="10" rx="3" fill="#14B8A6" opacity="${0.85 - i * 0.2}"/>`).join("")}`;
    } else if (dept.id === "ebook_writing") {
      bay = `<rect x="${cx - 56}" y="1030" width="112" height="240" rx="8" fill="#2a1140" stroke="#A855F7" stroke-opacity="0.7"/>
        ${[0, 1, 2, 3, 4, 5].map((i) => `<rect x="${cx - 38}" y="${1052 + i * 30}" width="${76 - (i % 2) * 18}" height="5" rx="2" fill="#d8b4fe" opacity="0.8"/>`).join("")}`;
    } else if (dept.id === "lead_generation") {
      bay = [0, 1, 2, 3, 4].map((i) => {
        const ax = cx + [-70, 0, 70, -35, 35][i];
        const ay = 1160 + [-40, -55, -30, 40, 55][i];
        return `<circle cx="${ax}" cy="${ay}" r="28" fill="#0a2614" stroke="#22C55E" stroke-opacity="0.7"/>
          <circle cx="${ax}" cy="${ay - 4}" r="8" fill="#22C55E" opacity="0.9"/>
          <path d="M ${ax - 14} ${ay + 16} Q ${ax} ${ay - 2} ${ax + 14} ${ay + 16}" fill="#22C55E" opacity="0.55"/>`;
      }).join("");
    } else {
      const heights = [70, 120, 95, 165, 135];
      bay = heights.map((h, i) => `<rect x="${cx - 100 + i * 42}" y="${1250 - h}" width="30" height="${h}" rx="4" fill="#EAB308" opacity="${0.55 + i * 0.1}"/>`).join("");
    }
    return `
    <g>
      <path id="pv-path-in-${dept.id}" d="${elbowPath(1920, 690, cx, towerY, 760)}" fill="none" stroke="${dept.color}" stroke-width="14" stroke-linecap="round" opacity="0.13"/>
      <path d="${elbowPath(1920, 690, cx, towerY, 760)}" fill="none" stroke="${dept.color}" stroke-width="2" stroke-linecap="round" opacity="0.5"/>
      <rect x="${x}" y="${towerY}" width="${towerW}" height="${towerH}" rx="22" fill="#0b0d10" stroke="${dept.color}" stroke-opacity="0.55" stroke-width="3"/>
      <rect x="${x + 14}" y="${towerY + 14}" width="${towerW - 28}" height="86" rx="14" fill="${dept.color}" fill-opacity="0.12"/>
      <circle cx="${cx}" cy="${towerY + 58}" r="42" fill="${dept.color}" fill-opacity="0.2" stroke="${dept.color}" stroke-opacity="0.7" stroke-width="2"/>
      <text x="${cx}" y="${towerY + 71}" text-anchor="middle" fill="#fff8e6" font-size="34" font-family="'Segoe UI Symbol', system-ui, sans-serif">${dept.icon}</text>
      <text x="${cx}" y="${towerY + 146}" text-anchor="middle" fill="#ffffff" font-size="27" font-weight="700" letter-spacing="2" font-family="'Segoe UI', system-ui, sans-serif">${dept.name}</text>
      <rect x="${x + 36}" y="${towerY + 176}" width="${towerW - 72}" height="280" rx="10" fill="#050708" stroke="${dept.color}" stroke-opacity="0.35"/>
      ${bay}
      <rect x="${x + 36}" y="${towerY + 484}" width="${towerW - 72}" height="44" rx="22" fill="${color}" fill-opacity="0.12" stroke="${color}" stroke-opacity="0.55"/>
      <circle id="pvDot-${dept.id}" cx="${x + 62}" cy="${towerY + 506}" r="7" fill="${color}"/>
      <text id="pvStatus-${dept.id}" x="${x + 80}" y="${towerY + 514}" fill="${color}" font-size="19" font-family="ui-monospace, monospace" letter-spacing="1.5">${label}</text>
      <text id="pvTask-${dept.id}" x="${cx}" y="${towerY + 556}" text-anchor="middle" fill="#b3a077" font-size="17" font-family="ui-monospace, monospace">${task}</text>
      <rect x="${x + 40}" y="${towerY + 572}" width="${towerW - 80}" height="8" rx="4" fill="#241d10"/>
      <rect id="pvBar-${dept.id}" x="${x + 40}" y="${towerY + 572}" width="${((towerW - 80) * pct) / 100}" height="8" rx="4" fill="${color}"/>
      <rect id="pvEdge-${dept.id}" x="${x}" y="${towerY + 40}" width="6" height="${towerH - 80}" rx="3" fill="${color}" opacity="0.5"/>
    </g>`;
  }).join("");

  const subs = [
    { label: "Plot Planner", parent: "script_writing", cx: 280 },
    { label: "Dialogue Agent", parent: "script_writing", cx: 560 },
    { label: "Asset Creator", parent: "video_generation", cx: 900 },
    { label: "Animator", parent: "video_generation", cx: 1180 },
    { label: "MONITORING / LOGGING", parent: "video_editing", cx: 1720, wide: true },
    { label: "Keyword Research", parent: "seo_agent", cx: 2920 },
    { label: "On-Page Opt", parent: "seo_agent", cx: 3200 },
  ];
  const subGroups = subs.map((sub) => {
    const dept = DEPTS.find((d) => d.id === sub.parent) || DEPTS[0];
    const agent = agentById(sub.parent) || {};
    const active = ACTIVE_STATUSES.has(agent.status) || missionsActiveFor(sub.parent);
    const tower = centers[DEPTS.findIndex((d) => d.id === sub.parent)] || 1920;
    const w = sub.wide ? 380 : 240;
    const x = sub.cx - w / 2;
    const y = 1790;
    const path = elbowPath(tower, towerY + towerH - 2, sub.cx, y, 1640);
    return `
    <g>
      <path id="pv-path-out-${sub.label.replace(/[^a-z]/gi, "")}" d="${path}" fill="none" stroke="${dept.color}" stroke-width="9" stroke-linecap="round" opacity="0.12"/>
      <path d="${path}" fill="none" stroke="${dept.color}" stroke-width="1.6" stroke-linecap="round" opacity="0.45"/>
      ${active ? `<circle r="5" fill="${dept.color}"><animateMotion dur="2.6s" repeatCount="indefinite"><mpath href="#pv-path-out-${sub.label.replace(/[^a-z]/gi, "")}"/></animateMotion></circle>` : ""}
      <rect x="${x}" y="${y}" width="${w}" height="130" rx="12" fill="#0c0d0f" stroke="#D4AF37" stroke-opacity="0.3" stroke-width="2"/>
      <text x="${sub.cx}" y="${y + 52}" text-anchor="middle" fill="#FFD700" font-size="26" font-family="'Segoe UI Symbol', system-ui, sans-serif">◎</text>
      <text x="${sub.cx}" y="${y + 96}" text-anchor="middle" fill="#e6dcc4" font-size="${sub.wide ? 22 : 19}" font-family="ui-monospace, monospace">${sub.label}</text>
    </g>`;
  }).join("");

  return `<svg xmlns="http://www.w3.org/2000/svg" id="previewSvg" viewBox="0 0 3840 2160" role="img" aria-label="CogniSphere Labs 3D isometric workflow">
  <defs>
    <radialGradient id="pvBg" cx="50%" cy="42%" r="76%">
      <stop offset="0" stop-color="#171208"/>
      <stop offset="0.6" stop-color="#080705"/>
      <stop offset="1" stop-color="#030302"/>
    </radialGradient>
    <radialGradient id="pvHubGlow" cx="42%" cy="36%" r="62%">
      <stop offset="0" stop-color="#fffbe8"/>
      <stop offset="0.28" stop-color="#ffe27a"/>
      <stop offset="0.62" stop-color="#e0a916"/>
      <stop offset="1" stop-color="#6b4c08"/>
    </radialGradient>
    <linearGradient id="pvTitle" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#fffdf4"/>
      <stop offset="0.45" stop-color="#ffe9a8"/>
      <stop offset="1" stop-color="#9a7624"/>
    </linearGradient>
    <filter id="pvGlow" x="-80%" y="-80%" width="260%" height="260%">
      <feGaussianBlur stdDeviation="7" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <rect width="3840" height="2160" fill="url(#pvBg)"/>
  ${Array.from({ length: 26 }, (_, i) => `<line x1="${i * 160}" y1="0" x2="${i * 160 - 240}" y2="2160" stroke="#D4AF37" stroke-opacity="0.045"/>`).join("")}
  ${Array.from({ length: 14 }, (_, i) => `<line x1="0" y1="${i * 160}" x2="3840" y2="${i * 160 + 120}" stroke="#D4AF37" stroke-opacity="0.035"/>`).join("")}

  <text x="1920" y="150" text-anchor="middle" fill="url(#pvTitle)" font-family="'Segoe UI', system-ui, sans-serif" font-size="72" font-weight="800" letter-spacing="6">MULTI-AGENT AI COMPANY</text>
  <text x="1920" y="230" text-anchor="middle" fill="url(#pvTitle)" font-family="'Segoe UI', system-ui, sans-serif" font-size="44" font-weight="700" letter-spacing="14">ORGANIZATIONAL WORKFLOW</text>

  <polygon points="240,540 3600,540 3840,2010 0,2010" fill="#0d0c0a" stroke="#D4AF37" stroke-width="5" stroke-opacity="0.75"/>
  <polygon points="300,580 3540,580 3730,1970 110,1970" fill="none" stroke="#D4AF37" stroke-opacity="0.18" stroke-width="2"/>
  <rect x="300" y="580" width="3240" height="1390" fill="none" stroke="#FFD700" stroke-opacity="0.05"/>

  <circle cx="1920" cy="650" r="240" fill="none" stroke="#FFD700" stroke-opacity="0.18" stroke-width="3"/>
  <ellipse cx="1920" cy="830" rx="330" ry="70" fill="#171205" stroke="#D4AF37" stroke-opacity="0.55" stroke-width="4"/>
  <circle cx="1920" cy="640" r="150" fill="url(#pvHubGlow)" stroke="#ffe27a" stroke-opacity="0.8" stroke-width="3" filter="url(#pvGlow)"/>
  <text x="1920" y="628" text-anchor="middle" fill="#3a2a04" font-family="'Segoe UI', system-ui, sans-serif" font-size="54" font-weight="800" letter-spacing="4">CEO</text>
  <text x="1920" y="676" text-anchor="middle" fill="#4a3608" font-family="ui-monospace, monospace" font-size="26" letter-spacing="8">CORE</text>

  ${towerGroups}
  ${subGroups}

  <rect x="3260" y="690" width="300" height="58" rx="29" fill="#05131b" stroke="#38BDF8" stroke-opacity="0.5" stroke-width="2"/>
  <circle cx="3292" cy="719" r="8" fill="#38BDF8"/>
  <text x="3312" y="728" fill="#bfe8ff" font-size="17" font-family="ui-monospace, monospace" letter-spacing="1">MONITORING/LOGGING</text>

  <rect x="1600" y="1580" width="640" height="80" rx="40" fill="#0a0906" stroke="#D4AF37" stroke-opacity="0.35"/>
  <text id="pvHubStatus" x="1920" y="1615" text-anchor="middle" fill="${hubColor}" font-family="ui-monospace, monospace" font-size="24" letter-spacing="3">${escapeHtml(statusLabel(hub.status))}</text>
  <text id="pvHubTask" x="1920" y="1645" text-anchor="middle" fill="#b3a077" font-family="ui-monospace, monospace" font-size="19">${escapeHtml(shortText(hub.current_task || "awaiting directives", 54))}</text>
  <rect x="1580" y="1675" width="680" height="10" rx="5" fill="#241d10"/>
  <rect id="pvHubBar" x="1580" y="1675" width="${(680 * hubPct) / 100}" height="10" rx="5" fill="${hubColor}"/>

  <text x="120" y="2110" fill="#8a7a5c" font-family="ui-monospace, monospace" font-size="19">${live} / ${total} DEPARTMENTS LIVE</text>
  <text x="3720" y="2110" text-anchor="end" fill="#6f6044" font-family="ui-monospace, monospace" font-size="17">${escapeHtml(stamp)} · 4K ISOMETRIC PIPELINE</text>
</svg>`;
}

function updatePreview() {
  if (!previewOpen) return;
  const root = $("#previewStage");
  const hub = state.hub || {};
  if (!root || !root.firstElementChild) return;
  const color = pvColor(hub.status);
  const status = root.querySelector("#pvHubStatus");
  if (status) {
    status.textContent = statusLabel(hub.status);
    status.setAttribute("fill", color);
  }
  const bar = root.querySelector("#pvHubBar");
  if (bar) {
    const pct = Math.max(0, Math.min(100, Number(hub.progress || 0)));
    bar.setAttribute("width", String((680 * pct) / 100));
    bar.setAttribute("fill", color);
  }
  const task = root.querySelector("#pvHubTask");
  if (task) task.textContent = shortText(hub.current_task || "awaiting directives", 48);

  ORDER.forEach((id) => {
    const agent = agentById(id);
    if (!agent) return;
    const deptColor = pvColor(agent.status, agent.module_status === "pending");
    const pct = Math.max(0, Math.min(100, Number(agent.progress || 0)));
    const label = root.querySelector(`#pvStatus-${id}`);
    if (label) {
      label.textContent = statusLabel(agent.status);
      label.setAttribute("fill", deptColor);
    }
    const dot = root.querySelector(`#pvDot-${id}`);
    if (dot) dot.setAttribute("fill", deptColor);
    const edge = root.querySelector(`#pvEdge-${id}`);
    if (edge) edge.setAttribute("fill", deptColor);
    const row = root.querySelector(`#pvBar-${id}`);
    if (row) {
      row.setAttribute("width", String((300 * pct) / 100));
      row.setAttribute("fill", deptColor);
    }
    const taskEl = root.querySelector(`#pvTask-${id}`);
    if (taskEl) taskEl.textContent = shortText(agent.current_task || "idle — no directive", 60);
    const flow = root.querySelector(`#pvPackets-${id}`);
    if (flow) flow.setAttribute("opacity", ACTIVE_STATUSES.has(agent.status) || missionsActiveFor(id) ? "1" : "0.3");
  });
  $("#previewMeta").textContent = `${PREVIEW_RES[$("#previewRes").value][0]} × ${PREVIEW_RES[$("#previewRes").value][1]} · vector render · live state`;
}

function openPreview() {
  previewOpen = true;
  $("#preview").classList.add("open");
  $("#preview").setAttribute("aria-hidden", "false");
  $("#previewStage").innerHTML = buildPreviewSvg();
  updatePreview();
}

function closePreview() {
  previewOpen = false;
  $("#preview").classList.remove("open");
  $("#preview").setAttribute("aria-hidden", "true");
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
}

async function downloadPreview() {
  const svg = $("#previewSvg");
  if (!svg) return;
  const [w, h] = PREVIEW_RES[$("#previewRes").value] || PREVIEW_RES["2160"];
  try {
    const clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", String(w));
    clone.setAttribute("height", String(h));
    const xml = new XMLSerializer().serializeToString(clone);
    const url = URL.createObjectURL(new Blob([xml], { type: "image/svg+xml;charset=utf-8" }));
    const img = new Image();
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error("SVG rasterise failed"));
      img.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#050810";
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0, w, h);
    URL.revokeObjectURL(url);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("PNG encode failed");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `cognisphere-pipeline-${w}x${h}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast(`Exported ${w} × ${h} PNG`);
  } catch (err) {
    toast(`Export failed: ${err.message}`, true);
  }
}

function togglePreviewFullscreen() {
  const el = $("#preview");
  if (!document.fullscreenElement) {
    if (el.requestFullscreen) el.requestFullscreen().catch(() => toast("Fullscreen blocked", true));
    else toast("Fullscreen not supported here", true);
  } else {
    document.exitFullscreen?.();
  }
}

/* ---------------- rendering ---------------- */
function updateStats() {
  const hub = state.hub || {};
  $("#liveCount").textContent = `${hub.live_departments ?? 0}/${hub.total_departments ?? ORDER.length}`;
  const agentKeyTotal = (state.settings.agent_keys && state.settings.agent_keys.total) || 0;
  $("#keyCount").textContent = String((state.settings.configured || []).length + agentKeyTotal);
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

let notificationsReady = false;

function notifyChanges(prevAgents, prevMission, logs) {
  if (!notificationsReady) return;
  const mission = state.missions[state.missions.length - 1];
  if (mission) {
    if (!prevMission || prevMission.id !== mission.id) {
      pushToast({
        title: "CEO CORE",
        message: `Mission ${mission.id} planned — approval required before execution.`,
        agentId: "ceo_core",
      });
    } else if (prevMission.status !== mission.status) {
      if (mission.status === "in_progress") {
        pushToast({
          title: "CEO CORE",
          message: `${mission.id} started — delegating ${(mission.tasks || []).length} step(s).`,
          agentId: "ceo_core",
        });
      } else if (["completed", "partial", "blocked", "failed"].includes(mission.status)) {
        pushToast({
          title: "CEO CORE",
          message: `${mission.id} ${mission.status} — delivery report ready in the CEO chat.`,
          agentId: "ceo_core",
          error: ["blocked", "failed"].includes(mission.status),
        });
      }
    }
    const prevRunning = new Set(
      ((prevMission && prevMission.tasks) || []).filter((task) => task.status === "running").map((task) => task.agent)
    );
    (mission.tasks || []).forEach((task) => {
      if (task.status === "running" && !prevRunning.has(task.agent)) {
        pushToast({
          title: "DELEGATION",
          message: `CEO CORE → ${deptName(task.agent)}: ${task.label || "task started"}`,
          agentId: task.agent === "compliance" ? "ceo_core" : task.agent,
          accent: deptColor(task.agent),
        });
      }
    });
  }

  state.agents.forEach((agent) => {
    const prev = prevAgents.get(agent.id);
    if (prev === agent.status) return;
    const color = deptColor(agent.id);
    const name = deptName(agent.id);
    if (agent.status === "working") {
      pushToast({ title: name, message: agent.current_task || "Now processing a directive.", agentId: agent.id, accent: color });
    } else if (["done", "review"].includes(agent.status)) {
      pushToast({ title: name, message: "Deliverable ready for review.", agentId: agent.id, accent: color });
    } else if (agent.status === "error") {
      pushToast({ title: name, message: "Agent hit an error — open the chat for details.", agentId: agent.id, error: true });
    } else if (agent.status === "offline" || agent.module_status === "pending") {
      pushToast({ title: name, message: "Offline fallback engaged — deterministic template in use.", agentId: agent.id, accent: color });
    }
  });

  (logs || []).forEach((entry) => {
    const message = String(entry.message || "");
    if (!/key/i.test(message)) return;
    if (!/added|removed|active key|fallback|failed|rotat/i.test(message)) return;
    const agentId = DEPTS.some((dept) => dept.id === entry.source)
      ? entry.source
      : entry.source === "ceo_core"
        ? "ceo_core"
        : null;
    pushToast({
      title: /fallback|failed/i.test(message) ? "KEY FALLBACK" : "API KEY",
      message,
      agentId,
      accent: GOLD.metal,
      error: /failed|error/i.test(message),
    });
  });
}

function applyState(data) {
  if (!data) return;
  const prevAgents = new Map((state.agents || []).map((agent) => [agent.id, agent.status]));
  const prevMission = (state.missions || [])[state.missions.length - 1];

  state.hub = data.hub || state.hub;
  state.agents = data.agents || [];
  state.org = data.org || [];
  state.missions = data.missions || [];
  state.settings = data.settings || state.settings;

  if (window.WorkflowScene) window.WorkflowScene.setState(state.agents, state.hub);
  updateStats();
  appendLogs(data.logs);
  renderMissions();
  schedulePipes();
  notifyChanges(prevAgents, prevMission, data.logs);
  notificationsReady = true;
  if (previewOpen) updatePreview();
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
  const pool = agentKeys && agentKeys.agent_id === activeAgent ? agentKeys.count : 0;
  const keyText = agent.key && agent.key.configured
    ? `KEY · ${agent.key.provider}${pool > 1 ? ` ×${pool}` : ""}`
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
  refreshAgentKeys(id);
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
  agentKeys = null;
  editingKeyId = null;
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

/* ---------------- per-agent key manager ---------------- */
const AGENT_PROVIDER_SLUGS = ["gemini", "groq", "openai", "heygen", "elevenlabs"];
const PROVIDER_LABELS = {
  gemini: "Gemini",
  groq: "Groq",
  openai: "OpenAI",
  heygen: "HeyGen",
  elevenlabs: "ElevenLabs",
};
let agentKeys = null;
let editingKeyId = null;

function providerLabel(slug) {
  const found = (state.providerCatalog || []).find((p) => p.slug === slug);
  return (found && found.label) || PROVIDER_LABELS[slug] || slug || "—";
}

function agentLabel(id) {
  const agent = agentById(id);
  return agent ? agent.name : id;
}

function renderAgentProviderOptions() {
  const select = $("#agentProvider");
  const current = (agentKeys && agentKeys.provider) || select.value;
  select.innerHTML = AGENT_PROVIDER_SLUGS.map(
    (slug) =>
      `<option value="${slug}" ${slug === current ? "selected" : ""}>${escapeHtml(providerLabel(slug))}</option>`
  ).join("");
  if (current && !AGENT_PROVIDER_SLUGS.includes(current)) {
    select.insertAdjacentHTML(
      "afterbegin",
      `<option value="${escapeHtml(current)}" selected>${escapeHtml(providerLabel(current))}</option>`
    );
  }
}

async function refreshAgentKeys(agentId = activeAgent) {
  if (!agentId) return;
  try {
    const data = await fetchJSON(`/api/agents/${agentId}/keys`);
    if (activeAgent !== agentId) return;
    agentKeys = data;
    editingKeyId = null;
    renderAgentKeys();
  } catch (err) {
    toast(`Keys unavailable: ${err.message}`, true);
  }
}

function renderAgentKeys() {
  if (!agentKeys) return;
  $("#agentKeyCount").textContent = `${agentKeys.count}/${agentKeys.max_keys || 8}`;
  renderAgentProviderOptions();
  const list = $("#agentKeyList");
  list.innerHTML = "";
  if (!agentKeys.keys.length) {
    list.innerHTML = `<li class="agent-key-empty">No key for this agent yet — a global key, if configured, is used as fallback.</li>`;
    return;
  }
  agentKeys.keys.forEach((key) => {
    const row = document.createElement("li");
    row.className = `agent-key${key.active ? " is-active" : ""}`;
    row.dataset.id = key.id;
    if (editingKeyId === key.id) {
      row.innerHTML = `
        <div class="agent-key-edit">
          <input class="key-input" type="password" autocomplete="off" spellcheck="false"
                 placeholder="new key value…" aria-label="New key value" />
          <button class="btn tiny primary" data-act="save" type="button">Save</button>
          <button class="btn tiny ghost" data-act="cancel" type="button">Cancel</button>
        </div>`;
    } else {
      row.innerHTML = `
        <div class="agent-key-top">
          <span class="agent-key-provider">${escapeHtml(providerLabel(key.provider))}</span>
          <span class="agent-key-mask">${escapeHtml(key.masked || "••••")}</span>
          ${key.active ? `<span class="chip done">ACTIVE</span>` : ""}
        </div>
        <div class="agent-key-actions">
          ${key.active ? "" : `<button class="btn tiny ghost" data-act="use" type="button">Use</button>`}
          <button class="btn tiny ghost" data-act="edit" type="button">Edit</button>
          <button class="btn tiny ghost" data-act="delete" type="button">Delete</button>
        </div>`;
    }
    list.appendChild(row);
  });
  const editing = list.querySelector(".agent-key-edit input");
  if (editing) editing.focus();
}

async function onAgentKeyAction(event) {
  const button = event.target.closest("[data-act]");
  if (!button || !activeAgent) return;
  const row = button.closest(".agent-key");
  const keyId = row && row.dataset.id;
  const act = button.dataset.act;
  if (act === "edit") {
    editingKeyId = keyId;
    renderAgentKeys();
    return;
  }
  if (act === "cancel") {
    editingKeyId = null;
    renderAgentKeys();
    return;
  }
  if (act === "delete" && !window.confirm("Delete this API key from the agent's pool?")) return;
  try {
    if (act === "save") {
      const input = row.querySelector("input");
      const value = input ? input.value.trim() : "";
      if (!value) {
        toast("Paste the new key value first", true);
        return;
      }
      agentKeys = await fetchJSON(`/api/agents/${activeAgent}/keys/${keyId}`, {
        method: "PUT",
        body: JSON.stringify({ value }),
      });
      toast("Key updated");
    } else if (act === "use") {
      agentKeys = await fetchJSON(`/api/agents/${activeAgent}/keys/${keyId}/activate`, {
        method: "POST",
      });
      const activeKey = agentKeys.keys.find((k) => k.active) || {};
      toast(`Active key → ${providerLabel(activeKey.provider)}`);
    } else if (act === "delete") {
      agentKeys = await fetchJSON(`/api/agents/${activeAgent}/keys/${keyId}`, { method: "DELETE" });
      toast("Key removed");
    }
    editingKeyId = null;
    renderAgentKeys();
    refreshDrawerMeta();
    tick();
  } catch (err) {
    toast(err.message, true);
  }
}

async function addAgentKey(event) {
  event.preventDefault();
  if (!activeAgent) return;
  const input = $("#agentKeyValue");
  const value = input.value.trim();
  const provider = $("#agentProvider").value;
  if (!value) {
    toast("Paste a key first", true);
    return;
  }
  try {
    const res = await fetchJSON(`/api/agents/${activeAgent}/keys`, {
      method: "POST",
      body: JSON.stringify({ provider, value }),
    });
    agentKeys = res.state;
    input.value = "";
    renderAgentKeys();
    refreshDrawerMeta();
    toast(
      `${providerLabel(provider)} key added${res.warnings && res.warnings.length ? " — " + res.warnings[0] : ""}`
    );
    tick();
  } catch (err) {
    toast(err.message, true);
  }
}

async function changeAgentProvider() {
  if (!activeAgent) return;
  const select = $("#agentProvider");
  try {
    await fetchJSON("/api/settings/assignments", {
      method: "POST",
      body: JSON.stringify({ agent_id: activeAgent, provider: select.value }),
    });
    toast(`${agentLabel(activeAgent)} → ${providerLabel(select.value)}`);
    await refreshAgentKeys(activeAgent);
    tick();
  } catch (err) {
    toast(err.message, true);
  }
}

function toggleAgentKeys() {
  const collapsed = $("#agentKeys").classList.toggle("collapsed");
  $("#toggleAgentKeys").textContent = collapsed ? "show" : "hide";
  $("#toggleAgentKeys").setAttribute("aria-expanded", String(!collapsed));
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
      agent_keys: data.agent_keys || { counts: {}, total: 0 },
    };
    state.providerCatalog = data.providers || [];
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

/* ---------------- golden animated background ---------------- */
function startBackgroundFx() {
  const canvas = $("#bgfx");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const sprite = document.createElement("canvas");
  sprite.width = sprite.height = 64;
  const sctx = sprite.getContext("2d");
  const glow = sctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  glow.addColorStop(0, "rgba(255, 232, 150, 0.9)");
  glow.addColorStop(0.35, "rgba(255, 215, 0, 0.32)");
  glow.addColorStop(1, "rgba(255, 215, 0, 0)");
  sctx.fillStyle = glow;
  sctx.fillRect(0, 0, 64, 64);

  let width = 0;
  let height = 0;
  let particles = [];
  let circuits = [];
  let mesh = [];
  let last = 0;
  let raf = 0;
  let running = false;

  function buildCircuits() {
    const list = [];
    const count = width < 720 ? 2 : 4;
    for (let i = 0; i < count; i += 1) {
      const points = [];
      let x = -90;
      let y = height * (0.16 + i * 0.21) + Math.random() * 40;
      points.push([x, y]);
      while (x < width + 90) {
        x += 90 + Math.random() * 160;
        if (Math.random() > 0.45) y += (Math.random() > 0.5 ? 1 : -1) * (28 + Math.random() * 52);
        points.push([x, y]);
      }
      list.push({
        points,
        speed: 55 + Math.random() * 45,
        offset: Math.random() * 1400,
        alpha: 0.05 + Math.random() * 0.04,
      });
    }
    return list;
  }

  function buildMesh() {
    const count = width < 720 ? 18 : 36;
    return Array.from({ length: count }, () => ({
      x: Math.random() * width,
      y: Math.random() * height,
      phase: Math.random() * Math.PI * 2,
    }));
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const count = Math.min(80, Math.max(30, Math.round((width * height) / 28000)));
    particles = Array.from({ length: count }, () => ({
      x: Math.random() * width,
      y: Math.random() * height,
      r: 0.8 + Math.random() * 2.2,
      vy: 6 + Math.random() * 16,
      drift: 4 + Math.random() * 10,
      phase: Math.random() * Math.PI * 2,
    }));
    circuits = buildCircuits();
    mesh = buildMesh();
  }

  function drawGrid(t) {
    const links = 230;
    for (let i = 0; i < mesh.length; i += 1) {
      const from = mesh[i];
      from.x += Math.cos(t * 0.00018 + from.phase) * 0.05;
      from.y += Math.sin(t * 0.00022 + from.phase) * 0.05;
      for (let j = i + 1; j < mesh.length; j += 1) {
        const to = mesh[j];
        const dist = Math.hypot(from.x - to.x, from.y - to.y);
        if (dist > links) continue;
        ctx.strokeStyle = `rgba(212, 175, 55, ${0.14 * (1 - dist / links)})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(from.x, from.y);
        ctx.lineTo(to.x, to.y);
        ctx.stroke();
      }
      const pulse = 0.45 + Math.sin(t * 0.001 + from.phase) * 0.25;
      ctx.fillStyle = `rgba(255, 215, 0, ${pulse})`;
      ctx.beginPath();
      ctx.arc(from.x, from.y, 1.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function drawCircuit(circuit, t) {
    const points = circuit.points;
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i += 1) ctx.lineTo(points[i][0], points[i][1]);
    ctx.strokeStyle = `rgba(212, 175, 55, ${circuit.alpha})`;
    ctx.lineWidth = 1;
    ctx.stroke();

    const lengths = [];
    let total = 0;
    for (let i = 1; i < points.length; i += 1) {
      const len = Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]) || 1;
      lengths.push(len);
      total += len;
    }
    const pos = ((t * 0.001 * circuit.speed + circuit.offset) % (total + 240)) - 120;
    if (pos < 0) return;
    let travelled = 0;
    for (let i = 0; i < lengths.length; i += 1) {
      if (travelled + lengths[i] >= pos) {
        const f = (pos - travelled) / lengths[i];
        const x = points[i][0] + (points[i + 1][0] - points[i][0]) * f;
        const y = points[i][1] + (points[i + 1][1] - points[i][1]) * f;
        ctx.drawImage(sprite, x - 14, y - 14, 28, 28);
        ctx.fillStyle = "rgba(255, 235, 160, 0.85)";
        ctx.beginPath();
        ctx.arc(x, y, 1.8, 0, Math.PI * 2);
        ctx.fill();
        break;
      }
      travelled += lengths[i];
    }
  }

  function render(t) {
    ctx.clearRect(0, 0, width, height);
    drawGrid(t);
    circuits.forEach((circuit) => drawCircuit(circuit, t));
    particles.forEach((particle) => {
      particle.x += Math.sin(t * 0.0006 + particle.phase) * particle.drift * 0.016;
      particle.y -= particle.vy * 0.016;
      if (particle.y < -12) {
        particle.y = height + 12;
        particle.x = Math.random() * width;
      }
      ctx.drawImage(sprite, particle.x - particle.r * 6, particle.y - particle.r * 6, particle.r * 12, particle.r * 12);
    });
  }

  function frame(t) {
    if (!running) return;
    raf = requestAnimationFrame(frame);
    if (document.hidden) return;
    if (t - last < 32) return;
    last = t;
    render(t);
  }

  function start() {
    if (running) return;
    running = true;
    last = 0;
    raf = requestAnimationFrame(frame);
  }

  function stop() {
    running = false;
    cancelAnimationFrame(raf);
  }

  resize();
  if (reduce) {
    render(0);
    return;
  }
  start();
  window.addEventListener("resize", () => {
    resize();
    render(performance.now());
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else start();
  });
}

/* ---------------- wiring ---------------- */
function wire() {
  const hubButton = $("#hub");
  if (hubButton) hubButton.addEventListener("click", () => openChat("ceo_core"));
  $("#closeDrawer").addEventListener("click", closeChat);
  $("#scrim").addEventListener("click", closeChat);
  $("#chatForm").addEventListener("submit", sendMessage);
  $("#agentKeyForm").addEventListener("submit", addAgentKey);
  $("#toggleAgentKeys").addEventListener("click", toggleAgentKeys);
  $("#agentProvider").addEventListener("change", changeAgentProvider);
  $("#agentKeyList").addEventListener("click", onAgentKeyAction);
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
  $("#previewBtn").addEventListener("click", openPreview);
  $("#closePreview").addEventListener("click", closePreview);
  $("#previewRes").addEventListener("change", updatePreview);
  $("#previewDownload").addEventListener("click", downloadPreview);
  $("#previewFull").addEventListener("click", togglePreviewFullscreen);
  $("#settings").addEventListener("click", (e) => {
    if (e.target === $("#settings")) closeSettings();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (previewOpen) closePreview();
    else if ($("#settings").classList.contains("open")) closeSettings();
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

/* ---------------- canvas 4K preview (overrides the SVG studio) ---------------- */
let previewLoop = null;

function drawPreviewFrame() {
  const target = $("#previewCanvas");
  const source = window.WorkflowScene && window.WorkflowScene.getCanvas();
  if (!target || !source) return;
  const ctx2 = target.getContext("2d");
  ctx2.drawImage(source, 0, 0, target.width, target.height);
}

function openPreview() {
  if (previewOpen) return;
  previewOpen = true;
  const modal = $("#preview");
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  const target = $("#previewCanvas");
  if (target) {
    target.width = 1920;
    target.height = 1080;
    drawPreviewFrame();
  }
  const loop = () => {
    if (!previewOpen) return;
    drawPreviewFrame();
    previewLoop = requestAnimationFrame(loop);
  };
  previewLoop = requestAnimationFrame(loop);
}

function closePreview() {
  previewOpen = false;
  if (previewLoop) cancelAnimationFrame(previewLoop);
  previewLoop = null;
  $("#preview").classList.remove("open");
  $("#preview").setAttribute("aria-hidden", "true");
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
}

function updatePreview() {
  /* the studio mirrors the live canvas via its own animation loop */
}

async function downloadPreview() {
  if (!window.WorkflowScene) return;
  const [w, h] = PREVIEW_RES[$("#previewRes").value] || PREVIEW_RES["2160"];
  try {
    const canvas2 = document.createElement("canvas");
    window.WorkflowScene.renderTo(canvas2, w / 1920, performance.now() / 1000);
    const blob = await new Promise((resolve) => canvas2.toBlob(resolve, "image/png"));
    if (!blob) throw new Error("PNG encode failed");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `cognisphere-workflow-${w}x${h}.png`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 4000);
    toast(`Exported ${w} × ${h} PNG`);
  } catch (err) {
    toast(`Export failed: ${err.message}`, true);
  }
}

function togglePreviewFullscreen() {
  const el = $("#preview");
  if (!document.fullscreenElement) {
    if (el.requestFullscreen) el.requestFullscreen().catch(() => toast("Fullscreen blocked", true));
    else toast("Fullscreen not supported here", true);
  } else {
    document.exitFullscreen?.();
  }
}
