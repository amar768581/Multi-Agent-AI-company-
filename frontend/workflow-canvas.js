/* CogniSphere Labs — 3D isometric workflow canvas renderer.
   Visual engine adapted from the reference canvas implementation; state,
   hit-testing and preview/export hooks are wired to the live dashboard. */

(() => {
  const canvas = document.getElementById("workflowCanvas");
  if (!canvas) return;
  // Slightly reduced backing store keeps the 60 FPS feel while CSS upscales crisply.
  canvas.width = 960;
  canvas.height = 540;
  const LIVE_SCALE = canvas.width / 1920;
  const ctx = canvas.getContext("2d", { alpha: false });

  const staticCanvas = document.createElement("canvas");
  staticCanvas.width = 1920;
  staticCanvas.height = 1080;
  const sctx = staticCanvas.getContext("2d");

  const W = 1920;
  const H = 1080;

  let flowSpeed = 1.2;
  let isPaused = false;
  let hoveredNode = null;
  let pulseBursts = [];
  let agentState = {};
  let hubState = {};

  const MACHINE_AGENT = {
    script: "script_writing",
    vidgen: "video_generation",
    videdit: "video_editing",
    ebook: "ebook_writing",
    leadgen: "lead_generation",
    seo: "seo_agent",
  };
  const AGENT_MACHINE = Object.fromEntries(Object.entries(MACHINE_AGENT).map(([k, v]) => [v, k]));

  const PALETTE = {
    blue: { main: "#38b6ff", light: "#a6e3ff", dark: "#164e78", glow: "rgba(56, 182, 255, 0.75)", bodyTop: "#4da6db", bodyFront: "#387fb2", bodySide: "#235378" },
    orange: { main: "#ff9442", light: "#ffd4a6", dark: "#7c3a13", glow: "rgba(255, 148, 66, 0.75)", bodyTop: "#d47c42", bodyFront: "#b35c27", bodySide: "#783914" },
    teal: { main: "#3be8d0", light: "#b8fff3", dark: "#125e57", glow: "rgba(59, 232, 208, 0.75)", bodyTop: "#3da9a4", bodyFront: "#29827e", bodySide: "#195452" },
    purple: { main: "#c56bff", light: "#f0ccff", dark: "#4f1f75", glow: "rgba(197, 107, 255, 0.75)", bodyTop: "#9364c4", bodyFront: "#6f429e", bodySide: "#49276e" },
    green: { main: "#4ce870", light: "#c4ffd1", dark: "#196129", glow: "rgba(76, 232, 112, 0.75)", bodyTop: "#56b365", bodyFront: "#398a47", bodySide: "#21592b" },
    gold: { main: "#ffd24c", light: "#fff2b3", dark: "#785910", glow: "rgba(255, 210, 76, 0.75)", bodyTop: "#dbb146", bodyFront: "#b58924", bodySide: "#785710" },
  };

  const MACHINES = [
    { id: "script", name: "SCRIPT WRITING", x: 265, y: 540, colorKey: "blue", icon: "pen", desc: "Automated narrative structure, screenplay formatting & scene scripting engine." },
    { id: "vidgen", name: "VIDEO GENERATION", x: 590, y: 540, colorKey: "orange", icon: "cam", desc: "Neural frame synthesis, 3D scene rendering & raw motion sequence generator." },
    { id: "videdit", name: "VIDEO EDITING", x: 925, y: 540, colorKey: "teal", icon: "play", desc: "Multi-track non-linear AI assembly, color grading, audio sync & cuts." },
    { id: "ebook", name: "E-BOOK WRITING", x: 1225, y: 540, colorKey: "purple", icon: "book", desc: "Long-form manuscript compilation, chapter layout & semantic publishing." },
    { id: "leadgen", name: "LEAD GENERATION", x: 1475, y: 540, colorKey: "green", icon: "funnel", desc: "Prospect graph mining, qualification funnel & automated outreach scoring." },
    { id: "seo", name: "SEO", x: 1715, y: 540, colorKey: "gold", icon: "search", desc: "SERP ranking optimization, semantic keyword clustering & organic growth." },
  ];

  const SUB_AGENTS = [
    { id: "plot", label: "Plot Planner", x: 115, y: 895, colorKey: "blue", parent: "script", type: "printer" },
    { id: "dialogue", label: "Dialogue Agent", x: 315, y: 895, colorKey: "blue", parent: "script", type: "chat" },
    { id: "asset", label: "Asset Creator", x: 525, y: 895, colorKey: "orange", parent: "vidgen", type: "cube" },
    { id: "animator", label: "Animator", x: 715, y: 895, colorKey: "orange", parent: "vidgen", type: "camera" },
    { id: "mon_bot", label: "MONITORING/LOGGING", x: 1245, y: 895, colorKey: "teal", parent: "ebook", type: "holo_pad" },
    { id: "keyword", label: "Keyword Research", x: 1665, y: 895, colorKey: "gold", parent: "seo", type: "screen_mag" },
    { id: "onpage", label: "On-Page Opt", x: 1835, y: 895, colorKey: "gold", parent: "seo", type: "screen_chart" },
  ];

  const CEO_CORE = { x: 960, y: 245, r: 90 };
  const UPPER_MON = { x: 1580, y: 345, label: "MONITORING/LOGGING" };

  const PIPES = [
    { id: "top_blue", colorKey: "blue", width: 34, pts: [{ x: 875, y: 225 }, { x: 325, y: 225 }, { x: 265, y: 345 }, { x: 265, y: 390 }], couplings: [{ x: 840, y: 225, dir: "h" }, { x: 520, y: 225, dir: "h" }, { x: 265, y: 365, dir: "v" }] },
    { id: "top_orange", colorKey: "orange", width: 34, pts: [{ x: 885, y: 255 }, { x: 635, y: 255 }, { x: 590, y: 345 }, { x: 590, y: 390 }], couplings: [{ x: 845, y: 255, dir: "h" }, { x: 675, y: 255, dir: "h" }, { x: 590, y: 365, dir: "v" }] },
    { id: "top_teal", colorKey: "teal", width: 32, pts: [{ x: 880, y: 280 }, { x: 785, y: 280 }, { x: 750, y: 345 }, { x: 830, y: 425 }, { x: 925, y: 425 }], couplings: [{ x: 835, y: 280, dir: "h" }, { x: 765, y: 315, dir: "v" }] },
    { id: "top_purple", colorKey: "purple", width: 34, pts: [{ x: 1035, y: 270 }, { x: 1185, y: 270 }, { x: 1225, y: 345 }, { x: 1225, y: 390 }], couplings: [{ x: 1085, y: 270, dir: "h" }, { x: 1225, y: 365, dir: "v" }] },
    { id: "top_green", colorKey: "green", width: 34, pts: [{ x: 1045, y: 240 }, { x: 1415, y: 240 }, { x: 1475, y: 350 }, { x: 1475, y: 390 }], couplings: [{ x: 1110, y: 240, dir: "h" }, { x: 1360, y: 240, dir: "h" }, { x: 1475, y: 365, dir: "v" }] },
    { id: "top_gold", colorKey: "gold", width: 34, pts: [{ x: 1040, y: 195 }, { x: 1595, y: 195 }, { x: 1715, y: 350 }, { x: 1715, y: 390 }], couplings: [{ x: 1105, y: 195, dir: "h" }, { x: 1390, y: 195, dir: "h" }, { x: 1560, y: 195, dir: "h" }, { x: 1715, y: 365, dir: "v" }] },

    { id: "side_script_L", colorKey: "blue", width: 24, pts: [{ x: 175, y: 585 }, { x: 52, y: 585 }, { x: 40, y: 695 }], couplings: [{ x: 155, y: 585, dir: "h" }, { x: 40, y: 685, dir: "v" }] },
    { id: "side_script_R", colorKey: "blue", width: 22, pts: [{ x: 355, y: 585 }, { x: 418, y: 585 }, { x: 418, y: 690 }], couplings: [{ x: 370, y: 585, dir: "h" }, { x: 418, y: 680, dir: "v" }] },
    { id: "side_vidgen_L", colorKey: "orange", width: 22, pts: [{ x: 500, y: 585 }, { x: 465, y: 585 }, { x: 465, y: 690 }], couplings: [{ x: 490, y: 585, dir: "h" }, { x: 465, y: 680, dir: "v" }] },
    { id: "side_vidgen_R", colorKey: "orange", width: 22, pts: [{ x: 680, y: 585 }, { x: 750, y: 585 }, { x: 750, y: 690 }], couplings: [{ x: 695, y: 585, dir: "h" }, { x: 750, y: 680, dir: "v" }] },
    { id: "side_videdit_L", colorKey: "teal", width: 22, pts: [{ x: 835, y: 585 }, { x: 795, y: 585 }, { x: 795, y: 690 }], couplings: [{ x: 820, y: 585, dir: "h" }, { x: 795, y: 680, dir: "v" }] },
    { id: "side_seo_R", colorKey: "gold", width: 22, pts: [{ x: 1805, y: 600 }, { x: 1895, y: 600 }, { x: 1905, y: 695 }], couplings: [{ x: 1825, y: 600, dir: "h" }, { x: 1905, y: 685, dir: "v" }] },

    { id: "bot_script_1", colorKey: "blue", width: 28, pts: [{ x: 235, y: 690 }, { x: 210, y: 785 }, { x: 115, y: 785 }, { x: 115, y: 855 }], couplings: [{ x: 230, y: 710, dir: "v" }, { x: 155, y: 785, dir: "h" }] },
    { id: "bot_script_2", colorKey: "blue", width: 28, pts: [{ x: 295, y: 690 }, { x: 345, y: 795 }, { x: 345, y: 865 }, { x: 315, y: 865 }], couplings: [{ x: 302, y: 710, dir: "v" }, { x: 345, y: 825, dir: "v" }] },
    { id: "bot_script_bridge", colorKey: "blue", width: 22, pts: [{ x: 155, y: 875 }, { x: 265, y: 875 }], couplings: [{ x: 210, y: 875, dir: "h" }] },
    { id: "bot_vidgen_1", colorKey: "orange", width: 28, pts: [{ x: 560, y: 690 }, { x: 525, y: 800 }, { x: 525, y: 855 }], couplings: [{ x: 552, y: 715, dir: "v" }, { x: 525, y: 825, dir: "v" }] },
    { id: "bot_vidgen_2", colorKey: "orange", width: 28, pts: [{ x: 620, y: 690 }, { x: 645, y: 790 }, { x: 715, y: 790 }, { x: 715, y: 855 }], couplings: [{ x: 625, y: 715, dir: "v" }, { x: 680, y: 790, dir: "h" }] },
    { id: "bot_vidgen_bridge", colorKey: "orange", width: 22, pts: [{ x: 575, y: 875 }, { x: 665, y: 875 }], couplings: [{ x: 620, y: 875, dir: "h" }] },
    { id: "bot_animator_to_edit", colorKey: "orange", width: 26, pts: [{ x: 765, y: 875 }, { x: 835, y: 875 }, { x: 865, y: 765 }, { x: 900, y: 765 }, { x: 900, y: 690 }], couplings: [{ x: 800, y: 875, dir: "h" }, { x: 850, y: 820, dir: "v" }] },

    { id: "bot_teal_main", colorKey: "teal", width: 28, pts: [{ x: 900, y: 690 }, { x: 900, y: 855 }, { x: 1175, y: 855 }], couplings: [{ x: 900, y: 725, dir: "v" }, { x: 900, y: 810, dir: "v" }, { x: 1050, y: 855, dir: "h" }] },
    { id: "bot_teal_inner", colorKey: "teal", width: 26, pts: [{ x: 955, y: 690 }, { x: 955, y: 795 }, { x: 1195, y: 795 }, { x: 1195, y: 845 }], couplings: [{ x: 955, y: 725, dir: "v" }, { x: 1085, y: 795, dir: "h" }] },
    { id: "bot_purple_left", colorKey: "purple", width: 26, pts: [{ x: 1195, y: 690 }, { x: 1195, y: 845 }], couplings: [{ x: 1195, y: 730, dir: "v" }] },
    { id: "bot_purple_right", colorKey: "purple", width: 26, pts: [{ x: 1260, y: 690 }, { x: 1260, y: 795 }, { x: 1410, y: 795 }, { x: 1445, y: 690 }], couplings: [{ x: 1260, y: 730, dir: "v" }, { x: 1340, y: 795, dir: "h" }, { x: 1435, y: 725, dir: "v" }] },
    { id: "bot_green_left", colorKey: "green", width: 28, pts: [{ x: 1465, y: 690 }, { x: 1465, y: 855 }, { x: 1315, y: 855 }], couplings: [{ x: 1465, y: 730, dir: "v" }, { x: 1390, y: 855, dir: "h" }] },
    { id: "bot_green_right", colorKey: "green", width: 28, pts: [{ x: 1510, y: 690 }, { x: 1545, y: 895 }, { x: 1315, y: 895 }], couplings: [{ x: 1518, y: 730, dir: "v" }, { x: 1435, y: 895, dir: "h" }] },

    { id: "bot_seo_1", colorKey: "gold", width: 28, pts: [{ x: 1685, y: 690 }, { x: 1665, y: 835 }], couplings: [{ x: 1678, y: 735, dir: "v" }] },
    { id: "bot_seo_2", colorKey: "gold", width: 28, pts: [{ x: 1745, y: 690 }, { x: 1825, y: 835 }], couplings: [{ x: 1765, y: 735, dir: "v" }] },
  ];

  const pipePaths = PIPES.map((pipe) => ({
    ...pipe,
    samples: buildRoundedPathSamples(pipe.pts, 26, 2.5),
  }));

  function buildRoundedPathSamples(pts, cornerRadius, stepSize) {
    if (pts.length < 2) return pts;
    const waypoints = [pts[0]];
    for (let i = 1; i < pts.length - 1; i++) {
      const prev = pts[i - 1];
      const curr = pts[i];
      const next = pts[i + 1];
      const v1x = curr.x - prev.x;
      const v1y = curr.y - prev.y;
      const len1 = Math.hypot(v1x, v1y) || 1;
      const v2x = next.x - curr.x;
      const v2y = next.y - curr.y;
      const len2 = Math.hypot(v2x, v2y) || 1;
      const r = Math.min(cornerRadius, len1 * 0.45, len2 * 0.45);
      const pA = { x: curr.x - (v1x / len1) * r, y: curr.y - (v1y / len1) * r };
      const pB = { x: curr.x + (v2x / len2) * r, y: curr.y + (v2y / len2) * r };
      waypoints.push(pA);
      for (let t = 0.2; t <= 0.8; t += 0.2) {
        const omt = 1 - t;
        waypoints.push({
          x: omt * omt * pA.x + 2 * omt * t * curr.x + t * t * pB.x,
          y: omt * omt * pA.y + 2 * omt * t * curr.y + t * t * pB.y,
        });
      }
      waypoints.push(pB);
    }
    waypoints.push(pts[pts.length - 1]);

    const dense = [];
    for (let i = 0; i < waypoints.length - 1; i++) {
      const a = waypoints[i];
      const b = waypoints[i + 1];
      const dist = Math.hypot(b.x - a.x, b.y - a.y);
      const steps = Math.max(1, Math.ceil(dist / stepSize));
      for (let s = 0; s < steps; s++) {
        const t = s / steps;
        dense.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, ang: Math.atan2(b.y - a.y, b.x - a.x) });
      }
    }
    const last = waypoints[waypoints.length - 1];
    dense.push({ x: last.x, y: last.y, ang: 0 });
    return dense;
  }

  function drawRoundedRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.lineTo(x + w - r, y);
    c.quadraticCurveTo(x + w, y, x + w, y + r);
    c.lineTo(x + w, y + h - r);
    c.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    c.lineTo(x + r, y + h);
    c.quadraticCurveTo(x, y + h, x, y + h - r);
    c.lineTo(x, y + r);
    c.quadraticCurveTo(x, y, x + r, y);
    c.closePath();
  }

  function statusOf(m) {
    const agent = agentState[m.id];
    return agent ? agent.status || "idle" : "idle";
  }
  function isWorking(m) {
    const status = statusOf(m);
    return status === "working" || status === "orchestrating";
  }
  function statusColor(status, pending) {
    if (pending) return "#c8a24a";
    if (status === "working" || status === "orchestrating") return "#ffd447";
    if (status === "done" || status === "review" || status === "completed") return "#4ce870";
    if (status === "error" || status === "failed") return "#ff6b6b";
    if (status === "awaiting_modules" || status === "blocked") return "#ffb347";
    return "#7d8ba0";
  }
  function statusLabel(status) {
    const labels = {
      working: "RUNNING", orchestrating: "ORCHESTRATING", done: "DONE", review: "REVIEW",
      error: "ERROR", failed: "ERROR", idle: "IDLE", online: "ONLINE", blocked: "BLOCKED",
      awaiting_modules: "BLOCKED", completed: "COMPLETE", partial: "PARTIAL",
    };
    return labels[status] || String(status || "idle").toUpperCase();
  }

  // ---------------------------------------------------------------- static --
  function renderStaticScene(c) {
    c.clearRect(0, 0, W, H);

    const bgGrad = c.createRadialGradient(W * 0.5, H * 0.45, 80, W * 0.5, H * 0.5, 1150);
    bgGrad.addColorStop(0, "#162038");
    bgGrad.addColorStop(0.55, "#0b101d");
    bgGrad.addColorStop(1, "#05070e");
    c.fillStyle = bgGrad;
    c.fillRect(0, 0, W, H);

    drawGoldenConstellationMesh(c);
    drawMainTitleHeader(c);
    drawMainPlatformBoard(c);
    drawBoardCircuitTraces(c);
    drawUpperMonitoringNode(c);
    pipePaths.forEach((pipe) => drawGlassPipeShell(c, pipe));
    MACHINES.forEach((m) => drawAgentMachineChassis(c, m));
    SUB_AGENTS.forEach((sub) => drawSubAgentPedestal(c, sub));
    drawCeoCoreBasePedestal(c);
  }

  function drawGoldenConstellationMesh(c) {
    const nodes = [
      [15, 580], [95, 620], [45, 710], [115, 760], [25, 840], [90, 920], [15, 995], [160, 1020],
      [1825, 560], [1905, 610], [1850, 705], [1910, 790], [1835, 860], [1905, 940], [1810, 1020],
      [280, 1045], [420, 995], [560, 1060], [740, 1005], [910, 1065], [1080, 1005],
      [1240, 1065], [1420, 1005], [1590, 1060], [1730, 1000],
    ];
    c.save();
    c.strokeStyle = "rgba(245, 180, 65, 0.24)";
    c.lineWidth = 1.2;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const distance = Math.hypot(nodes[i][0] - nodes[j][0], nodes[i][1] - nodes[j][1]);
        if (distance < 230) {
          c.beginPath();
          c.moveTo(nodes[i][0], nodes[i][1]);
          c.lineTo(nodes[j][0], nodes[j][1]);
          c.stroke();
        }
      }
    }
    nodes.forEach(([nx, ny]) => {
      const g = c.createRadialGradient(nx, ny, 0, nx, ny, 7);
      g.addColorStop(0, "#ffe28a");
      g.addColorStop(0.5, "rgba(255, 185, 50, 0.6)");
      g.addColorStop(1, "rgba(255, 185, 50, 0)");
      c.fillStyle = g;
      c.beginPath();
      c.arc(nx, ny, 7, 0, Math.PI * 2);
      c.fill();
    });
    c.restore();
  }

  function drawMainTitleHeader(c) {
    const title = "MULTI-AGENT AI COMPANY ORGANIZATIONAL WORKFLOW";
    c.save();
    c.font = '900 50px "Orbitron", sans-serif';
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.shadowColor = "rgba(80, 205, 255, 0.65)";
    c.shadowBlur = 24;
    for (let d = 6; d >= 1; d--) {
      c.fillStyle = d > 3 ? "#1b3554" : "#2c5480";
      c.fillText(title, W / 2, 68 + d);
    }
    const grad = c.createLinearGradient(0, 42, 0, 92);
    grad.addColorStop(0, "#f0fbff");
    grad.addColorStop(0.45, "#a8dcf7");
    grad.addColorStop(0.55, "#6da8d6");
    grad.addColorStop(1, "#bce8ff");
    c.fillStyle = grad;
    c.fillText(title, W / 2, 68);
    c.lineWidth = 1.2;
    c.strokeStyle = "rgba(255, 255, 255, 0.65)";
    c.strokeText(title, W / 2, 68);
    c.restore();
  }

  function drawMainPlatformBoard(c) {
    c.save();
    const topY = 175;
    const botY = 965;
    const topLeftX = 165;
    const topRightX = 1755;
    const botLeftX = 18;
    const botRightX = 1902;
    const cornerR = 48;

    function tracePlatformPath(offsetY = 0) {
      c.beginPath();
      c.moveTo(topLeftX + cornerR, topY + offsetY);
      c.lineTo(topRightX - cornerR, topY + offsetY);
      c.quadraticCurveTo(topRightX, topY + offsetY, topRightX + 14, topY + cornerR + offsetY);
      c.lineTo(botRightX - 8, botY - cornerR + offsetY);
      c.quadraticCurveTo(botRightX, botY + offsetY, botRightX - cornerR, botY + offsetY);
      c.lineTo(botLeftX + cornerR, botY + offsetY);
      c.quadraticCurveTo(botLeftX, botY + offsetY, botLeftX + 8, botY - cornerR + offsetY);
      c.lineTo(topLeftX - 14, topY + cornerR + offsetY);
      c.quadraticCurveTo(topLeftX, topY + offsetY, topLeftX + cornerR, topY + offsetY);
      c.closePath();
    }

    c.save();
    tracePlatformPath(28);
    c.fillStyle = "rgba(0, 0, 0, 0.85)";
    c.shadowColor = "#000";
    c.shadowBlur = 45;
    c.fill();
    c.restore();

    tracePlatformPath(22);
    const rimGrad = c.createLinearGradient(0, botY - 40, 0, botY + 25);
    rimGrad.addColorStop(0, "#1e2636");
    rimGrad.addColorStop(0.6, "#0f141f");
    rimGrad.addColorStop(1, "#292214");
    c.fillStyle = rimGrad;
    c.fill();

    c.save();
    tracePlatformPath(20);
    c.strokeStyle = "#ffbe42";
    c.lineWidth = 3.5;
    c.shadowColor = "#ffae19";
    c.shadowBlur = 18;
    c.stroke();
    c.restore();

    tracePlatformPath(0);
    const surfGrad = c.createRadialGradient(W * 0.5, 480, 60, W * 0.5, 580, 1020);
    surfGrad.addColorStop(0, "#323f54");
    surfGrad.addColorStop(0.55, "#202938");
    surfGrad.addColorStop(1, "#141a24");
    c.fillStyle = surfGrad;
    c.fill();
    c.strokeStyle = "rgba(160, 195, 235, 0.25)";
    c.lineWidth = 2;
    c.stroke();
    c.restore();
  }

  function drawBoardCircuitTraces(c) {
    c.save();
    c.strokeStyle = "rgba(255, 255, 255, 0.04)";
    c.lineWidth = 1.5;
    [350, 520, 715, 830].forEach((y) => {
      c.beginPath();
      c.moveTo(110, y);
      c.lineTo(1810, y);
      c.stroke();
    });

    c.strokeStyle = "rgba(245, 190, 80, 0.45)";
    c.lineWidth = 2;
    c.beginPath();
    c.moveTo(215, 920); c.lineTo(215, 940); c.lineTo(620, 940); c.lineTo(620, 920);
    c.moveTo(620, 940); c.lineTo(1245, 940); c.lineTo(1245, 928);
    c.moveTo(1245, 940); c.lineTo(1605, 940); c.lineTo(1635, 905);
    c.stroke();

    c.strokeStyle = "rgba(65, 225, 255, 0.55)";
    c.lineWidth = 2.2;
    c.beginPath();
    c.moveTo(1005, 690); c.lineTo(1005, 925); c.lineTo(1175, 925);
    c.stroke();
    c.restore();
  }

  function drawUpperMonitoringNode(c) {
    const { x, y, label } = UPPER_MON;
    c.save();
    c.strokeStyle = "rgba(130, 200, 255, 0.45)";
    c.lineWidth = 3;
    c.beginPath();
    c.moveTo(1045, 315); c.lineTo(1310, 315); c.lineTo(1335, 345); c.lineTo(x - 75, 345);
    c.stroke();

    drawRoundedRect(c, x - 78, y - 30, 156, 62, 12);
    c.fillStyle = "#151c28";
    c.fill();
    c.strokeStyle = "rgba(120, 160, 200, 0.35)";
    c.lineWidth = 1.5;
    c.stroke();

    c.beginPath();
    c.ellipse(x, y - 6, 32, 18, 0, 0, Math.PI * 2);
    c.fillStyle = "#0c263b";
    c.fill();
    c.lineWidth = 3.5;
    c.strokeStyle = "#4cd4ff";
    c.shadowColor = "#38b6ff";
    c.shadowBlur = 15;
    c.stroke();

    c.beginPath();
    c.ellipse(x, y - 6, 14, 7, 0, 0, Math.PI * 2);
    c.fillStyle = "#bff2ff";
    c.fill();

    c.shadowBlur = 0;
    c.font = '700 13px "Rajdhani", sans-serif';
    c.fillStyle = "#d8ecff";
    c.textAlign = "center";
    c.fillText(label, x, y + 25);
    c.restore();
  }

  function drawCeoCoreBasePedestal(c) {
    const cx = CEO_CORE.x;
    const cy = CEO_CORE.y + 82;
    c.save();
    c.beginPath();
    c.ellipse(cx, cy, 118, 42, 0, 0, Math.PI * 2);
    const baseGrad = c.createLinearGradient(cx - 118, cy, cx + 118, cy);
    baseGrad.addColorStop(0, "#1a2230");
    baseGrad.addColorStop(0.5, "#3b485e");
    baseGrad.addColorStop(1, "#161d29");
    c.fillStyle = baseGrad;
    c.fill();
    c.lineWidth = 2.5;
    c.strokeStyle = "rgba(255, 205, 95, 0.65)";
    c.stroke();

    c.beginPath();
    c.ellipse(cx, cy - 8, 92, 31, 0, 0, Math.PI * 2);
    c.fillStyle = "#222a3a";
    c.fill();
    c.strokeStyle = "#ffbe44";
    c.lineWidth = 2;
    c.stroke();

    c.beginPath();
    c.ellipse(cx, cy - 15, 66, 21, 0, 0, Math.PI * 2);
    const dishGrad = c.createRadialGradient(cx, cy - 15, 4, cx, cy - 15, 65);
    dishGrad.addColorStop(0, "#fff3b8");
    dishGrad.addColorStop(0.5, "#f59e0b");
    dishGrad.addColorStop(1, "#452708");
    c.fillStyle = dishGrad;
    c.fill();
    c.restore();
  }

  function drawGlassPipeShell(c, pipe) {
    const pal = PALETTE[pipe.colorKey];
    const pts = pipe.pts;
    const w = pipe.width;

    c.save();
    c.lineCap = "round";
    c.lineJoin = "round";

    function tracePipe() {
      c.beginPath();
      c.moveTo(pts[0].x, pts[0].y);
      for (let i = 1; i < pts.length - 1; i++) {
        c.arcTo(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y, 26);
      }
      c.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y);
    }

    c.save();
    c.translate(0, 8);
    tracePipe();
    c.strokeStyle = "rgba(0, 0, 0, 0.55)";
    c.lineWidth = w + 6;
    c.stroke();
    c.restore();

    c.save();
    tracePipe();
    c.strokeStyle = pal.glow;
    c.lineWidth = w + 2;
    c.shadowColor = pal.main;
    c.shadowBlur = 18;
    c.stroke();
    c.restore();

    tracePipe();
    c.strokeStyle = "#0d1522";
    c.lineWidth = w - 4;
    c.stroke();

    tracePipe();
    c.strokeStyle = pal.dark;
    c.globalAlpha = 0.55;
    c.lineWidth = w - 8;
    c.stroke();
    c.globalAlpha = 1;

    tracePipe();
    c.strokeStyle = "rgba(255, 255, 255, 0.38)";
    c.lineWidth = w - 2;
    c.stroke();

    if (pipe.couplings) {
      pipe.couplings.forEach((cp) => drawPipeCoupling(c, cp.x, cp.y, w, cp.dir, pal.main));
    }
    c.restore();
  }

  function drawPipeCoupling(c, x, y, pipeW, dir, accentColor) {
    c.save();
    c.translate(x, y);
    if (dir === "v") c.rotate(Math.PI / 2);
    const rw = 14;
    const rh = pipeW + 10;
    const grad = c.createLinearGradient(0, -rh / 2, 0, rh / 2);
    grad.addColorStop(0, "#495870");
    grad.addColorStop(0.3, "#9fb4d0");
    grad.addColorStop(0.5, "#2b3547");
    grad.addColorStop(0.8, "#7388a6");
    grad.addColorStop(1, "#1c2330");
    drawRoundedRect(c, -rw / 2, -rh / 2, rw, rh, 4);
    c.fillStyle = grad;
    c.fill();
    c.strokeStyle = "rgba(255,255,255,0.45)";
    c.lineWidth = 1.2;
    c.stroke();
    c.strokeStyle = accentColor;
    c.lineWidth = 2;
    c.beginPath();
    c.moveTo(0, -rh / 2 + 2);
    c.lineTo(0, rh / 2 - 2);
    c.stroke();
    c.restore();
  }

  function drawAgentMachineChassis(c, m) {
    const pal = PALETTE[m.colorKey];
    const x = m.x;
    const y = m.y;
    const boxW = 176;
    const boxH = 225;
    const left = x - boxW / 2;
    const top = y - 105;

    c.save();
    c.beginPath();
    c.ellipse(x, top + boxH + 8, boxW * 0.56, 22, 0, 0, Math.PI * 2);
    c.fillStyle = "rgba(0, 0, 0, 0.7)";
    c.fill();

    c.beginPath();
    c.moveTo(left + 14, top - 22);
    c.lineTo(left + boxW - 14, top - 22);
    c.lineTo(left + boxW, top + 6);
    c.lineTo(left, top + 6);
    c.closePath();
    const roofGrad = c.createLinearGradient(left, top - 22, left + boxW, top + 6);
    roofGrad.addColorStop(0, pal.light);
    roofGrad.addColorStop(0.5, pal.bodyTop);
    roofGrad.addColorStop(1, pal.bodySide);
    c.fillStyle = roofGrad;
    c.fill();
    c.strokeStyle = "rgba(255,255,255,0.45)";
    c.lineWidth = 1.5;
    c.stroke();

    drawRoundedRect(c, left, top, boxW, boxH, 18);
    const bodyGrad = c.createLinearGradient(left, top, left + boxW, top + boxH);
    bodyGrad.addColorStop(0, pal.bodyTop);
    bodyGrad.addColorStop(0.5, pal.bodyFront);
    bodyGrad.addColorStop(1, pal.bodySide);
    c.fillStyle = bodyGrad;
    c.fill();
    c.strokeStyle = pal.light;
    c.lineWidth = 2;
    c.stroke();

    const plaqueW = boxW - 18;
    const plaqueH = 28;
    const plaqueX = x - plaqueW / 2;
    const plaqueY = top + 34;
    drawRoundedRect(c, plaqueX, plaqueY, plaqueW, plaqueH, 6);
    c.fillStyle = "rgba(10, 16, 28, 0.78)";
    c.fill();
    c.strokeStyle = pal.main;
    c.lineWidth = 1.5;
    c.stroke();
    c.font = '800 15px "Rajdhani", sans-serif';
    c.fillStyle = "#ffffff";
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.fillText(m.name, x, plaqueY + plaqueH / 2 + 1);

    const bayW = boxW - 28;
    const bayH = 128;
    const bayX = x - bayW / 2;
    const bayY = top + 72;
    drawRoundedRect(c, bayX, bayY, bayW, bayH, 10);
    const bayGrad = c.createLinearGradient(bayX, bayY, bayX, bayY + bayH);
    bayGrad.addColorStop(0, "#090e18");
    bayGrad.addColorStop(1, "#162236");
    c.fillStyle = bayGrad;
    c.fill();
    c.strokeStyle = "rgba(255, 255, 255, 0.22)";
    c.lineWidth = 1.5;
    c.stroke();

    const badgeY = top - 16;
    const badgeR = 46;
    c.beginPath();
    c.arc(x, badgeY - 4, badgeR + 5, 0, Math.PI * 2);
    c.fillStyle = pal.bodySide;
    c.fill();
    c.strokeStyle = pal.light;
    c.lineWidth = 2;
    c.stroke();

    c.beginPath();
    c.arc(x, badgeY, badgeR, 0, Math.PI * 2);
    const badgeGrad = c.createRadialGradient(x, badgeY, 6, x, badgeY, badgeR);
    badgeGrad.addColorStop(0, pal.dark);
    badgeGrad.addColorStop(0.75, "#0b1220");
    badgeGrad.addColorStop(1, "#070b14");
    c.fillStyle = badgeGrad;
    c.fill();
    c.strokeStyle = pal.main;
    c.lineWidth = 3.5;
    c.shadowColor = pal.main;
    c.shadowBlur = 14;
    c.stroke();
    c.shadowBlur = 0;

    drawMachineTopIcon(c, m.id, x, badgeY, pal.light, pal.main);
    drawSideRobotHelpers(c, m);
    c.restore();
  }

  function drawMachineTopIcon(c, id, cx, cy, lightColor, glowColor) {
    c.save();
    c.strokeStyle = lightColor;
    c.fillStyle = lightColor;
    c.lineWidth = 3;
    c.lineCap = "round";
    c.lineJoin = "round";
    c.shadowColor = glowColor;
    c.shadowBlur = 12;

    if (id === "script") {
      c.translate(cx, cy);
      c.rotate(-Math.PI / 4);
      c.beginPath();
      c.moveTo(-22, 0); c.lineTo(-6, -11); c.lineTo(18, -7); c.lineTo(18, 7); c.lineTo(-6, 11);
      c.closePath();
      c.stroke();
      c.beginPath(); c.moveTo(-22, 0); c.lineTo(-4, 0); c.stroke();
      c.beginPath(); c.arc(-2, 0, 3, 0, Math.PI * 2); c.fill();
    } else if (id === "vidgen") {
      drawRoundedRect(c, cx - 22, cy - 14, 30, 28, 5);
      c.stroke();
      c.beginPath(); c.arc(cx - 7, cy, 7, 0, Math.PI * 2); c.stroke();
      c.beginPath();
      c.moveTo(cx + 8, cy - 6); c.lineTo(cx + 22, cy - 13); c.lineTo(cx + 22, cy + 13); c.lineTo(cx + 8, cy + 6);
      c.closePath();
      c.stroke();
    } else if (id === "videdit") {
      drawRoundedRect(c, cx - 22, cy - 16, 44, 32, 4);
      c.stroke();
      for (let i = -14; i <= 14; i += 14) {
        c.fillRect(cx + i - 2, cy - 13, 4, 3);
        c.fillRect(cx + i - 2, cy + 10, 4, 3);
      }
      c.beginPath();
      c.moveTo(cx - 5, cy - 7); c.lineTo(cx + 9, cy); c.lineTo(cx - 5, cy + 7);
      c.closePath();
      c.fill();
    } else if (id === "ebook") {
      c.beginPath();
      c.moveTo(cx, cy - 12);
      c.quadraticCurveTo(cx - 12, cy - 16, cx - 22, cy - 10);
      c.lineTo(cx - 22, cy + 14);
      c.quadraticCurveTo(cx - 12, cy + 9, cx, cy + 15);
      c.quadraticCurveTo(cx + 12, cy + 9, cx + 22, cy + 14);
      c.lineTo(cx + 22, cy - 10);
      c.quadraticCurveTo(cx + 12, cy - 16, cx, cy - 12);
      c.closePath();
      c.stroke();
      c.beginPath(); c.moveTo(cx, cy - 12); c.lineTo(cx, cy + 15); c.stroke();
    } else if (id === "leadgen") {
      c.beginPath(); c.ellipse(cx, cy - 13, 20, 6, 0, 0, Math.PI * 2); c.stroke();
      c.beginPath();
      c.moveTo(cx - 20, cy - 13); c.lineTo(cx - 4, cy + 4); c.lineTo(cx - 4, cy + 18);
      c.lineTo(cx + 4, cy + 15); c.lineTo(cx + 4, cy + 4); c.lineTo(cx + 20, cy - 13);
      c.stroke();
    } else if (id === "seo") {
      c.beginPath(); c.arc(cx - 4, cy - 4, 14, 0, Math.PI * 2); c.stroke();
      c.lineWidth = 5;
      c.beginPath(); c.moveTo(cx + 6, cy + 6); c.lineTo(cx + 19, cy + 19); c.stroke();
    }
    c.restore();
  }

  function drawSideRobotHelpers(c, m) {
    c.save();
    if (m.id === "script") {
      const rx = m.x + 115;
      const ry = m.y - 10;
      c.fillStyle = "#d8e4f2";
      c.strokeStyle = "#334155";
      c.lineWidth = 2;
      c.beginPath(); c.arc(rx, ry, 12, 0, Math.PI * 2); c.fill(); c.stroke();
      c.fillStyle = "#0ea5e9";
      c.beginPath(); c.arc(rx - 3, ry - 2, 4, 0, Math.PI * 2); c.fill();
    } else if (m.id === "videdit") {
      const rx = m.x + 115;
      const ry = m.y + 45;
      c.fillStyle = "#334155";
      c.beginPath(); c.ellipse(rx, ry + 55, 22, 9, 0, 0, Math.PI * 2); c.fill();
      c.fillStyle = "#e2e8f0";
      drawRoundedRect(c, rx - 13, ry + 5, 26, 45, 8); c.fill();
      drawRoundedRect(c, rx - 15, ry - 22, 30, 24, 10); c.fill();
      c.fillStyle = "#0f172a";
      drawRoundedRect(c, rx - 11, ry - 15, 22, 10, 4); c.fill();
      c.fillStyle = "#38bdf8";
      c.beginPath(); c.arc(rx - 5, ry - 10, 2.5, 0, Math.PI * 2); c.arc(rx + 5, ry - 10, 2.5, 0, Math.PI * 2); c.fill();
    } else if (m.id === "seo") {
      const rx = m.x - 115;
      const ry = m.y + 35;
      c.fillStyle = "#eab308";
      drawRoundedRect(c, rx - 12, ry, 24, 52, 6); c.fill();
      c.fillStyle = "#1e293b";
      c.beginPath(); c.arc(rx, ry - 8, 12, 0, Math.PI * 2); c.fill();
      c.fillStyle = "#fef08a";
      c.beginPath(); c.arc(rx, ry - 8, 5, 0, Math.PI * 2); c.fill();
    }
    c.restore();
  }

  function drawSubAgentPedestal(c, sub) {
    const pal = PALETTE[sub.colorKey];
    const x = sub.x;
    const y = sub.y;
    c.save();

    if (sub.type === "holo_pad") {
      const rx = 74;
      const ry = 32;
      c.beginPath(); c.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
      c.fillStyle = "#1a2333"; c.fill();
      c.lineWidth = 3; c.strokeStyle = "#64748b"; c.stroke();
      c.beginPath(); c.ellipse(x, y - 6, rx - 14, ry - 7, 0, 0, Math.PI * 2);
      c.fillStyle = "#0d1829"; c.fill();
      c.strokeStyle = "#3be8d0"; c.lineWidth = 3; c.shadowColor = "#3be8d0"; c.shadowBlur = 16; c.stroke();
      c.shadowBlur = 0;
      c.font = '800 18px "Rajdhani", sans-serif';
      c.fillStyle = "#e2f1ff"; c.textAlign = "center";
      c.fillText(sub.label, x, y + 54);
      c.restore();
      return;
    }

    const pw = 108;
    const ph = 52;
    drawRoundedRect(c, x - pw / 2, y - ph / 2 + 8, pw, ph, 12);
    c.fillStyle = "#121824"; c.fill();
    c.strokeStyle = pal.main; c.lineWidth = 2.5; c.shadowColor = pal.main; c.shadowBlur = 14; c.stroke();
    c.shadowBlur = 0;

    drawRoundedRect(c, x - pw / 2 + 6, y - ph / 2, pw - 12, ph - 8, 9);
    const topGrad = c.createLinearGradient(x, y - ph / 2, x, y + ph / 2);
    topGrad.addColorStop(0, "#2c374b"); topGrad.addColorStop(1, "#19202e");
    c.fillStyle = topGrad; c.fill();

    if (sub.type === "printer") {
      drawRoundedRect(c, x - 28, y - 48, 56, 40, 6);
      c.fillStyle = "#94a3b8"; c.fill();
      drawRoundedRect(c, x - 20, y - 62, 40, 38, 3);
      c.fillStyle = "#e0f2fe"; c.fill();
      c.strokeStyle = "#0284c7"; c.lineWidth = 2;
      for (let ly = -54; ly <= -32; ly += 6) {
        c.beginPath(); c.moveTo(x - 14, y + ly); c.lineTo(x + 14, y + ly); c.stroke();
      }
    } else if (sub.type === "chat") {
      drawRoundedRect(c, x - 26, y - 54, 52, 42, 8);
      c.fillStyle = "#1e293b"; c.fill();
      c.strokeStyle = "#38bdf8"; c.lineWidth = 2; c.stroke();
      drawRoundedRect(c, x - 14, y - 44, 28, 18, 4);
      c.fillStyle = "#38bdf8"; c.fill();
    } else if (sub.type === "cube") {
      drawRoundedRect(c, x - 30, y - 42, 60, 32, 6);
      c.fillStyle = "#475569"; c.fill();
      c.beginPath(); c.arc(x, y - 48, 14, 0, Math.PI * 2); c.fillStyle = "#f97316"; c.fill();
    } else if (sub.type === "camera") {
      drawRoundedRect(c, x - 22, y - 56, 44, 28, 6);
      c.fillStyle = "#78350f"; c.fill();
      c.strokeStyle = "#fb923c"; c.lineWidth = 2; c.stroke();
      c.beginPath(); c.arc(x - 16, y - 62, 9, 0, Math.PI * 2); c.arc(x + 4, y - 62, 9, 0, Math.PI * 2);
      c.fillStyle = "#fdba74"; c.fill();
    } else if (sub.type === "screen_mag") {
      drawRoundedRect(c, x - 36, y - 58, 72, 48, 7);
      c.fillStyle = "rgba(30, 41, 59, 0.9)"; c.fill();
      c.strokeStyle = "#facc15"; c.lineWidth = 2; c.stroke();
      c.beginPath(); c.arc(x - 4, y - 36, 11, 0, Math.PI * 2); c.stroke();
      c.beginPath(); c.moveTo(x + 4, y - 28); c.lineTo(x + 15, y - 18); c.stroke();
    } else if (sub.type === "screen_chart") {
      drawRoundedRect(c, x - 36, y - 58, 72, 48, 7);
      c.fillStyle = "rgba(30, 41, 59, 0.9)"; c.fill();
      c.strokeStyle = "#facc15"; c.lineWidth = 2; c.stroke();
      c.beginPath();
      c.moveTo(x - 24, y - 22); c.lineTo(x - 8, y - 36); c.lineTo(x + 6, y - 28); c.lineTo(x + 24, y - 46);
      c.strokeStyle = "#fde047"; c.lineWidth = 3; c.stroke();
    }

    c.font = '700 20px "Inter", sans-serif';
    c.fillStyle = "#f1f5f9"; c.textAlign = "center";
    c.fillText(sub.label, x, y + 54);
    c.restore();
  }

  // --------------------------------------------------------------- dynamic --
  function renderDynamicFrame(timeSec) {
    ctx.setTransform(LIVE_SCALE, 0, 0, LIVE_SCALE, 0, 0);
    ctx.drawImage(staticCanvas, 0, 0);
    renderAllPipeDataStreams(ctx, timeSec);
    MACHINES.forEach((m) => {
      renderAnimatedMachineBay(ctx, m, timeSec * (isWorking(m) ? 1.9 : 1));
      renderMachineTelemetry(ctx, m, timeSec);
    });
    renderCeoCoreOrb(ctx, timeSec);
    renderSurgeBursts(ctx, timeSec);
    if (hoveredNode) {
      ctx.save();
      ctx.beginPath();
      ctx.arc(hoveredNode.x, hoveredNode.y, hoveredNode.r || 95, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.55)";
      ctx.lineWidth = 2.5;
      ctx.setLineDash([8, 6]);
      ctx.stroke();
      ctx.restore();
    }
  }

  function renderMachineTelemetry(c, m, t) {
    const agent = agentState[m.id] || {};
    const working = isWorking(m);
    const color = statusColor(agent.status, agent.module_status === "pending");
    const top = m.y - 105;
    const left = m.x - 88;

    c.save();
    c.beginPath();
    c.arc(left + 166, top + 16, 4.5 + (working ? Math.sin(t * 6) * 1.2 : 0), 0, Math.PI * 2);
    c.fillStyle = color;
    c.shadowColor = color;
    c.shadowBlur = 12;
    c.fill();
    c.shadowBlur = 0;

    const pct = Math.max(0, Math.min(100, Number(agent.progress || 0)));
    drawRoundedRect(c, left + 14, top + 203, 148, 5, 3);
    c.fillStyle = "rgba(8, 12, 20, 0.9)";
    c.fill();
    if (pct > 0) {
      drawRoundedRect(c, left + 14, top + 203, (148 * pct) / 100, 5, 3);
      c.fillStyle = color;
      c.fill();
    }
    c.restore();
  }

  function renderAllPipeDataStreams(c, t) {
    c.save();
    pipePaths.forEach((pipe, pIdx) => {
      const pal = PALETTE[pipe.colorKey];
      const samples = pipe.samples;
      const len = samples.length;
      if (len < 4) return;
      const pts = pipe.pts;
      const boost = isWorking(MACHINES.find((m) => m.colorKey === pipe.colorKey)) ? 1.8 : 1;

      c.save();
      c.beginPath();
      c.moveTo(pts[0].x, pts[0].y);
      for (let i = 1; i < pts.length - 1; i++) c.arcTo(pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y, 26);
      c.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y);

      c.strokeStyle = pal.main;
      c.lineWidth = pipe.width * 0.36;
      c.lineCap = "round";
      c.setLineDash([22, 34]);
      c.lineDashOffset = -(t * 95 * flowSpeed * boost + pIdx * 17);
      c.globalAlpha = 0.75;
      c.stroke();

      c.strokeStyle = pal.light;
      c.lineWidth = pipe.width * 0.16;
      c.setLineDash([8, 48]);
      c.lineDashOffset = -(t * 145 * flowSpeed * boost + pIdx * 31);
      c.globalAlpha = 0.95;
      c.stroke();
      c.restore();

      const packetCount = Math.min(7, Math.max(3, Math.floor(len / 70)));
      for (let k = 0; k < packetCount; k++) {
        const progress = ((t * 0.28 * flowSpeed * boost) + (k / packetCount) + (pIdx * 0.13)) % 1;
        const idx = Math.min(len - 1, Math.floor(progress * len));
        const pt = samples[idx];
        c.fillStyle = "#ffffff";
        c.beginPath(); c.arc(pt.x, pt.y, 3.6, 0, Math.PI * 2); c.fill();
        c.fillStyle = pal.light;
        c.globalAlpha = 0.65;
        c.beginPath(); c.arc(pt.x, pt.y, 7.5, 0, Math.PI * 2); c.fill();
        c.globalAlpha = 1;
      }
    });
    c.restore();
  }

  function renderAnimatedMachineBay(c, m, t) {
    const x = m.x;
    const y = m.y;
    const bayW = 148;
    const bayH = 128;
    const bayX = x - bayW / 2;
    const bayY = y - 33;

    c.save();
    if (m.id === "script") {
      const paperW = 92;
      const paperX = x - paperW / 2;
      const paperY = bayY + 12;
      c.fillStyle = "rgba(56, 182, 255, 0.18)";
      c.fillRect(bayX + 6, bayY + 6, bayW - 12, bayH - 12);

      c.beginPath();
      c.moveTo(paperX, paperY);
      c.lineTo(paperX + paperW, paperY);
      c.lineTo(paperX + paperW + 4, paperY + 135);
      c.lineTo(paperX - 4, paperY + 135);
      c.closePath();
      const pGrad = c.createLinearGradient(paperX, paperY, paperX, paperY + 135);
      pGrad.addColorStop(0, "#bae6fd");
      pGrad.addColorStop(0.3, "#f0f9ff");
      pGrad.addColorStop(1, "#7dd3fc");
      c.fillStyle = pGrad; c.fill();
      c.strokeStyle = "#38bdf8"; c.lineWidth = 1.5; c.stroke();

      c.save();
      c.clip();
      const scrollOffset = (t * 28 * flowSpeed) % 12;
      c.strokeStyle = "#1e3a8a";
      c.lineWidth = 2.2;
      for (let ly = paperY + 8 - scrollOffset; ly < paperY + 132; ly += 10) {
        const lineW = 48 + ((Math.floor((ly + scrollOffset) / 10) * 17) % 28);
        c.beginPath(); c.moveTo(paperX + 10, ly); c.lineTo(paperX + 10 + lineW, ly); c.stroke();
      }
      c.restore();
    } else if (m.id === "vidgen") {
      c.fillStyle = "rgba(255, 148, 66, 0.18)";
      c.fillRect(bayX + 6, bayY + 6, bayW - 12, bayH - 12);
      drawRoundedRect(c, x - 48, bayY + 14, 96, 56, 6);
      c.fillStyle = "#451a03"; c.fill();
      c.strokeStyle = "#fb923c"; c.lineWidth = 2; c.stroke();
      c.fillStyle = "#fed7aa";
      c.beginPath();
      c.moveTo(x - 8, bayY + 32); c.lineTo(x + 12, bayY + 42); c.lineTo(x - 8, bayY + 52);
      c.closePath(); c.fill();

      const stripX = x - 42;
      const stripY = bayY + 58;
      for (let f = 0; f < 4; f++) {
        const fy = stripY + f * 34 + ((t * 24 * flowSpeed) % 34) - 10;
        const shiftX = Math.sin(f * 0.7 + t * 2) * 4;
        drawRoundedRect(c, stripX + shiftX, fy, 64, 28, 3);
        c.fillStyle = "#292524"; c.fill();
        c.strokeStyle = "#fdba74"; c.lineWidth = 1.5; c.stroke();
        c.fillStyle = "#fb923c";
        c.beginPath(); c.arc(stripX + shiftX + 22, fy + 14, 6, 0, Math.PI * 2); c.fill();
        c.fillRect(stripX + shiftX + 34, fy + 9, 18, 10);
      }
    } else if (m.id === "videdit") {
      drawRoundedRect(c, bayX + 12, bayY + 10, bayW - 24, 20, 4);
      c.fillStyle = "#1e293b"; c.fill();
      c.strokeStyle = "#2dd4bf"; c.lineWidth = 1.5; c.stroke();
      c.fillStyle = "#99f6e4";
      for (let sx = bayX + 18; sx < bayX + bayW - 24; sx += 18) c.fillRect(sx, bayY + 12, 8, 16);

      const tracks = [
        { y: bayY + 42, color: "#38bdf8", w1: 38, w2: 46 },
        { y: bayY + 66, color: "#c084fc", w1: 52, w2: 34 },
        { y: bayY + 90, color: "#4ade80", w1: 30, w2: 56 },
      ];
      tracks.forEach((tr, idx) => {
        const shift = Math.sin(t * 2 * flowSpeed + idx) * 6;
        drawRoundedRect(c, bayX + 16 + shift, tr.y, tr.w1, 16, 3);
        c.fillStyle = tr.color; c.fill();
        drawRoundedRect(c, bayX + 24 + tr.w1 + shift, tr.y, tr.w2, 16, 3);
        c.fill();
      });
      const playheadX = bayX + 20 + ((t * 45 * flowSpeed) % (bayW - 40));
      c.strokeStyle = "#ffffff"; c.lineWidth = 2;
      c.beginPath(); c.moveTo(playheadX, bayY + 36); c.lineTo(playheadX, bayY + 114); c.stroke();
    } else if (m.id === "ebook") {
      const scrollW = 74;
      const scrollX = x - scrollW / 2;
      const scrollTop = bayY + 26;
      const scrollBot = 865;
      const holoGrad = c.createLinearGradient(x, scrollTop, x, scrollBot);
      holoGrad.addColorStop(0, "rgba(216, 180, 254, 0.92)");
      holoGrad.addColorStop(0.5, "rgba(168, 85, 247, 0.75)");
      holoGrad.addColorStop(0.85, "rgba(56, 189, 248, 0.75)");
      holoGrad.addColorStop(1, "rgba(59, 232, 208, 0.95)");
      c.beginPath();
      c.moveTo(scrollX, scrollTop);
      c.lineTo(scrollX + scrollW, scrollTop);
      c.bezierCurveTo(scrollX + scrollW + 10, scrollTop + 110, scrollX + scrollW - 8, scrollBot - 80, scrollX + scrollW, scrollBot);
      c.lineTo(scrollX, scrollBot);
      c.bezierCurveTo(scrollX - 8, scrollBot - 80, scrollX + 10, scrollTop + 110, scrollX, scrollTop);
      c.closePath();
      c.fillStyle = holoGrad; c.fill();
      c.strokeStyle = "#e9d5ff"; c.lineWidth = 1.8; c.stroke();

      c.save();
      c.clip();
      const textShift = (t * 40 * flowSpeed) % 14;
      c.strokeStyle = "rgba(15, 23, 42, 0.72)";
      c.lineWidth = 2.2;
      for (let ly = scrollTop + 10 - textShift; ly < scrollBot - 25; ly += 9) {
        const lw = 38 + ((Math.floor((ly + textShift) / 9) * 13) % 20);
        c.beginPath(); c.moveTo(scrollX + 10, ly); c.lineTo(scrollX + 10 + lw, ly); c.stroke();
      }
      c.restore();

      const bx = x + 15;
      const by = 842;
      c.strokeStyle = "#a5f3fc"; c.lineWidth = 2.5;
      c.strokeRect(bx - 22, by - 16, 20, 24);
      c.strokeRect(bx - 2, by - 16, 20, 24);
    } else if (m.id === "leadgen") {
      const nodes = [
        { nx: x - 36, ny: bayY + 34 }, { nx: x + 36, ny: bayY + 34 },
        { nx: x, ny: bayY + 64 }, { nx: x - 34, ny: bayY + 96 }, { nx: x + 34, ny: bayY + 96 },
      ];
      c.strokeStyle = "rgba(74, 222, 128, 0.65)";
      c.lineWidth = 2;
      nodes.forEach((n1, i) => nodes.forEach((n2, j) => {
        if (i < j) {
          c.beginPath(); c.moveTo(n1.nx, n1.ny); c.lineTo(n2.nx, n2.ny); c.stroke();
        }
      }));
      nodes.forEach((n, idx) => {
        const pulse = 1 + Math.sin(t * 4 * flowSpeed + idx) * 0.12;
        c.beginPath(); c.arc(n.nx, n.ny, 13 * pulse, 0, Math.PI * 2);
        c.fillStyle = "#064e3b"; c.fill();
        c.strokeStyle = "#4ade80"; c.lineWidth = 2; c.stroke();
        c.fillStyle = "#bbf7d0";
        c.beginPath(); c.arc(n.nx, n.ny - 3, 4, 0, Math.PI * 2); c.fill();
        c.beginPath(); c.arc(n.nx, n.ny + 7, 6.5, Math.PI, 0); c.fill();
      });
    } else if (m.id === "seo") {
      const barBaseY = bayY + 110;
      const barW = 14;
      const heights = [26, 42, 36, 58, 76];
      heights.forEach((bh, idx) => {
        const animH = bh + Math.sin(t * 3.5 * flowSpeed + idx * 0.8) * 6;
        const bx = bayX + 22 + idx * 22;
        const bGrad = c.createLinearGradient(bx, barBaseY - animH, bx, barBaseY);
        bGrad.addColorStop(0, "#fef08a");
        bGrad.addColorStop(1, "#ca8a04");
        c.fillStyle = bGrad;
        drawRoundedRect(c, bx, barBaseY - animH, barW, animH, 3);
        c.fill();
      });
      c.strokeStyle = "#fef9c3"; c.lineWidth = 3.5;
      c.beginPath();
      c.moveTo(bayX + 24, bayY + 64); c.lineTo(bayX + 54, bayY + 44);
      c.lineTo(bayX + 78, bayY + 52); c.lineTo(bayX + 118, bayY + 22);
      c.stroke();
      c.fillStyle = "#fef9c3";
      c.beginPath();
      c.moveTo(bayX + 122, bayY + 19); c.lineTo(bayX + 108, bayY + 21); c.lineTo(bayX + 118, bayY + 32);
      c.closePath(); c.fill();
    }
    c.restore();
  }

  function renderCeoCoreOrb(c, t) {
    const { x, y, r } = CEO_CORE;
    const busy = hubState.status === "orchestrating" || hubState.status === "working";
    c.save();

    // The human CEO card is rendered by HTML above the canvas. Keep only a
    // restrained halo/connection target here so the old abstract orb is gone.
    const pulse = r * (1.05 + Math.sin(t * (busy ? 4.2 : 2.2)) * 0.08);
    const aura = c.createRadialGradient(x, y, r * 0.12, x, y, r * 1.4);
    aura.addColorStop(0, busy ? "rgba(110, 231, 183, 0.24)" : "rgba(255, 215, 0, 0.16)");
    aura.addColorStop(1, "rgba(255, 215, 0, 0)");
    c.fillStyle = aura;
    c.beginPath();
    c.arc(x, y, r * 1.35, 0, Math.PI * 2);
    c.fill();

    c.strokeStyle = busy ? "rgba(110, 231, 183, 0.7)" : "rgba(255, 215, 0, 0.52)";
    c.lineWidth = 2;
    c.setLineDash([5, 12]);
    c.lineDashOffset = -t * 16;
    c.beginPath();
    c.arc(x, y, pulse, 0, Math.PI * 2);
    c.stroke();
    c.setLineDash([]);
    c.restore();
  }

  function renderSurgeBursts(c, t) {
    for (let i = pulseBursts.length - 1; i >= 0; i--) {
      const burst = pulseBursts[i];
      burst.progress += 0.018 * flowSpeed;
      if (burst.progress >= 1) {
        pulseBursts.splice(i, 1);
        continue;
      }
      const pipe = burst.pipe;
      const idx = Math.min(pipe.samples.length - 1, Math.floor(burst.progress * pipe.samples.length));
      const pt = pipe.samples[idx];
      const pal = PALETTE[pipe.colorKey];
      c.save();
      const g = c.createRadialGradient(pt.x, pt.y, 2, pt.x, pt.y, 24);
      g.addColorStop(0, "#ffffff");
      g.addColorStop(0.4, pal.light);
      g.addColorStop(1, "rgba(0,0,0,0)");
      c.fillStyle = g;
      c.beginPath(); c.arc(pt.x, pt.y, 24, 0, Math.PI * 2); c.fill();
      c.restore();
    }
  }

  function triggerTaskSurge(colorKeyFilter = null) {
    pipePaths.forEach((pipe) => {
      if (!colorKeyFilter || pipe.colorKey === colorKeyFilter) {
        pulseBursts.push({ pipe, progress: 0 });
      }
    });
  }

  // ------------------------------------------------------------ interaction --
  function canvasCoords(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * (W / rect.width),
      y: (event.clientY - rect.top) * (H / rect.height),
    };
  }

  function nodeAt(pt) {
    if (Math.hypot(pt.x - CEO_CORE.x, pt.y - CEO_CORE.y) <= CEO_CORE.r + 10) {
      return {
        kind: "ceo", x: CEO_CORE.x, y: CEO_CORE.y, r: CEO_CORE.r + 8,
        name: "CEO CORE ORCHESTRATOR",
        desc: "Central autonomous executive intelligence directing all 6 multi-agent departments.",
        agentId: "ceo_core", colorKey: "gold",
      };
    }
    for (const m of MACHINES) {
      if (Math.abs(pt.x - m.x) < 92 && Math.abs(pt.y - m.y) < 130) {
        return { ...m, kind: "machine", r: 105, agentId: MACHINE_AGENT[m.id] };
      }
    }
    for (const s of SUB_AGENTS) {
      if (Math.abs(pt.x - s.x) < 65 && Math.abs(pt.y - s.y) < 55) {
        return {
          kind: "sub", x: s.x, y: s.y, r: 62, name: s.label,
          desc: "Specialized execution sub-agent processing real-time micro-tasks.",
          agentId: MACHINE_AGENT[s.parent], colorKey: s.colorKey,
        };
      }
    }
    return null;
  }

  const tooltip = document.getElementById("workflowTip");
  const wtTitle = document.getElementById("wtTitle");
  const wtDesc = document.getElementById("wtDesc");
  const wtStatus = document.getElementById("wtStatus");
  const stage = document.getElementById("stage");

  canvas.addEventListener("mousemove", (event) => {
    const found = nodeAt(canvasCoords(event));
    hoveredNode = found;
    if (!found || !tooltip) return;
    wtTitle.textContent = found.name;
    wtDesc.textContent = found.desc;
    const machineId = found.kind === "machine" ? found.id : found.kind === "sub" ? found.parent : null;
    const agent = machineId ? agentState[machineId] : hubState;
    wtStatus.textContent = statusLabel(agent && agent.status);
    wtStatus.style.color = statusColor(agent && agent.status, agent && agent.module_status === "pending");
    tooltip.classList.add("show");
    const stageRect = stage.getBoundingClientRect();
    const x = Math.min(window.innerWidth - 280, event.clientX - stageRect.left + 16);
    const y = Math.min(window.innerHeight - 130, event.clientY - stageRect.top + 18);
    tooltip.style.left = `${Math.max(8, x)}px`;
    tooltip.style.top = `${Math.max(8, y)}px`;
  });

  canvas.addEventListener("mouseleave", () => {
    hoveredNode = null;
    if (tooltip) tooltip.classList.remove("show");
  });

  canvas.addEventListener("click", () => {
    if (hoveredNode && hoveredNode.agentId && typeof window.openChat === "function") {
      window.openChat(hoveredNode.agentId);
    } else {
      triggerTaskSurge(hoveredNode ? hoveredNode.colorKey : null);
    }
  });

  // ------------------------------------------------------------------- HUD --
  const speedSlider = document.getElementById("speedSlider");
  const speedValue = document.getElementById("speedValue");
  const surgeBtn = document.getElementById("surgeBtn");
  const pauseBtn = document.getElementById("pauseBtn");
  const fpsCounter = document.getElementById("fpsCounter");

  if (speedSlider) {
    speedSlider.addEventListener("input", (event) => {
      flowSpeed = parseFloat(event.target.value);
      if (speedValue) speedValue.textContent = `${flowSpeed.toFixed(1)}x`;
    });
  }
  if (surgeBtn) surgeBtn.addEventListener("click", () => triggerTaskSurge(null));
  if (pauseBtn) {
    pauseBtn.addEventListener("click", () => {
      isPaused = !isPaused;
      pauseBtn.textContent = isPaused ? "Resume" : "Pause";
    });
  }

  renderStaticScene(sctx);
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => renderStaticScene(sctx));
  }

  let animTime = 0;
  let lastPerf = performance.now();
  let frameCount = 0;
  let fpsTimer = lastPerf;

  function loop(now) {
    const dt = Math.min(0.05, (now - lastPerf) / 1000);
    lastPerf = now;
    if (!isPaused) animTime += dt;
    try {
      renderDynamicFrame(animTime);
    } catch (err) {
      console.error("workflow render error:", err && err.stack ? err.stack : err);
    }

    if (fpsCounter) {
      frameCount++;
      if (now - fpsTimer >= 500) {
        const fps = Math.min(60, Math.round((frameCount * 1000) / (now - fpsTimer)));
        fpsCounter.textContent = `${fps} FPS`;
        frameCount = 0;
        fpsTimer = now;
      }
    }
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  window.WorkflowScene = {
    setState(agents, hub) {
      agentState = {};
      (agents || []).forEach((agent) => {
        const machineId = AGENT_MACHINE[agent.id];
        if (machineId) agentState[machineId] = agent;
      });
      hubState = hub || {};
    },
    setSpeed(value) {
      flowSpeed = Math.max(0.4, Math.min(2.8, Number(value) || 1.2));
      if (speedSlider) speedSlider.value = String(flowSpeed);
      if (speedValue) speedValue.textContent = `${flowSpeed.toFixed(1)}x`;
    },
    surge: triggerTaskSurge,
    renderTo(target, scale, time = performance.now() / 1000) {
      const width = Math.round(W * scale);
      const height = Math.round(H * scale);
      target.width = width;
      target.height = height;
      const c = target.getContext("2d", { alpha: false });
      c.save();
      c.setTransform(scale, 0, 0, scale, 0, 0);
      renderStaticScene(c);
      renderAllPipeDataStreams(c, time);
      MACHINES.forEach((m) => renderAnimatedMachineBay(c, m, time));
      MACHINES.forEach((m) => renderMachineTelemetry(c, m, time));
      renderCeoCoreOrb(c, time);
      c.restore();
    },
    getCanvas() {
      return canvas;
    },
    getFps() {
      return fpsCounter ? fpsCounter.textContent : "";
    },
  };
})();
