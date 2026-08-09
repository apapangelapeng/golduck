(() => {
  const canvas = document.getElementById("board");
  const ctx = canvas.getContext("2d");
  const panel = document.querySelector(".panel");
  const levelSelect = document.getElementById("level");
  const seedInput = document.getElementById("seed");
  const meta = document.getElementById("meta");
  const status = document.getElementById("status");
  const showGrid = document.getElementById("show-grid");
  const showCanvas = document.getElementById("show-canvas");
  const showCells = document.getElementById("show-cells");
  const solutionSelect = document.getElementById("solution");
  const generationInput = document.getElementById("generations");
  const runNav = document.getElementById("run-nav");
  const runLabel = document.getElementById("run-label");
  const scorePanel = document.getElementById("score-panel");
  const scoreSeed = document.getElementById("score-seed");
  const scoreStatus = document.getElementById("score-status");
  const scoreTotal = document.getElementById("score-total");
  const scoreTotalLabel = document.getElementById("score-total-label");
  const scoreLevels = document.getElementById("score-levels");
  const scoreNote = document.getElementById("score-note");
  const visualizerView = document.getElementById("visualizer-view");
  const ideaLabView = document.getElementById("idea-lab-view");
  const secretView = document.getElementById("secret-view");
  const comparisonView = document.getElementById("comparison-view");
  const scoringView = document.getElementById("scoring-view");
  const visualizerTab = document.getElementById("tab-visualizer");
  const ideaLabTab = document.getElementById("tab-idea-lab");
  const secretTab = document.getElementById("tab-secret");
  const comparisonTab = document.getElementById("tab-comparison");
  const scoringTab = document.getElementById("tab-scoring");
  const secretLevelSelect = document.getElementById("secret-level");
  const secretSeedInput = document.getElementById("secret-seed");
  const secretRandom = document.getElementById("secret-random");
  const secretCanvas = document.getElementById("secret-board");
  const secretCtx = secretCanvas.getContext("2d");
  const secretValue = document.getElementById("secret-value");
  const secretCellCount = document.getElementById("secret-cell-count");
  const secretSize = document.getElementById("secret-size");
  const secretStatus = document.getElementById("secret-status");
  const comparisonLevelSelect = document.getElementById("comparison-level");
  const comparisonGenerate = document.getElementById("comparison-generate");
  const comparisonFillEmpty = document.getElementById("comparison-fill-empty");
  const comparisonRestoreControls = document.getElementById(
    "comparison-restore-controls"
  );
  const comparisonRestoreSelect = document.getElementById(
    "comparison-restore-select"
  );
  const comparisonRestore = document.getElementById("comparison-restore");
  const comparisonRefresh = document.getElementById("comparison-refresh");
  const comparisonBatch = document.getElementById("comparison-batch");
  const comparisonBatchStatus = document.getElementById("comparison-batch-status");
  const comparisonBatchCount = document.getElementById("comparison-batch-count");
  const comparisonBatchProgress = document.getElementById("comparison-batch-progress");
  const comparisonStatus = document.getElementById("comparison-status");
  const comparisonTable = document.getElementById("comparison-table");
  const scoringSolution = document.getElementById("scoring-solution");
  const scoringSeed = document.getElementById("scoring-seed");
  const scoringStatus = document.getElementById("scoring-status");
  const scoringCalculations = document.getElementById("scoring-calculations");
  const scoringTotal = document.getElementById("scoring-total");
  const DEFAULT_LEVEL = 3;
  const SOLUTION_POLL_INTERVAL_MS = 1000;
  const SOLUTIONS_CHANGED_EVENT = "golduck:solutions-changed";
  const COMPARISON_BATCH_SIZE = 10;
  const COMPARISON_EXCLUDED_STORAGE_KEY = "golduck-comparison-excluded-solutions";
  const VIDEO_GENERATIONS_PER_SECOND = 120;
  const VIDEO_CAPTURE_FPS = 60;
  const VIDEO_EXPORT_LABEL = "Export video · 120 gen/s";
  const REQUESTED_PARAMS = new URLSearchParams(window.location.search);
  const REQUESTED_TAB = REQUESTED_PARAMS.get("tab");
  const REQUESTED_SOLUTION = REQUESTED_PARAMS.get("solution");
  const VALID_TABS = new Set([
    "visualizer",
    "idea-lab",
    "secret",
    "comparison",
    "scoring",
  ]);
  const INITIAL_TAB = VALID_TABS.has(REQUESTED_TAB) ? REQUESTED_TAB : "visualizer";

  const UINT64_MASK = 0xffffffffffffffffn;
  const BIT_STATE_LABELS = {
    "known-correct": "Known · correct",
    "known-incorrect": "Known · incorrect",
    "guess-correct": "Guess · correct",
    "guess-incorrect": "Guess · incorrect",
    unknown: "Unknown",
  };
  const BIT_STATE_ORDER = [
    "known-correct",
    "known-incorrect",
    "guess-correct",
    "guess-incorrect",
    "unknown",
  ];

  const COLORS = {
    secret: "#1f7a6c",
    contestant: "#c45c26",
    viewing: "#2a6f9e",
    canvas: "#7a9aa3",
    cell: "#0d3d36",
    solution: "#c9892d",
    gridMinor: "rgba(19, 38, 47, 0.055)",
    gridMajor: "rgba(19, 38, 47, 0.13)",
    gridText: "rgba(19, 38, 47, 0.58)",
    axis: "rgba(19, 38, 47, 0.48)",
  };

  let data = null;
  let solution = null;
  let runIndex = 0;
  let evaluation = null;
  let evaluationAbort = null;
  let evaluationSeq = 0;
  let solutionLoadSeq = 0;
  let solutionVersions = null;
  let solutionSyncRunning = false;
  let solutionPollTimer = null;
  let comparisonSeq = 0;
  let comparisonPayload = null;
  let comparisonBatchRunning = false;
  let comparisonBatchMode = null;
  let comparisonBatchLevel = null;
  let comparisonBatchSeeds = [];
  let comparisonBatchPollTimer = null;
  let comparisonBatchLastCompleted = -1;
  let comparisonMissingCellCount = 0;
  let secretPreview = null;
  let secretLoadSeq = 0;
  let secretLoading = false;
  const activeLevelIds = new Set();
  const levelScoringRules = new Map();
  const expandedComparisonScores = new Set();
  const expandedScoreLevels = new Set();
  const excludedComparisonSolutions = new Set();
  try {
    const storedExcluded = JSON.parse(
      window.localStorage.getItem(COMPARISON_EXCLUDED_STORAGE_KEY) || "[]"
    );
    if (Array.isArray(storedExcluded)) {
      for (const name of storedExcluded) {
        if (typeof name === "string") excludedComparisonSolutions.add(name);
      }
    }
  } catch (_error) {
    // Storage can be unavailable in private or locked-down browser contexts.
  }
  let sim = null;
  const play = { playing: false, speed: 30, pending: null, acc: 0, lastT: 0, raf: null };
  let videoExport = null;
  let view = { x: 0, y: 0, scale: 4 };
  let dragging = false;
  let lastPointer = null;

  function canvasBounds() {
    return data?.rects.find((rect) => rect.name === "canvas") || null;
  }

  function minimumViewScale() {
    const bounds = canvasBounds();
    if (!bounds) return 0.02;
    const padding = 1.08;
    return Math.max(
      0.0001,
      Math.min(
        canvas.clientWidth / (bounds.w * padding),
        canvas.clientHeight / (bounds.h * padding)
      )
    );
  }

  function clampViewToCanvas() {
    const bounds = canvasBounds();
    if (!bounds) return;

    view.scale = Math.min(80, Math.max(minimumViewScale(), view.scale));
    const halfWidth = canvas.clientWidth / (2 * view.scale);
    const halfHeight = canvas.clientHeight / (2 * view.scale);
    const centerX = bounds.x + bounds.w / 2;
    const centerY = bounds.y + bounds.h / 2;

    if (halfWidth * 2 >= bounds.w) {
      view.x = centerX;
    } else {
      view.x = Math.max(
        bounds.x + halfWidth,
        Math.min(bounds.x + bounds.w - halfWidth, view.x)
      );
    }

    if (halfHeight * 2 >= bounds.h) {
      view.y = centerY;
    } else {
      view.y = Math.max(
        bounds.y + halfHeight,
        Math.min(bounds.y + bounds.h - halfHeight, view.y)
      );
    }
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    clampViewToCanvas();
    draw();
  }

  function randomSeedHex() {
    const words = new Uint32Array(4);
    window.crypto.getRandomValues(words);
    return Array.from(words, (word) => word.toString(16).padStart(8, "0")).join("");
  }

  function setSecretStatus(message, error = false) {
    secretStatus.hidden = !message;
    secretStatus.textContent = message || "";
    secretStatus.classList.toggle("error", Boolean(message) && error);
  }

  function drawSecretPreview() {
    const width = secretCanvas.clientWidth;
    const height = secretCanvas.clientHeight;
    secretCtx.clearRect(0, 0, width, height);
    secretCtx.fillStyle = "#f4faf8";
    secretCtx.fillRect(0, 0, width, height);
    if (!secretPreview || width <= 0 || height <= 0) return;

    const patternWidth = Math.max(1, Number(secretPreview.secret_size?.w) || 1);
    const patternHeight = Math.max(1, Number(secretPreview.secret_size?.h) || 1);
    const padding = Math.min(48, Math.max(18, Math.min(width, height) * 0.08));
    const scale = Math.max(
      0.25,
      Math.min(
        (width - padding * 2) / patternWidth,
        (height - padding * 2) / patternHeight,
        28
      )
    );
    const drawnWidth = patternWidth * scale;
    const drawnHeight = patternHeight * scale;
    const offsetX = (width - drawnWidth) / 2;
    const offsetY = (height - drawnHeight) / 2;

    secretCtx.save();
    secretCtx.translate(offsetX, offsetY);
    if (scale >= 3) {
      secretCtx.beginPath();
      for (let x = 0; x <= patternWidth; x++) {
        secretCtx.moveTo(x * scale + 0.5, 0);
        secretCtx.lineTo(x * scale + 0.5, drawnHeight);
      }
      for (let y = 0; y <= patternHeight; y++) {
        secretCtx.moveTo(0, y * scale + 0.5);
        secretCtx.lineTo(drawnWidth, y * scale + 0.5);
      }
      secretCtx.strokeStyle = "rgba(19, 38, 47, 0.075)";
      secretCtx.lineWidth = 1;
      secretCtx.stroke();
    }

    const originX = Number(secretPreview.secret_origin?.x) || 0;
    const originY = Number(secretPreview.secret_origin?.y) || 0;
    const gap = scale >= 5 ? Math.min(1, scale * 0.12) : 0;
    secretCtx.fillStyle = COLORS.cell;
    for (const [absoluteX, absoluteY] of secretPreview.cells || []) {
      const x = (absoluteX - originX) * scale + gap / 2;
      const y = (absoluteY - originY) * scale + gap / 2;
      secretCtx.fillRect(x, y, Math.max(1, scale - gap), Math.max(1, scale - gap));
    }
    secretCtx.strokeStyle = "rgba(19, 38, 47, 0.24)";
    secretCtx.lineWidth = 1;
    secretCtx.strokeRect(0.5, 0.5, Math.max(0, drawnWidth - 1), Math.max(0, drawnHeight - 1));
    secretCtx.restore();
  }

  function resizeSecretCanvas() {
    const rect = secretCanvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    secretCanvas.width = Math.max(1, Math.floor(rect.width * dpr));
    secretCanvas.height = Math.max(1, Math.floor(rect.height * dpr));
    secretCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawSecretPreview();
  }

  function renderSecretPreview(payload) {
    secretValue.textContent = payload.secret_hex || "—";
    secretCellCount.textContent = Number(payload.cell_count || 0).toLocaleString(
      "en-US"
    );
    secretSize.textContent = `${payload.secret_size.w} × ${payload.secret_size.h}`;
    drawSecretPreview();
  }

  async function loadSecretPreview({ randomize = false } = {}) {
    const seq = ++secretLoadSeq;
    if (randomize || !secretSeedInput.value) {
      secretSeedInput.value = randomSeedHex();
    }
    const level = secretLevelSelect.value;
    if (!level) {
      setSecretStatus("Levels are loading…");
      return;
    }
    const seed = encodeURIComponent(secretSeedInput.value);
    secretLoading = true;
    secretRandom.disabled = true;
    setSecretStatus("Generating secret…");
    try {
      const response = await fetch(`/api/level/${level}?seed=${seed}`, {
        cache: "no-store",
      });
      const payload = await response.json();
      if (seq !== secretLoadSeq) return;
      if (!response.ok) {
        throw new Error(payload.error || "failed to generate secret");
      }
      secretPreview = payload;
      secretSeedInput.value = payload.seed_hex;
      renderSecretPreview(payload);
      setSecretStatus("");
    } catch (error) {
      if (seq !== secretLoadSeq) return;
      setSecretStatus(error.message || String(error), true);
    } finally {
      if (seq === secretLoadSeq) {
        secretLoading = false;
        secretRandom.disabled = false;
      }
    }
  }

  function worldToScreen(x, y) {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    return {
      x: (x - view.x) * view.scale + w / 2,
      y: (y - view.y) * view.scale + h / 2,
    };
  }

  function screenToWorld(sx, sy) {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    return {
      x: (sx - w / 2) / view.scale + view.x,
      y: (sy - h / 2) / view.scale + view.y,
    };
  }

  function fitToBounds(bounds, pad) {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const panelRight = w >= 720 ? Math.ceil(panel.getBoundingClientRect().right + 18) : 0;
    const availableWidth = Math.max(w - panelRight, 1);
    const sx = availableWidth / Math.max(bounds.w * (1 + pad), 1);
    const sy = h / Math.max(bounds.h * (1 + pad), 1);
    view.scale = Math.min(sx, sy, 40);
    const focusCenterX = bounds.x + bounds.w / 2;
    const targetScreenX = panelRight + availableWidth / 2;
    view.x = focusCenterX - (targetScreenX - w / 2) / view.scale;
    view.y = bounds.y + bounds.h / 2;
    clampViewToCanvas();
    draw();
  }

  function fitToFocus() {
    if (!data) return;
    fitToBounds(data.focus, 0.12);
  }

  function fitToCanvas() {
    if (!data) return;
    const bounds = data.rects.find((rect) => rect.name === "canvas");
    if (bounds) fitToBounds(bounds, 0.08);
  }

  function niceGridStep(scale) {
    const targetWorldSize = 80 / scale;
    const magnitude = 10 ** Math.floor(Math.log10(targetWorldSize));
    const normalized = targetWorldSize / magnitude;
    if (normalized <= 1) return magnitude;
    if (normalized <= 2) return 2 * magnitude;
    if (normalized <= 5) return 5 * magnitude;
    return 10 * magnitude;
  }

  function drawGridLines(step, color) {
    const topLeft = screenToWorld(0, 0);
    const bottomRight = screenToWorld(canvas.clientWidth, canvas.clientHeight);
    const x0 = Math.floor(topLeft.x / step) * step;
    const y0 = Math.floor(topLeft.y / step) * step;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = x0; x <= bottomRight.x; x += step) {
      const p = worldToScreen(x, 0);
      ctx.moveTo(Math.round(p.x) + 0.5, 0);
      ctx.lineTo(Math.round(p.x) + 0.5, canvas.clientHeight);
    }
    for (let y = y0; y <= bottomRight.y; y += step) {
      const p = worldToScreen(0, y);
      ctx.moveTo(0, Math.round(p.y) + 0.5);
      ctx.lineTo(canvas.clientWidth, Math.round(p.y) + 0.5);
    }
    ctx.stroke();
  }

  function drawGrid() {
    if (!showGrid.checked) return;
    const step = niceGridStep(view.scale);
    const minorStep = step / 5;
    if (minorStep * view.scale >= 12) drawGridLines(minorStep, COLORS.gridMinor);
    drawGridLines(step, COLORS.gridMajor);

    const origin = worldToScreen(0, 0);
    ctx.strokeStyle = COLORS.axis;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(Math.round(origin.x) + 0.5, 0);
    ctx.lineTo(Math.round(origin.x) + 0.5, canvas.clientHeight);
    ctx.moveTo(0, Math.round(origin.y) + 0.5);
    ctx.lineTo(canvas.clientWidth, Math.round(origin.y) + 0.5);
    ctx.stroke();
  }

  function drawRect(rect, color, fillAlpha, labelSlots) {
    const p = worldToScreen(rect.x, rect.y);
    const w = rect.w * view.scale;
    const h = rect.h * view.scale;
    ctx.fillStyle = color.replace(")", `, ${fillAlpha})`).replace("rgb", "rgba");
    if (color.startsWith("#")) {
      const r = parseInt(color.slice(1, 3), 16);
      const g = parseInt(color.slice(3, 5), 16);
      const b = parseInt(color.slice(5, 7), 16);
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${fillAlpha})`;
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(1.5, 2);
    ctx.fillRect(p.x, p.y, w, h);
    ctx.strokeRect(p.x, p.y, w, h);

    if (view.scale > 0.25) {
      const fontSize = Math.max(11, Math.min(14, 12 * Math.sqrt(view.scale)));
      const key = `${Math.round(p.x)},${Math.round(p.y)}`;
      const slot = labelSlots.get(key) || 0;
      labelSlots.set(key, slot + 1);
      ctx.fillStyle = color;
      ctx.font = `600 ${fontSize}px Sora, sans-serif`;
      ctx.fillText(rect.name, p.x + 6, p.y - 6 - slot * (fontSize + 4));
    }
  }

  function drawCanvasBoundary(rect) {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const p = worldToScreen(rect.x, rect.y);
    const right = p.x + rect.w * view.scale;
    const bottom = p.y + rect.h * view.scale;
    const clippedLeft = Math.max(0, Math.min(w, p.x));
    const clippedRight = Math.max(0, Math.min(w, right));
    const clippedTop = Math.max(0, Math.min(h, p.y));
    const clippedBottom = Math.max(0, Math.min(h, bottom));

    ctx.fillStyle = "rgba(19, 38, 47, 0.075)";
    ctx.fillRect(0, 0, w, clippedTop);
    ctx.fillRect(0, clippedBottom, w, h - clippedBottom);
    ctx.fillRect(0, clippedTop, clippedLeft, clippedBottom - clippedTop);
    ctx.fillRect(clippedRight, clippedTop, w - clippedRight, clippedBottom - clippedTop);

    ctx.save();
    ctx.strokeStyle = COLORS.canvas;
    ctx.lineWidth = 2.5;
    ctx.setLineDash([9, 6]);
    ctx.strokeRect(p.x, p.y, rect.w * view.scale, rect.h * view.scale);
    ctx.restore();

    if (p.y >= 0 && p.y <= h && right >= 0 && p.x <= w) {
      const label = `canvas boundary · ${rect.w.toLocaleString("en-US")} × ` +
        `${rect.h.toLocaleString("en-US")}`;
      const labelX = Math.max(10, Math.min(w - 220, p.x + 9));
      const labelY = Math.max(16, Math.min(h - 8, p.y + 18));
      ctx.fillStyle = COLORS.canvas;
      ctx.font = "500 11px IBM Plex Mono, ui-monospace, monospace";
      ctx.fillText(label, labelX, labelY);
    }
  }

  function drawCells() {
    if (!showCells.checked || !data) return;
    const size = Math.max(1, view.scale);
    ctx.fillStyle = COLORS.cell;
    for (const [x, y] of data.cells) {
      const p = worldToScreen(x, y);
      if (
        p.x < -size ||
        p.y < -size ||
        p.x > canvas.clientWidth + size ||
        p.y > canvas.clientHeight + size
      ) {
        continue;
      }
      ctx.fillRect(p.x, p.y, size, size);
    }
  }

  // --- Life simulation (playback) ---

  const LIFE_ENGINE = globalThis.GolduckLifeEngine;
  // Levels 3 and 4 routinely send cells beyond the legacy 256-cell preview
  // margin, so their playback universe must match the boundary we draw.
  const FULL_CANVAS_PLAYBACK_LEVELS = new Set([3, 4]);
  const SIM_MARGIN = 256;
  const SNAP_EVERY = 64;
  const MAX_PLAYBACK_SNAPSHOTS = 64;
  const MAX_PLAYBACK_SNAPSHOT_BYTES = 24 * 1024 * 1024;

  const simPlayBtn = document.getElementById("sim-play");
  const simResetBtn = document.getElementById("sim-reset");
  const simBackBtn = document.getElementById("sim-back");
  const simForwardBtn = document.getElementById("sim-fwd");
  const simSpeedSelect = document.getElementById("sim-speed");
  const simExportBtn = document.getElementById("sim-export");
  const simExportStatus = document.getElementById("sim-export-status");
  const simSlider = document.getElementById("sim-slider");
  const simGenLabel = document.getElementById("sim-gen");

  function setVideoExportStatus(message, error = false) {
    simExportStatus.hidden = !message;
    simExportStatus.textContent = message || "";
    simExportStatus.classList.toggle("error", Boolean(message) && error);
  }

  function updateVideoExportUI() {
    const exporting = Boolean(videoExport);
    const lockedControls = [
      simPlayBtn,
      simResetBtn,
      simBackBtn,
      simForwardBtn,
      simSpeedSelect,
      simSlider,
      generationInput,
      solutionSelect,
      levelSelect,
      seedInput,
    ];
    for (const control of lockedControls) control.disabled = exporting;
    simExportBtn.disabled = exporting || !data;
    simExportBtn.textContent = exporting
      ? `Exporting · gen ${sim?.gen || 0}/${sim?.limit || 0}`
      : VIDEO_EXPORT_LABEL;
    updateRunNav();
  }

  function activeRun() {
    if (!solution || !data) return null;
    const run = solution.runs[runIndex];
    return run && run.level === data.level ? run : null;
  }

  function defaultGenerationLimit() {
    const run = activeRun();
    if (run) return run.generations;
    return data ? Math.min(data.generations.max, 10000) : 1000;
  }

  function requestedGenerationLimit() {
    const raw = generationInput.value.trim();
    const value = Number(raw);
    const max = data ? data.generations.max : 10000;
    if (raw && Number.isInteger(value) && value >= 0 && value <= max) {
      return value;
    }
    return defaultGenerationLimit();
  }

  function useRunGenerationLimit() {
    const run = solution?.runs[runIndex];
    generationInput.value = String(
      run ? run.generations : defaultGenerationLimit()
    );
  }

  function packGrid(grid) {
    const packed = new Uint8Array((grid.length + 7) >> 3);
    for (let i = 0; i < grid.length; i++) {
      if (grid[i]) packed[i >> 3] |= 1 << (i & 7);
    }
    return packed;
  }

  function unpackGrid(packed, grid) {
    for (let i = 0; i < grid.length; i++) {
      grid[i] = (packed[i >> 3] >> (i & 7)) & 1;
    }
  }

  function createFullCanvasSim() {
    const bounds = canvasBounds();
    if (!bounds || !LIFE_ENGINE) return null;
    const run = activeRun();
    const seedCells = run ? data.cells.concat(run.cells) : data.cells;
    const cells = LIFE_ENGINE.packedCellsInRect(
      LIFE_ENGINE.cellsToPacked(seedCells),
      bounds
    );
    const limit = requestedGenerationLimit();
    return {
      mode: "full-canvas",
      bounds,
      cells,
      gen: 0,
      limit,
      snaps: new Map([[0, cells]]),
      snapshotBytes: cells.byteLength,
    };
  }

  function createSim() {
    if (!data) return null;
    if (FULL_CANVAS_PLAYBACK_LEVELS.has(data.level)) {
      return createFullCanvasSim();
    }
    const rects = data.rects.filter((r) => r.name !== "canvas");
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const r of rects) {
      minX = Math.min(minX, r.x);
      minY = Math.min(minY, r.y);
      maxX = Math.max(maxX, r.x + r.w);
      maxY = Math.max(maxY, r.y + r.h);
    }
    minX -= SIM_MARGIN;
    minY -= SIM_MARGIN;
    maxX += SIM_MARGIN;
    maxY += SIM_MARGIN;
    const w = maxX - minX;
    const h = maxY - minY;
    const grid = new Uint8Array(w * h);
    const run = activeRun();
    const seedCells = run ? data.cells.concat(run.cells) : data.cells;
    for (const [x, y] of seedCells) {
      const gx = x - minX;
      const gy = y - minY;
      if (gx >= 0 && gx < w && gy >= 0 && gy < h) grid[gy * w + gx] = 1;
    }
    const limit = requestedGenerationLimit();
    const s = {
      minX,
      minY,
      w,
      h,
      grid,
      scratch: new Uint8Array(w * h),
      gen: 0,
      limit,
      snaps: [packGrid(grid)],
    };
    computeExtents(s);
    return s;
  }

  function saveFullCanvasSnapshot(s) {
    if (s.gen % SNAP_EVERY !== 0 || s.snaps.has(s.gen)) return;
    const snapshot = s.cells.slice();
    s.snaps.set(s.gen, snapshot);
    s.snapshotBytes += snapshot.byteLength;
    while (
      s.snaps.size > MAX_PLAYBACK_SNAPSHOTS
      || s.snapshotBytes > MAX_PLAYBACK_SNAPSHOT_BYTES
    ) {
      const removable = [...s.snaps.keys()].find(
        (generation) => generation !== 0 && generation !== s.gen
      );
      if (removable === undefined) break;
      s.snapshotBytes -= s.snaps.get(removable).byteLength;
      s.snaps.delete(removable);
    }
  }

  function fullCanvasSnapshotGeneration(s, target) {
    let nearest = 0;
    for (const generation of s.snaps.keys()) {
      if (generation <= target && generation > nearest) nearest = generation;
    }
    return nearest;
  }

  function restoreFullCanvasSnapshot(s, target) {
    const generation = fullCanvasSnapshotGeneration(s, target);
    s.cells = s.snaps.get(generation).slice();
    s.gen = generation;
  }

  function computeExtents(s) {
    let x0 = s.w;
    let x1 = -1;
    let y0 = s.h;
    let y1 = -1;
    for (let y = 0; y < s.h; y++) {
      const row = y * s.w;
      for (let x = 0; x < s.w; x++) {
        if (s.grid[row + x]) {
          if (x < x0) x0 = x;
          if (x > x1) x1 = x;
          if (y < y0) y0 = y;
          y1 = y;
        }
      }
    }
    s.ax0 = x0;
    s.ax1 = x1;
    s.ay0 = y0;
    s.ay1 = y1;
  }

  function simStep(s) {
    if (s.mode === "full-canvas") {
      s.cells = LIFE_ENGINE.stepPackedLifeInRect(
        s.cells,
        s.bounds
      );
      s.gen++;
      saveFullCanvasSnapshot(s);
      return;
    }
    const g = s.grid;
    const next = s.scratch;
    const w = s.w;
    const h = s.h;
    next.fill(0);
    let nx0 = w;
    let nx1 = -1;
    let ny0 = h;
    let ny1 = -1;
    if (s.ax1 >= s.ax0) {
      const bx0 = Math.max(1, s.ax0 - 1);
      const bx1 = Math.min(w - 1, s.ax1 + 2);
      const by0 = Math.max(1, s.ay0 - 1);
      const by1 = Math.min(h - 1, s.ay1 + 2);
      for (let y = by0; y < by1; y++) {
        const row = y * w;
        for (let x = bx0; x < bx1; x++) {
          const i = row + x;
          const n =
            g[i - w - 1] + g[i - w] + g[i - w + 1] +
            g[i - 1] + g[i + 1] +
            g[i + w - 1] + g[i + w] + g[i + w + 1];
          if (n === 3 || (n === 2 && g[i] === 1)) {
            next[i] = 1;
            if (x < nx0) nx0 = x;
            if (x > nx1) nx1 = x;
            if (y < ny0) ny0 = y;
            ny1 = y;
          }
        }
      }
    }
    s.ax0 = nx0;
    s.ax1 = nx1;
    s.ay0 = ny0;
    s.ay1 = ny1;
    s.scratch = g;
    s.grid = next;
    s.gen++;
    if (s.gen % SNAP_EVERY === 0) {
      const idx = s.gen / SNAP_EVERY;
      if (!s.snaps[idx]) s.snaps[idx] = packGrid(s.grid);
    }
  }

  function ensureSim() {
    if (!sim) sim = createSim();
    return sim;
  }

  function resetSim() {
    sim = null;
    play.playing = false;
    play.pending = null;
    play.acc = 0;
    updateSimUI();
  }

  function updateSimUI() {
    simPlayBtn.textContent = play.playing ? "❚❚" : "▶";
    if (!sim) {
      const limit = requestedGenerationLimit();
      simSlider.max = String(limit);
      simSlider.value = "0";
      simGenLabel.textContent = "gen 0";
      updateVideoExportUI();
      return;
    }
    simSlider.max = String(sim.limit);
    simSlider.value = String(sim.gen);
    simGenLabel.textContent = `gen ${sim.gen}/${sim.limit}`;
    updateVideoExportUI();
  }

  function releaseVideoExport(state) {
    for (const track of state.stream.getTracks()) track.stop();
    play.speed = state.previousSpeed;
    play.playing = false;
    play.pending = null;
    play.acc = 0;
    if (videoExport === state) videoExport = null;
    updateSimUI();
  }

  function completeVideoExport(state) {
    if (videoExport !== state) return;
    if (state.error) {
      releaseVideoExport(state);
      setVideoExportStatus(state.error.message || String(state.error), true);
      return;
    }

    const mimeType = state.recorder.mimeType || state.mimeType || "video/webm";
    const blob = new Blob(state.chunks, { type: mimeType });
    if (!blob.size) {
      releaseVideoExport(state);
      setVideoExportStatus("The browser produced an empty video.", true);
      return;
    }

    const run = activeRun();
    const filename = globalThis.GolduckPlaybackState.videoExportFilename({
      solutionName: solution?.solution,
      level: data?.level,
      runNumber: run?.level_run,
      generations: state.limit,
      mimeType,
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    releaseVideoExport(state);
    setVideoExportStatus(`Downloaded ${filename}`);
  }

  function finishVideoExport() {
    const state = videoExport;
    if (!state || state.finishing) return;
    state.finishing = true;
    play.playing = false;
    play.acc = 0;
    updateSimUI();
    draw();
    setTimeout(() => {
      if (videoExport !== state) return;
      if (state.recorder.state !== "inactive") state.recorder.stop();
      else completeVideoExport(state);
    }, 100);
  }

  function startVideoExport() {
    if (videoExport) return;
    if (!data) {
      setVideoExportStatus("Load a level before exporting video.", true);
      return;
    }
    if (typeof MediaRecorder === "undefined" || typeof canvas.captureStream !== "function") {
      setVideoExportStatus("Video export is not supported by this browser.", true);
      return;
    }

    setVideoExportStatus("");
    resetSim();
    const playback = ensureSim();
    if (!playback) {
      setVideoExportStatus("Unable to initialize playback.", true);
      return;
    }
    draw();

    const mimeType = globalThis.GolduckPlaybackState.selectVideoMimeType(
      (candidate) => MediaRecorder.isTypeSupported(candidate)
    );
    let stream;
    let recorder;
    try {
      stream = canvas.captureStream(VIDEO_CAPTURE_FPS);
      const options = { videoBitsPerSecond: 8_000_000 };
      if (mimeType) options.mimeType = mimeType;
      recorder = new MediaRecorder(stream, options);
    } catch (error) {
      if (stream) for (const track of stream.getTracks()) track.stop();
      setVideoExportStatus(error.message || String(error), true);
      return;
    }

    const state = {
      recorder,
      stream,
      mimeType,
      chunks: [],
      limit: playback.limit,
      previousSpeed: play.speed,
      finishing: false,
      error: null,
    };
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data?.size) state.chunks.push(event.data);
    });
    recorder.addEventListener("error", (event) => {
      state.error = event.error || new Error("Video recording failed.");
      if (recorder.state !== "inactive") recorder.stop();
      else completeVideoExport(state);
    });
    recorder.addEventListener("stop", () => completeVideoExport(state), { once: true });

    videoExport = state;
    play.speed = VIDEO_GENERATIONS_PER_SECOND;
    play.playing = true;
    play.pending = null;
    play.acc = 0;
    play.lastT = performance.now();
    setVideoExportStatus(
      `Recording ${playback.limit} generations at ${VIDEO_GENERATIONS_PER_SECOND} gen/s…`
    );
    updateSimUI();
    try {
      recorder.start(250);
    } catch (error) {
      state.error = error;
      completeVideoExport(state);
      return;
    }
    draw();
    scheduleTick();
  }

  function scheduleTick() {
    if (play.raf === null) {
      play.lastT = performance.now();
      if (document.hidden) {
        const id = setTimeout(() => {
          if (play.raf === id) simTick(performance.now());
        }, 33);
        play.raf = id;
      } else {
        play.raf = requestAnimationFrame(simTick);
      }
    }
  }

  function seekTo(target) {
    const s = ensureSim();
    if (!s) return;
    const clamped = Math.max(0, Math.min(s.limit, target));
    const backSteps = clamped < s.gen
      ? s.mode === "full-canvas"
        ? clamped - fullCanvasSnapshotGeneration(s, clamped)
        : clamped % SNAP_EVERY
      : 0;
    const fwdSteps = clamped > s.gen ? clamped - s.gen : 0;
    if (Math.max(backSteps, fwdSteps) <= 200) {
      // small jumps run synchronously so stepping works even when
      // rAF is throttled (hidden tab)
      if (clamped < s.gen) {
        if (s.mode === "full-canvas") {
          restoreFullCanvasSnapshot(s, clamped);
        } else {
          const idx = Math.min(Math.floor(clamped / SNAP_EVERY), s.snaps.length - 1);
          unpackGrid(s.snaps[idx], s.grid);
          s.gen = idx * SNAP_EVERY;
          computeExtents(s);
        }
      }
      while (s.gen < clamped) simStep(s);
      play.pending = null;
      updateSimUI();
      draw();
      return;
    }
    play.pending = clamped;
    scheduleTick();
  }

  function simTick(t) {
    play.raf = null;
    if (!sim) return;
    const budget = play.speed >= 1000 || play.pending !== null ? 30 : 14;
    const deadline = performance.now() + budget;
    let advanced = false;

    if (play.pending !== null) {
      if (play.pending < sim.gen) {
        if (sim.mode === "full-canvas") {
          restoreFullCanvasSnapshot(sim, play.pending);
        } else {
          const idx = Math.min(
            Math.floor(play.pending / SNAP_EVERY),
            sim.snaps.length - 1
          );
          unpackGrid(sim.snaps[idx], sim.grid);
          sim.gen = idx * SNAP_EVERY;
          computeExtents(sim);
        }
        advanced = true;
      }
      while (sim.gen < play.pending && performance.now() < deadline) {
        simStep(sim);
        advanced = true;
      }
      if (sim.gen >= play.pending) play.pending = null;
    } else if (play.playing) {
      const dt = Math.min(0.25, (t - play.lastT) / 1000);
      play.acc = Math.min(play.acc + dt * play.speed, 100000);
      while (play.acc >= 1 && sim.gen < sim.limit && performance.now() < deadline) {
        simStep(sim);
        play.acc -= 1;
        advanced = true;
      }
      if (sim.gen >= sim.limit) {
        play.playing = false;
        play.acc = 0;
      }
    }

    play.lastT = t;
    if (advanced) {
      updateSimUI();
      draw();
    }
    if (videoExport && sim.gen >= sim.limit) finishVideoExport();
    if (play.playing || play.pending !== null) scheduleTick();
    else updateSimUI();
  }

  function drawSimCells() {
    const size = Math.max(1, view.scale);
    ctx.fillStyle = COLORS.cell;
    if (sim.mode === "full-canvas") {
      const stride = LIFE_ENGINE.CELL_KEY_STRIDE;
      const offset = LIFE_ENGINE.CELL_KEY_OFFSET;
      for (const key of sim.cells) {
        const packedX = Math.floor(key / stride);
        const wx = packedX - offset;
        const wy = key - packedX * stride - offset;
        const p = worldToScreen(wx, wy);
        if (
          p.x < -size
          || p.y < -size
          || p.x > canvas.clientWidth + size
          || p.y > canvas.clientHeight + size
        ) {
          continue;
        }
        ctx.fillRect(p.x, p.y, size, size);
      }
      return;
    }
    const tl = screenToWorld(0, 0);
    const br = screenToWorld(canvas.clientWidth, canvas.clientHeight);
    const x0 = Math.max(sim.minX, Math.floor(tl.x) - 1);
    const x1 = Math.min(sim.minX + sim.w, Math.ceil(br.x) + 1);
    const y0 = Math.max(sim.minY, Math.floor(tl.y) - 1);
    const y1 = Math.min(sim.minY + sim.h, Math.ceil(br.y) + 1);
    for (let wy = y0; wy < y1; wy++) {
      const row = (wy - sim.minY) * sim.w - sim.minX;
      for (let wx = x0; wx < x1; wx++) {
        if (sim.grid[row + wx]) {
          const p = worldToScreen(wx, wy);
          ctx.fillRect(p.x, p.y, size, size);
        }
      }
    }
  }

  function drawSolutionCells() {
    if (!solution || !data) return;
    const run = solution.runs[runIndex];
    if (!run || run.level !== data.level) return;
    const size = Math.max(1, view.scale);
    ctx.fillStyle = COLORS.solution;
    for (const [x, y] of run.cells) {
      const p = worldToScreen(x, y);
      if (
        p.x < -size ||
        p.y < -size ||
        p.x > canvas.clientWidth + size ||
        p.y > canvas.clientHeight + size
      ) {
        continue;
      }
      ctx.fillRect(p.x, p.y, size, size);
    }
  }

  function draw() {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    drawGrid();
    if (!data) return;

    const labelSlots = new Map();
    const canvasRect = data.rects.find((rect) => rect.name === "canvas");
    if (showCanvas.checked && canvasRect) drawCanvasBoundary(canvasRect);
    for (const rect of data.rects) {
      if (rect.name === "canvas") continue;
      const color = COLORS[rect.name] || "#333";
      drawRect(rect, color, 0.12, labelSlots);
    }
    if (sim && sim.gen > 0) {
      drawSimCells();
    } else {
      drawCells();
      drawSolutionCells();
    }
  }

  function setStatus(message) {
    if (!message) {
      status.hidden = true;
      status.textContent = "";
      return;
    }
    status.hidden = false;
    status.textContent = message;
  }

  function renderMeta(payload) {
    meta.innerHTML = "";
    const canvasRect = payload.rects.find((rect) => rect.name === "canvas");
    const formatInt = (value) => value.toLocaleString("en-US");
    const rows = [
      ...(payload.seed_hex ? [["seed", payload.seed_hex]] : []),
      ["secret", payload.secret_hex],
      ["cells", String(payload.cell_count)],
      ["gens", `${payload.generations.min}–${payload.generations.max}`],
      ["max runs", String(payload.max_runs)],
      [
        "canvas",
        `${formatInt(canvasRect.w)}×${formatInt(canvasRect.h)}`,
      ],
      [
        "x bounds",
        `${formatInt(canvasRect.x)}…${formatInt(canvasRect.x + canvasRect.w - 1)}`,
      ],
      [
        "y bounds",
        `${formatInt(canvasRect.y)}…${formatInt(canvasRect.y + canvasRect.h - 1)}`,
      ],
      [
        "secret box",
        `${payload.secret_origin.x},${payload.secret_origin.y} ${payload.secret_size.w}×${payload.secret_size.h}`,
      ],
    ];
    for (const [k, v] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      meta.append(dt, dd);
    }
  }

  function formatScore(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return "pending";
    }
    return Math.round(Number(value)).toLocaleString("en-US");
  }

  function formatComparisonScore(value) {
    return Number(value).toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    });
  }

  function formatComparisonSeed(seed) {
    return seed.length > 6 ? `${seed.slice(0, 3)}…${seed.slice(-3)}` : seed;
  }

  function persistExcludedComparisonSolutions() {
    try {
      window.localStorage.setItem(
        COMPARISON_EXCLUDED_STORAGE_KEY,
        JSON.stringify([...excludedComparisonSolutions])
      );
    } catch (_error) {
      // Row removal still applies for this page session when storage is unavailable.
    }
  }

  function visibleComparisonRows(payload = comparisonPayload) {
    const rows = Array.isArray(payload?.solutions) ? payload.solutions : [];
    return rows.filter((row) => !excludedComparisonSolutions.has(row.solution));
  }

  function isActiveLevel(level) {
    return activeLevelIds.has(Number(level));
  }

  function activeEvaluationLevels() {
    return Object.values(evaluation?.levels || {})
      .filter((level) => isActiveLevel(level.level))
      .sort((first, second) => first.level - second.level);
  }

  function activeEvaluationTotals(levels = activeEvaluationLevels()) {
    const sum = (field) => levels.reduce((total, level) => {
      const value = Number(level[field]);
      return total + (Number.isFinite(value) ? value : 0);
    }, 0);
    return {
      score: sum("score"),
      potentialScore: sum("potential_score"),
      hasPotentialScore: levels.some((level) =>
        Number.isFinite(Number(level.potential_score))
      ),
      submittedLevels: levels.filter((level) => level.submitted).length,
      levelCount: levels.length,
    };
  }

  function selectedComparisonLevel() {
    if (comparisonLevelSelect.value === "total") return null;
    const level = Number(comparisonLevelSelect.value);
    return Number.isInteger(level) ? level : null;
  }

  function comparisonScopeLabel(level = selectedComparisonLevel()) {
    return level === null ? "Levels 3–4 total" : `Level ${level}`;
  }

  function comparisonScore(row, seed, level = selectedComparisonLevel()) {
    const levels = Array.isArray(row.breakdowns?.[seed])
      ? row.breakdowns[seed]
      : [];
    if (level === null) {
      if (!activeLevelIds.size) return undefined;
      const scores = [...activeLevelIds].map((activeLevel) =>
        levels.find((entry) => Number(entry.level) === activeLevel)?.score
      );
      if (scores.some((score) => !Number.isFinite(Number(score)))) return undefined;
      return scores.reduce((total, score) => total + Number(score), 0);
    }
    return levels.find((entry) => Number(entry.level) === level)?.score;
  }

  function visibleComparisonWinners(rows, seeds, level) {
    const winners = {};
    for (const seed of seeds) {
      let highest = -Infinity;
      let names = [];
      for (const row of rows) {
        const score = comparisonScore(row, seed, level);
        if (score === null || score === undefined || !Number.isFinite(Number(score))) {
          continue;
        }
        const numericScore = Number(score);
        if (numericScore > highest) {
          highest = numericScore;
          names = [row.solution];
        } else if (numericScore === highest) {
          names.push(row.solution);
        }
      }
      winners[seed] = names;
    }
    return winners;
  }

  function updateComparisonActionState() {
    const rows = visibleComparisonRows();
    const scope = comparisonScopeLabel();
    const removedNames = Array.isArray(comparisonPayload?.solutions)
      ? comparisonPayload.solutions
          .map((row) => row.solution)
          .filter((name) => excludedComparisonSolutions.has(name))
      : [];
    comparisonGenerate.disabled = comparisonBatchRunning || rows.length === 0;
    comparisonFillEmpty.disabled =
      comparisonBatchRunning || comparisonMissingCellCount === 0;
    comparisonLevelSelect.disabled = comparisonBatchRunning;
    comparisonGenerate.textContent =
      comparisonBatchRunning && comparisonBatchMode === "random_seeds"
        ? `Scoring ${comparisonScopeLabel(comparisonBatchLevel)}…`
        : "Score 10 random seeds";
    comparisonGenerate.title = `Generate ${scope.toLowerCase()} for 10 new shared seeds`;
    comparisonFillEmpty.textContent =
      comparisonBatchRunning && comparisonBatchMode === "fill_empty"
        ? `Filling ${comparisonScopeLabel(comparisonBatchLevel)} cells…`
        : "Fill empty cells";
    comparisonFillEmpty.title = comparisonMissingCellCount
      ? `Score ${comparisonMissingCellCount} empty table cell${
          comparisonMissingCellCount === 1 ? "" : "s"
        } for ${scope.toLowerCase()} in the background`
      : `Every visible solution and seed already has a ${scope.toLowerCase()}`;
    const selectedRemovedName = comparisonRestoreSelect.value;
    const restoreOptions = removedNames.map((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      return option;
    });
    comparisonRestoreSelect.replaceChildren(...restoreOptions);
    if (removedNames.includes(selectedRemovedName)) {
      comparisonRestoreSelect.value = selectedRemovedName;
    }
    comparisonRestoreControls.hidden = removedNames.length === 0;
    comparisonRestoreSelect.disabled =
      comparisonBatchRunning || removedNames.length === 0;
    comparisonRestore.disabled = comparisonBatchRunning || removedNames.length === 0;
    comparisonRestore.textContent = "Restore selected";
    comparisonRestore.title = removedNames.length
      ? `${removedNames.length} removed solution${removedNames.length === 1 ? "" : "s"}`
      : "";
  }

  function renderComparison(payload) {
    comparisonPayload = payload;
    comparisonTable.innerHTML = "";
    let seeds = Array.isArray(payload.seeds) ? [...payload.seeds] : [];
    const allRows = Array.isArray(payload.solutions) ? payload.solutions : [];
    const rows = visibleComparisonRows(payload);
    const selectedLevel = selectedComparisonLevel();
    const scope = comparisonScopeLabel(selectedLevel);
    const removedCount = allRows.length - rows.length;
    if (comparisonBatchSeeds.length) {
      const displayedSeeds = new Set(seeds);
      seeds.push(...comparisonBatchSeeds.filter((seed) => !displayedSeeds.has(seed)));
    }
    comparisonMissingCellCount = rows.reduce((count, row) => {
      return (
        count +
        seeds.filter((seed) => {
          const score = comparisonScore(row, seed, selectedLevel);
          return score === null || score === undefined || !Number.isFinite(Number(score));
        }).length
      );
    }, 0);
    const winnersBySeed = visibleComparisonWinners(rows, seeds, selectedLevel);
    updateComparisonActionState();

    const savedSeedCount = Array.isArray(payload.seeds) ? payload.seeds.length : 0;
    const scoringSeedCount = seeds.length - savedSeedCount;
    comparisonStatus.textContent =
      `${scope} · ${rows.length} solution${rows.length === 1 ? "" : "s"} · ` +
      `${savedSeedCount} saved seed${savedSeedCount === 1 ? "" : "s"}` +
      (removedCount ? ` · ${removedCount} removed` : "") +
      (scoringSeedCount
        ? ` · ${scoringSeedCount} seed${scoringSeedCount === 1 ? "" : "s"} scoring`
        : "");
    comparisonStatus.classList.remove("error");

    if (rows.length === 0) {
      const empty = document.createElement("p");
      empty.className = "comparison-empty";
      empty.textContent =
        "All solution rows are removed. Restore rows to include them in score batches.";
      comparisonTable.append(empty);
      return;
    }

    if (seeds.length === 0) {
      const empty = document.createElement("p");
      empty.className = "comparison-empty";
      empty.textContent =
        "No completed scores have been saved yet. Run a solution in the Visualizer tab first.";
      comparisonTable.append(empty);
      return;
    }

    const table = document.createElement("table");
    table.className = "comparison-table";
    table.setAttribute("aria-label", `Saved ${scope.toLowerCase()}s by seed`);
    const head = document.createElement("thead");
    const headerRow = document.createElement("tr");
    const solutionHeader = document.createElement("th");
    solutionHeader.scope = "col";
    solutionHeader.className = "solution-column";
    solutionHeader.textContent = "Solution";
    headerRow.append(solutionHeader);

    for (const seed of seeds) {
      const header = document.createElement("th");
      header.scope = "col";
      header.title = seed;
      const label = document.createElement("span");
      label.className = "comparison-seed-label";
      label.textContent = "Seed";
      const value = document.createElement("code");
      value.className = "comparison-seed";
      value.textContent = formatComparisonSeed(seed);
      header.append(label, value);
      headerRow.append(header);
    }
    head.append(headerRow);
    table.append(head);

    const body = document.createElement("tbody");
    for (const row of rows) {
      const tableRow = document.createElement("tr");
      const solutionCell = document.createElement("th");
      solutionCell.scope = "row";
      solutionCell.className = "solution-column";
      const solutionName = document.createElement("span");
      solutionName.className = "comparison-solution-name";
      solutionName.textContent = row.solution;
      const removeRow = document.createElement("button");
      removeRow.type = "button";
      removeRow.className = "comparison-row-remove";
      removeRow.textContent = "Remove";
      removeRow.title = `Remove ${row.solution} from comparison and future score batches`;
      removeRow.disabled = comparisonBatchRunning;
      removeRow.addEventListener("click", () => {
        excludedComparisonSolutions.add(row.solution);
        persistExcludedComparisonSolutions();
        renderComparison(payload);
      });
      const solutionHeading = document.createElement("span");
      solutionHeading.className = "comparison-solution-heading";
      solutionHeading.append(solutionName, removeRow);
      const wins = Object.values(winnersBySeed).filter((seedWinners) =>
        seedWinners.includes(row.solution)
      ).length;
      const winCount = document.createElement("span");
      winCount.className = "comparison-win-count";
      winCount.textContent = `${wins} win${wins === 1 ? "" : "s"}`;
      if (wins === 0) winCount.classList.add("empty");
      solutionCell.append(solutionHeading);
      const solutionLevels = Array.isArray(row.level_workloads)
        ? row.level_workloads
        : [];
      if (solutionLevels.length) {
        const workloadLevels = document.createElement("span");
        workloadLevels.className = "comparison-solution-levels";
        for (const level of solutionLevels) {
          if (!isActiveLevel(level.level)) continue;
          if (selectedLevel !== null && level.level !== selectedLevel) continue;
          if (
            !Number.isInteger(level.level) ||
            !Number.isInteger(level.runs) ||
            level.runs < 0 ||
            !Number.isInteger(level.max_generations) ||
            level.max_generations < 0
          ) {
            continue;
          }
          const levelRunLabel = level.runs === 1 ? "run" : "runs";
          const levelGenerationLabel =
            level.max_generations === 1 ? "generation" : "generations";
          const workloadLevel = document.createElement("span");
          workloadLevel.className = "comparison-solution-level";
          workloadLevel.textContent =
            `L${level.level}: ${level.runs.toLocaleString("en-US")} ` +
            `${levelRunLabel} · max ` +
            `${level.max_generations.toLocaleString("en-US")} ` +
            levelGenerationLabel;
          workloadLevels.append(workloadLevel);
        }
        if (workloadLevels.childElementCount) solutionCell.append(workloadLevels);
      }
      solutionCell.append(winCount);
      tableRow.append(solutionCell);
      const breakdowns = row.breakdowns || {};

      for (const seed of seeds) {
        const scoreCell = document.createElement("td");
        scoreCell.className = "comparison-score clickable";
        scoreCell.tabIndex = 0;
        scoreCell.setAttribute(
          "aria-label",
          `Open ${row.solution} with seed ${seed} in the Visualizer`
        );
        scoreCell.addEventListener("click", () => openComparisonSolution(row, seed));
        scoreCell.addEventListener("keydown", (event) => {
          if (event.target !== scoreCell) return;
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          openComparisonSolution(row, seed);
        });
        const score = comparisonScore(row, seed, selectedLevel);
        if (
          score === null ||
          score === undefined ||
          !Number.isFinite(Number(score))
        ) {
          scoreCell.classList.add("missing");
          scoreCell.textContent = "—";
          scoreCell.title =
            `${row.solution} has no completed ${scope.toLowerCase()} for seed ${seed}. ` +
            "Use Fill empty cells to generate it.";
        } else {
          const numericScore = Number(score);
          const detailsKey = `${row.solution}:${seed}:total`;
          const scoreHeader = document.createElement("div");
          scoreHeader.className = "comparison-score-header";
          const totalLine = document.createElement("span");
          totalLine.className = "comparison-total-line";
          const totalValue = document.createElement("strong");
          totalValue.textContent = formatComparisonScore(numericScore);
          totalLine.append(totalValue);
          scoreHeader.append(totalLine);

          const winners = winnersBySeed[seed] || [];
          if (winners.includes(row.solution)) {
            scoreCell.classList.add("winner");
          }

          const levels = Array.isArray(breakdowns[seed])
            ? breakdowns[seed].filter((level) => isActiveLevel(level.level))
            : [];
          if (selectedLevel === null && levels.length) {
            const levelList = document.createElement("div");
            levelList.className = "comparison-level-breakdown";
            const levelToggle = document.createElement("button");
            levelToggle.type = "button";
            levelToggle.className = "comparison-level-toggle";
            const setExpanded = (expanded) => {
              levelList.hidden = !expanded;
              levelToggle.setAttribute("aria-expanded", String(expanded));
              levelToggle.textContent = expanded ? "Hide levels" : "Levels";
              if (expanded) expandedComparisonScores.add(detailsKey);
              else expandedComparisonScores.delete(detailsKey);
            };
            levelToggle.addEventListener("click", (event) => {
              event.stopPropagation();
              setExpanded(levelList.hidden);
            });
            for (const level of levels) {
              const levelRow = document.createElement("div");
              levelRow.className = "comparison-level-row";
              const levelLabel = document.createElement("span");
              levelLabel.textContent = `Level ${level.level}`;
              const levelValue = document.createElement("strong");
              levelValue.textContent =
                level.score !== null && Number.isFinite(Number(level.score))
                  ? formatComparisonScore(level.score)
                  : "—";
              levelRow.append(levelLabel, levelValue);
              levelList.append(levelRow);
            }
            setExpanded(expandedComparisonScores.has(detailsKey));
            scoreHeader.append(levelToggle);
            scoreCell.append(scoreHeader, levelList);
          } else {
            scoreCell.append(scoreHeader);
          }

          if (winners.includes(row.solution)) {
            const best = document.createElement("span");
            best.className = "comparison-best";
            best.textContent = "Highest";
            scoreCell.append(best);
          }

          scoreCell.title =
            `Open ${row.solution} · ${seed} · ${scope} · ` +
            numericScore.toLocaleString("en-US", { maximumFractionDigits: 6 });
        }
        tableRow.append(scoreCell);
      }
      body.append(tableRow);
    }
    table.append(body);
    comparisonTable.append(table);
  }

  async function loadComparison({ quiet = false } = {}) {
    const seq = ++comparisonSeq;
    comparisonRefresh.disabled = true;
    if (!quiet) {
      comparisonStatus.textContent = "Loading saved scores…";
      comparisonStatus.classList.remove("error");
    }
    try {
      const response = await fetch("/api/comparison", { cache: "no-store" });
      const payload = await response.json();
      if (seq !== comparisonSeq) return null;
      if (!response.ok) throw new Error(payload.error || "failed to load scores");
      renderComparison(payload);
      return payload;
    } catch (error) {
      if (seq !== comparisonSeq) return null;
      if (!quiet) {
        comparisonStatus.textContent = error.message || String(error);
        comparisonStatus.classList.add("error");
      }
      return null;
    } finally {
      if (seq === comparisonSeq) comparisonRefresh.disabled = false;
    }
  }

  function updateComparisonBatchProgress(batch) {
    const completed = Number(batch.completed || 0);
    const total = Number(batch.total || 0);
    const failures = Array.isArray(batch.failures) ? batch.failures : [];
    const fillEmpty = batch.mode === "fill_empty";
    const batchLevel = Number.isInteger(batch.level) ? batch.level : null;
    const scope = comparisonScopeLabel(batchLevel);
    const seedCount = Array.isArray(batch.seeds) ? batch.seeds.length : 0;
    const failed = failures.length;
    const done = completed >= total;
    comparisonBatch.hidden = false;
    comparisonBatch.classList.toggle("complete", done && failed === 0);
    comparisonBatch.classList.toggle("error", done && failed > 0);
    comparisonBatchCount.textContent = `${completed}/${total} evals`;
    comparisonBatchProgress.setAttribute("aria-valuemax", String(total));
    comparisonBatchProgress.setAttribute("aria-valuenow", String(completed));
    comparisonBatchProgress.firstElementChild.style.width =
      `${total ? (100 * completed) / total : 0}%`;

    if (!done) {
      comparisonBatchStatus.textContent =
        (fillEmpty
          ? `Filling ${total} empty ${scope.toLowerCase()} cell${
              total === 1 ? "" : "s"
            } in parallel`
          : `Background scoring ${scope.toLowerCase()} for ${
              seedCount || COMPARISON_BATCH_SIZE
            } seeds in parallel`) +
        (failed ? ` · ${failed} failed` : "");
    } else if (failed) {
      comparisonBatchStatus.textContent =
        `Batch finished · ${total - failed} saved · ${failed} failed`;
    } else if (fillEmpty) {
      comparisonBatchStatus.textContent =
        `Empty-cell fill complete · ${total} ${scope.toLowerCase()}${
          total === 1 ? "" : "s"
        } saved`;
    } else {
      comparisonBatchStatus.textContent =
        `Batch complete · ${scope} saved for ${
          seedCount || COMPARISON_BATCH_SIZE
        } seeds and included solutions`;
    }
    comparisonBatchStatus.title = failures
      .map((failure) => `${failure.solution} ${failure.seed}: ${failure.message}`)
      .join("\n");
  }

  function scheduleComparisonBatchPoll() {
    if (comparisonBatchPollTimer !== null) {
      window.clearTimeout(comparisonBatchPollTimer);
    }
    comparisonBatchPollTimer = window.setTimeout(loadComparisonBatchStatus, 1000);
  }

  function applyComparisonBatch(batch) {
    if (!batch) {
      comparisonBatchRunning = false;
      comparisonBatchMode = null;
      comparisonBatchLevel = null;
      comparisonBatchSeeds = [];
      comparisonBatch.hidden = true;
      if (comparisonPayload) renderComparison(comparisonPayload);
      if (comparisonBatchPollTimer !== null) {
        window.clearTimeout(comparisonBatchPollTimer);
        comparisonBatchPollTimer = null;
      }
      return;
    }
    comparisonBatchSeeds = Array.isArray(batch.seeds) ? batch.seeds : [];
    comparisonBatchRunning = batch.status === "running";
    comparisonBatchMode = batch.mode || "random_seeds";
    comparisonBatchLevel = Number.isInteger(batch.level) ? batch.level : null;
    if (comparisonBatchRunning) {
      comparisonLevelSelect.value =
        comparisonBatchLevel === null ? "total" : String(comparisonBatchLevel);
    }
    updateComparisonBatchProgress(batch);

    if (comparisonPayload) renderComparison(comparisonPayload);
    if (Number(batch.completed || 0) !== comparisonBatchLastCompleted) {
      comparisonBatchLastCompleted = Number(batch.completed || 0);
      loadComparison({ quiet: true });
    }
    if (comparisonBatchRunning) {
      scheduleComparisonBatchPoll();
    } else {
      if (comparisonBatchPollTimer !== null) {
        window.clearTimeout(comparisonBatchPollTimer);
        comparisonBatchPollTimer = null;
      }
      loadComparison({ quiet: true });
    }
  }

  async function loadComparisonBatchStatus() {
    try {
      const response = await fetch("/api/comparison/batch", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "failed to load background batch");
      }
      applyComparisonBatch(payload.batch);
    } catch (error) {
      comparisonBatch.hidden = false;
      comparisonBatchStatus.textContent = error.message || String(error);
      comparisonBatch.classList.add("error");
      if (comparisonBatchRunning) scheduleComparisonBatchPoll();
    }
  }

  async function runComparisonBatch(mode) {
    if (comparisonBatchRunning) return;
    const includedSolutions = visibleComparisonRows().map((row) => row.solution);
    if (includedSolutions.length === 0) return;
    const level = selectedComparisonLevel();
    comparisonBatchRunning = true;
    comparisonBatchMode = mode;
    comparisonBatchLevel = level;
    updateComparisonActionState();
    if (mode === "fill_empty") {
      comparisonFillEmpty.textContent = "Starting empty-cell fill…";
    } else {
      comparisonGenerate.textContent = "Starting background batch…";
    }
    try {
      const response = await fetch("/api/comparison/batch", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, solutions: includedSolutions, level }),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "failed to start background batch");
      }
      if (!payload.batch) {
        comparisonBatchRunning = false;
        comparisonBatchMode = null;
        comparisonBatchLevel = null;
        comparisonBatchSeeds = [];
        if (comparisonPayload) renderComparison(comparisonPayload);
        comparisonStatus.textContent = payload.message || "There are no cells to score.";
        comparisonStatus.classList.remove("error");
        return;
      }
      comparisonBatchLastCompleted = -1;
      applyComparisonBatch(payload.batch);
      if (mode === "random_seeds") {
        requestAnimationFrame(() => {
          comparisonTable.scrollTo({
            left: comparisonTable.scrollWidth,
            behavior: "smooth",
          });
        });
      }
    } catch (error) {
      comparisonBatchRunning = false;
      comparisonBatchMode = null;
      comparisonBatchLevel = null;
      updateComparisonActionState();
      comparisonBatch.hidden = false;
      comparisonBatch.classList.add("error");
      comparisonBatchStatus.textContent = error.message || String(error);
    }
  }

  async function openComparisonSolution(row, seed) {
    const available = Array.from(solutionSelect.options).some(
      (option) => option.value === row.solution
    );
    if (!available) return;
    const selectedLevel = selectedComparisonLevel();
    solutionSelect.value = row.solution;
    if (selectedLevel !== null) {
      levelSelect.value = String(selectedLevel);
    }
    activateTab("visualizer");
    await loadSolution(seed || row.latest_seed || null, selectedLevel);
  }

  function syncTabToUrl(name) {
    const nextUrl = globalThis.GolduckTabState.urlWithTab(
      window.location.href,
      name
    );
    if (nextUrl !== window.location.href) {
      window.history.replaceState(window.history.state, "", nextUrl);
    }
  }

  function activateTab(name) {
    if (!VALID_TABS.has(name)) return;
    syncTabToUrl(name);
    const showVisualizer = name === "visualizer";
    const showIdeaLab = name === "idea-lab";
    const showSecret = name === "secret";
    const showComparison = name === "comparison";
    const showScoring = name === "scoring";
    visualizerView.hidden = !showVisualizer;
    ideaLabView.hidden = !showIdeaLab;
    secretView.hidden = !showSecret;
    comparisonView.hidden = !showComparison;
    scoringView.hidden = !showScoring;
    visualizerTab.classList.toggle("active", showVisualizer);
    ideaLabTab.classList.toggle("active", showIdeaLab);
    secretTab.classList.toggle("active", showSecret);
    comparisonTab.classList.toggle("active", showComparison);
    scoringTab.classList.toggle("active", showScoring);
    visualizerTab.setAttribute("aria-selected", String(showVisualizer));
    ideaLabTab.setAttribute("aria-selected", String(showIdeaLab));
    secretTab.setAttribute("aria-selected", String(showSecret));
    comparisonTab.setAttribute("aria-selected", String(showComparison));
    scoringTab.setAttribute("aria-selected", String(showScoring));
    visualizerTab.tabIndex = showVisualizer ? 0 : -1;
    ideaLabTab.tabIndex = showIdeaLab ? 0 : -1;
    secretTab.tabIndex = showSecret ? 0 : -1;
    comparisonTab.tabIndex = showComparison ? 0 : -1;
    scoringTab.tabIndex = showScoring ? 0 : -1;
    if (!showIdeaLab) window.ideaLab?.deactivate();
    if (showIdeaLab) {
      window.ideaLab?.activate();
    } else if (showScoring) {
      renderScoringCalculation(true);
    } else if (showComparison) {
      loadComparison();
      loadComparisonBatchStatus();
    } else if (showSecret) {
      requestAnimationFrame(() => {
        resizeSecretCanvas();
        if (!secretPreview && !secretLoading) loadSecretPreview();
      });
    } else if (showVisualizer) {
      requestAnimationFrame(resize);
    }
  }

  function formatCalculationNumber(value, maximumFractionDigits = 4) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return "pending";
    }
    return Number(value).toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits,
    });
  }

  function logarithmicScoreRatio(value, maximum) {
    const numericValue = Number(value);
    const numericMaximum = Number(maximum);
    if (
      !Number.isFinite(numericValue) ||
      numericValue < 0 ||
      !Number.isFinite(numericMaximum) ||
      numericMaximum <= 0
    ) {
      return null;
    }
    return Math.max(
      0,
      Math.min(1, Math.log(numericValue + 1) / Math.log(numericMaximum + 1))
    );
  }

  function contestantAreaExpression(rule) {
    const width = Number(rule?.contestant_width);
    const height = Number(rule?.contestant_height);
    if (Number.isFinite(width) && Number.isFinite(height)) {
      return `${formatCalculationNumber(width)} × ${formatCalculationNumber(height)}`;
    }
    return "contestant width × height";
  }

  function calculationLine(parent, label, expression, result, emphasis = false) {
    const row = document.createElement("div");
    row.className = "scoring-calculation-line";
    if (emphasis) row.classList.add("emphasis");
    const name = document.createElement("span");
    name.textContent = label;
    const formula = document.createElement("code");
    formula.textContent = expression;
    const value = document.createElement("strong");
    value.textContent = result;
    row.append(name, formula, value);
    parent.append(row);
  }

  function submissionWord(parent, label, value) {
    const item = document.createElement("p");
    const name = document.createElement("span");
    name.textContent = label;
    const word = document.createElement("code");
    word.textContent = value || "—";
    item.append(name, word);
    parent.append(item);
  }

  function buildScoringRunInputs(level, runs, rule) {
    const details = document.createElement("details");
    details.className = "scoring-run-inputs";
    const summary = document.createElement("summary");
    summary.textContent =
      `Per-run density and generation inputs for Level ${level.level} ` +
      `(${runs.length})`;
    details.append(summary);

    const constraints = document.createElement("p");
    const area = Number(rule?.contestant_area);
    const maxGenerations = Number(rule?.max_generations);
    constraints.textContent =
      Number.isFinite(area) && Number.isFinite(maxGenerations)
        ? `A = ${contestantAreaExpression(rule)} = ${area.toLocaleString("en-US")} cells · ` +
          `generation cap = ${maxGenerations.toLocaleString("en-US")}`
        : "Level limits are still loading.";
    details.append(constraints);

    const note = document.createElement("p");
    note.className = "scoring-run-note";
    note.textContent =
      "Initial live cells count the contestant input before evolution. " +
      "The largest density ratio and largest generation ratio are used; " +
      "totals and averages are ignored.";
    details.append(note);

    if (!runs.length) {
      const empty = document.createElement("p");
      empty.textContent = "No completed runs; both maximum ratios are zero.";
      details.append(empty);
      return details;
    }

    const table = document.createElement("table");
    table.className = "scoring-run-table";
    table.setAttribute("aria-label", `Level ${level.level} scoring run inputs`);
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const label of [
      "Run",
      "Initial live cells",
      "Density ratio",
      "Generations",
      "Generation ratio",
    ]) {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = label;
      headRow.append(cell);
    }
    head.append(headRow);
    table.append(head);

    const body = document.createElement("tbody");
    const densityRatios = runs.map((run) =>
      logarithmicScoreRatio(Number(run.cell_count || 0), area)
    );
    const generationRatios = runs.map((run) =>
      logarithmicScoreRatio(Number(run.generations || 0), maxGenerations)
    );
    const maximumDensityRatio = Math.max(
      0,
      ...densityRatios.filter((ratio) => ratio !== null)
    );
    const maximumGenerationRatio = Math.max(
      0,
      ...generationRatios.filter((ratio) => ratio !== null)
    );
    runs.forEach((run, index) => {
      const row = document.createElement("tr");
      const densityRatio = densityRatios[index];
      const generationRatio = generationRatios[index];
      const isMaximumDensity =
        densityRatio !== null &&
        Math.abs(densityRatio - maximumDensityRatio) < Number.EPSILON;
      const isMaximumGeneration =
        generationRatio !== null &&
        Math.abs(generationRatio - maximumGenerationRatio) < Number.EPSILON;
      const values = [
        run.level_run ?? index + 1,
        Number(run.cell_count || 0).toLocaleString("en-US"),
        densityRatio === null
          ? "pending"
          : `${formatCalculationNumber(densityRatio, 6)}${
              isMaximumDensity ? " · max" : ""
            }`,
        Number(run.generations || 0).toLocaleString("en-US"),
        generationRatio === null
          ? "pending"
          : `${formatCalculationNumber(generationRatio, 6)}${
              isMaximumGeneration ? " · max" : ""
            }`,
      ];
      values.forEach((item, columnIndex) => {
        const cell = document.createElement("td");
        cell.textContent = String(item);
        if (
          (columnIndex === 2 && isMaximumDensity) ||
          (columnIndex === 4 && isMaximumGeneration)
        ) {
          cell.classList.add("scoring-run-maximum");
          cell.title =
            columnIndex === 2
              ? "This run sets the level density ratio"
              : "This run sets the level generation ratio";
        }
        row.append(cell);
      });
      body.append(row);
    });
    table.append(body);
    details.append(table);
    return details;
  }

  function buildLevelScoringCalculation(level) {
    const article = document.createElement("article");
    article.className = "scoring-level-calculation";
    if (level.submitted) article.classList.add("submitted");

    const heading = document.createElement("header");
    const headingCopy = document.createElement("div");
    const title = document.createElement("h2");
    title.textContent = `Level ${level.level}`;
    const state = document.createElement("span");
    state.className = "scoring-level-state";
    state.textContent = level.submitted ? "Submitted" : "Awaiting submission";
    headingCopy.append(title, state);
    const score = document.createElement("strong");
    score.textContent = level.submitted
      ? formatCalculationNumber(level.score)
      : "Not scored";
    heading.append(headingCopy, score);
    article.append(heading);

    const runs = Array.isArray(solution?.runs)
      ? solution.runs.filter((run) => Number(run.level) === Number(level.level))
      : [];
    const rule = levelScoringRules.get(Number(level.level));
    const runCount = Number(level.runs_completed || 0);
    const completedRuns = runs.slice(0, Math.max(0, runCount));
    const maxCells = completedRuns.reduce(
      (maximum, run) => Math.max(maximum, Number(run.cell_count || 0)),
      0
    );
    const maxRunGenerations = completedRuns.reduce(
      (maximum, run) => Math.max(maximum, Number(run.generations || 0)),
      0
    );
    const scoredDensityRatio = Math.max(
      0,
      Math.min(1, 1 - Number(level.density_bonus || 0) / 25_000)
    );
    const scoredGenerationRatio = Math.max(
      0,
      Math.min(1, 1 - Number(level.generations_bonus || 0) / 25_000)
    );
    const area = Number(rule?.contestant_area);
    const maxGenerations = Number(rule?.max_generations);
    const calculatedDensityRatio = logarithmicScoreRatio(maxCells, area);
    const densityRatio = calculatedDensityRatio ?? scoredDensityRatio;
    const calculatedGenerationRatio = logarithmicScoreRatio(
      maxRunGenerations,
      maxGenerations
    );
    const generationRatio = calculatedGenerationRatio ?? scoredGenerationRatio;

    const performance = document.createElement("section");
    performance.className = "scoring-calculation-group";
    const performanceTitle = document.createElement("h3");
    performanceTitle.textContent = "1 · Performance from run cost";
    performance.append(performanceTitle);
    calculationLine(
      performance,
      "Base",
      "fixed for every level",
      formatCalculationNumber(level.base_score)
    );
    calculationLine(
      performance,
      "Run bonus",
      `100,000 ÷ (max(${runCount}, 1) + 1)`,
      `+${formatCalculationNumber(level.run_bonus)}`
    );
    calculationLine(
      performance,
      "Density input Nmax",
      completedRuns.length
        ? `largest generation-zero input across ${completedRuns.length} run${
            completedRuns.length === 1 ? "" : "s"
          }`
        : "no completed runs → 0",
      formatCalculationNumber(maxCells)
    );
    calculationLine(
      performance,
      "Contestant area A",
      Number.isFinite(area) ? contestantAreaExpression(rule) : "level contestant area",
      Number.isFinite(area) ? formatCalculationNumber(area) : "pending"
    );
    calculationLine(
      performance,
      "Density ratio d",
      Number.isFinite(area)
        ? `clamp[0, 1](ln(${maxCells} + 1) ÷ ln(${area} + 1))`
        : "max run density ratio",
      formatCalculationNumber(densityRatio, 6)
    );
    calculationLine(
      performance,
      "Density bonus",
      `25,000 × (1 − ${formatCalculationNumber(densityRatio, 6)})`,
      `+${formatCalculationNumber(level.density_bonus)}`
    );
    calculationLine(
      performance,
      "Generation input Tmax",
      completedRuns.length
        ? `largest request across ${completedRuns.length} run${
            completedRuns.length === 1 ? "" : "s"
          }`
        : "no completed runs → 0",
      formatCalculationNumber(maxRunGenerations)
    );
    calculationLine(
      performance,
      "Generation cap Tcap",
      "maximum allowed by this level",
      Number.isFinite(maxGenerations)
        ? formatCalculationNumber(maxGenerations)
        : "pending"
    );
    calculationLine(
      performance,
      "Generation ratio g",
      Number.isFinite(maxGenerations)
        ? `clamp[0, 1](ln(${maxRunGenerations} + 1) ÷ ln(${maxGenerations} + 1))`
        : "max run generation ratio",
      formatCalculationNumber(generationRatio, 6)
    );
    calculationLine(
      performance,
      "Generation bonus",
      `25,000 × (1 − ${formatCalculationNumber(generationRatio, 6)})`,
      `+${formatCalculationNumber(level.generations_bonus)}`
    );
    calculationLine(
      performance,
      "Performance P",
      `${formatCalculationNumber(level.base_score)} + ${formatCalculationNumber(level.run_bonus)} + ${formatCalculationNumber(level.density_bonus)} + ${formatCalculationNumber(level.generations_bonus)}`,
      formatCalculationNumber(level.performance_score),
      true
    );
    article.append(performance, buildScoringRunInputs(level, completedRuns, rule));

    const answer = document.createElement("section");
    answer.className = "scoring-calculation-group";
    const answerTitle = document.createElement("h3");
    answerTitle.textContent = "2 · Submission answer weight";
    answer.append(answerTitle);

    if (!level.submitted) {
      const pending = document.createElement("p");
      pending.className = "scoring-pending";
      pending.textContent =
        `Current performance is ${formatCalculationNumber(level.performance_score)}. ` +
        "Known weight, guess weight, exact bonus, and the level score are calculated after submit().";
      answer.append(pending);
      article.append(answer);
      return article;
    }

    const words = document.createElement("div");
    words.className = "scoring-submission-words";
    submissionWord(words, "Value", level.submission);
    submissionWord(words, "Known mask", level.known_mask);
    submissionWord(words, "Guess mask", level.guess_mask);
    answer.append(words);

    const knownGate = level.known_correct ? 1 : 0;
    calculationLine(
      answer,
      "Known weight K",
      `${knownGate} × ${Number(level.known_bits || 0)} ÷ 64`,
      formatCalculationNumber(level.known_weight, 6)
    );
    calculationLine(
      answer,
      "Guess weight Q",
      `0.5 × (0.4 × ${Number(level.guess_correct_bits || 0)} − 0.6 × ${Number(level.guess_wrong_bits || 0)}) ÷ 64`,
      formatCalculationNumber(level.guess_weight, 6)
    );
    calculationLine(
      answer,
      "Answer weight",
      `${formatCalculationNumber(level.known_weight, 6)} + ${formatCalculationNumber(level.guess_weight, 6)}`,
      formatCalculationNumber(level.answer_weight, 6),
      true
    );
    calculationLine(
      answer,
      "Weighted score",
      `${formatCalculationNumber(level.performance_score)} × ${formatCalculationNumber(level.answer_weight, 6)}`,
      formatCalculationNumber(level.weighted_score)
    );
    calculationLine(
      answer,
      "Exact bonus",
      level.exact_answer ? "all 64 known and correct" : "exact-answer condition not met",
      level.exact_answer ? "+100,000" : "+0"
    );
    calculationLine(
      answer,
      "Level score",
      `${formatCalculationNumber(level.weighted_score)} + ${formatCalculationNumber(level.exact_bonus)}`,
      formatCalculationNumber(level.score),
      true
    );
    article.append(answer);
    return article;
  }

  function renderScoringCalculation(force = false) {
    if (scoringView.hidden && !force) return;
    scoringSolution.textContent = solution?.solution || "None selected";
    scoringSeed.textContent = evaluation?.seed || "—";
    scoringStatus.textContent = evaluation?.statusText || evaluation?.status || "Waiting";
    const levels = activeEvaluationLevels();
    const totals = activeEvaluationTotals(levels);
    scoringTotal.textContent = formatCalculationNumber(totals.score);
    scoringCalculations.innerHTML = "";

    if (!solution || !evaluation) {
      const empty = document.createElement("p");
      empty.className = "scoring-empty";
      empty.textContent = "Select a solution in the Visualizer to see its calculations.";
      scoringCalculations.append(empty);
      return;
    }

    if (!levels.length) {
      const empty = document.createElement("p");
      empty.className = "scoring-empty";
      empty.textContent = "Waiting for level scoring data…";
      scoringCalculations.append(empty);
      return;
    }
    for (const level of levels) {
      scoringCalculations.append(buildLevelScoringCalculation(level));
    }
  }

  function formatWeight(value) {
    if (value === null || value === undefined) return "pending";
    return Number(value).toFixed(4);
  }

  function scoreRow(list, label, value, options = {}) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    if (options.wide) {
      dt.classList.add("score-wide-label");
      dd.classList.add("score-wide-value");
    }
    if (options.hex) dd.classList.add("score-hex-value");
    if (options.title) dd.title = options.title;
    if (options.emphasis) dd.classList.add("score-emphasis");
    list.append(dt, dd);
  }

  function selectScoreLevel(level) {
    if (String(levelSelect.value) === String(level)) return;
    levelSelect.value = String(level);
    loadLevel();
  }

  function scoreWord(value) {
    try {
      return BigInt(value || 0) & UINT64_MASK;
    } catch {
      return 0n;
    }
  }

  function classifyLevelBits(level) {
    const secret = scoreWord(level.secret);
    const submitted = scoreWord(level.submission);
    const knownMask = scoreWord(level.known_mask);
    const guessMask = scoreWord(level.guess_mask);
    const bits = [];

    for (let index = 63; index >= 0; index--) {
      const mask = 1n << BigInt(index);
      const submittedBit = Number((submitted & mask) !== 0n);
      const secretBit = Number((secret & mask) !== 0n);
      const correct = submittedBit === secretBit;
      let state = "unknown";
      if ((knownMask & mask) !== 0n) {
        state = correct ? "known-correct" : "known-incorrect";
      } else if ((guessMask & mask) !== 0n) {
        state = correct ? "guess-correct" : "guess-incorrect";
      }
      bits.push({
        index,
        state,
        submittedBit,
        secretBit,
        covered: state !== "unknown",
      });
    }
    return bits;
  }

  function buildBitPanel(level) {
    const bits = classifyLevelBits(level);
    const counts = Object.fromEntries(BIT_STATE_ORDER.map((state) => [state, 0]));
    for (const bit of bits) counts[bit.state]++;

    const panel = document.createElement("section");
    panel.className = "score-bit-panel";

    const summary = document.createElement("p");
    summary.className = "score-bit-summary";
    const knownCount = counts["known-correct"] + counts["known-incorrect"];
    const guessCount = counts["guess-correct"] + counts["guess-incorrect"];
    summary.textContent = level.submitted
      ? `${knownCount} known · ${guessCount} guessed · 8×8 · bit 63 → bit 0`
      : "Awaiting submission · 8×8 · bit 63 → bit 0";
    panel.append(summary);

    const grid = document.createElement("div");
    grid.className = "score-bit-grid";
    grid.setAttribute("role", "grid");
    grid.setAttribute(
      "aria-label",
      `Level ${level.level} submitted bits from bit 63 through bit 0`
    );
    for (const bit of bits) {
      const pixel = document.createElement("span");
      pixel.className = `score-bit score-bit-${bit.state}`;
      pixel.setAttribute("role", "gridcell");
      pixel.textContent = bit.covered ? String(bit.submittedBit) : "·";
      const detail = bit.covered
        ? `${BIT_STATE_LABELS[bit.state]}; submitted ${bit.submittedBit}, secret ${bit.secretBit}`
        : "Unknown; not included in either mask";
      pixel.setAttribute("aria-label", `Bit ${bit.index}: ${detail}`);
      pixel.title = `Bit ${bit.index} · ${detail}`;
      grid.append(pixel);
    }
    panel.append(grid);

    const legend = document.createElement("ul");
    legend.className = "score-bit-legend";
    legend.setAttribute("aria-label", "Bit state legend");
    for (const state of BIT_STATE_ORDER) {
      const item = document.createElement("li");
      const swatch = document.createElement("span");
      swatch.className = `score-bit-swatch score-bit-${state}`;
      swatch.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.textContent = `${BIT_STATE_LABELS[state]} ${counts[state]}`;
      item.append(swatch, label);
      legend.append(item);
    }
    panel.append(legend);
    return panel;
  }

  function toggleScoreLevel(level) {
    const expanding = !expandedScoreLevels.has(level);
    if (expanding) expandedScoreLevels.add(level);
    else expandedScoreLevels.delete(level);
    renderScore();
    if (expanding) selectScoreLevel(level);
  }

  function renderScore() {
    renderScoringCalculation();
    scoreLevels.innerHTML = "";
    if (!solution || !evaluation) {
      scorePanel.hidden = true;
      return;
    }

    scorePanel.hidden = false;
    scoreSeed.textContent = evaluation.seed || "validating seed…";
    scoreStatus.textContent = evaluation.statusText || evaluation.status;
    scoreStatus.dataset.state = evaluation.status;

    const levels = Object.values(evaluation.levels || {}).sort(
      (a, b) => a.level - b.level
    );
    const totals = evaluation.totals || {};
    scoreTotal.textContent = formatScore(totals.score ?? 0);
    const submitted = Number(totals.submitted_levels || 0);
    const levelCount = Number(totals.level_count || levels.length || 0);
    scoreTotalLabel.textContent =
      `${submitted}/${levelCount} levels submitted` +
      (totals.potential_score !== undefined
        ? ` · exact-answer ceiling at current run cost ${formatScore(totals.potential_score)}`
        : "");

    for (const level of levels) {
      const card = document.createElement("article");
      card.className = "score-level";
      const expanded = expandedScoreLevels.has(level.level);
      if (expanded) card.classList.add("expanded");
      if (data?.level === level.level) card.classList.add("selected");
      if (level.submitted) card.classList.add("submitted");

      const heading = document.createElement("button");
      heading.type = "button";
      heading.className = "score-level-heading";
      const detailsId = `score-level-${level.level}-details`;
      heading.setAttribute("aria-expanded", String(expanded));
      heading.setAttribute("aria-controls", detailsId);
      heading.addEventListener("click", () => toggleScoreLevel(level.level));
      const headingLabel = document.createElement("span");
      headingLabel.className = "score-level-heading-label";
      const caret = document.createElement("span");
      caret.className = "score-level-caret";
      caret.setAttribute("aria-hidden", "true");
      caret.textContent = "›";
      const title = document.createElement("span");
      title.textContent = `Level ${level.level}`;
      headingLabel.append(caret, title);
      const levelTotal = document.createElement("strong");
      levelTotal.textContent = formatScore(level.score);
      heading.append(headingLabel, levelTotal);
      card.append(heading);

      const details = document.createElement("div");
      details.id = detailsId;
      details.className = "score-level-details";
      details.hidden = !expanded;

      const secret = document.createElement("p");
      secret.className = "score-level-secret";
      secret.textContent = `secret ${level.secret}`;
      details.append(secret);

      const progress = document.createElement("div");
      progress.className = "score-progress";
      const progressBar = document.createElement("span");
      const maxRuns = Math.max(1, Number(level.max_runs || 1));
      progressBar.style.width =
        `${Math.min(100, 100 * Number(level.runs_completed || 0) / maxRuns)}%`;
      progress.append(progressBar);
      details.append(progress);
      details.append(buildBitPanel(level));

      const breakdown = document.createElement("dl");
      breakdown.className = "score-breakdown";
      const active = evaluation.activeRun;
      const runText = `${level.runs_completed}/${level.max_runs}` +
        (active?.level === level.level ? ` · running ${active.run}` : "");
      scoreRow(breakdown, "runs", runText);
      const completeDecode =
        level.submitted && level.known_bits === 64 && level.guess_bits === 0;
      const decodeLabel = completeDecode
        ? "decoded secret"
        : level.submitted
          ? `partial decode · ${level.known_bits} known / ${level.guess_bits} guessed`
          : "decoded value";
      scoreRow(
        breakdown,
        decodeLabel,
        level.submitted ? level.submission : "awaiting submission",
        {
          wide: true,
          hex: level.submitted,
          title: level.submitted
            ? "Only bits selected by the known or guess masks are meaningful."
            : "",
        }
      );
      scoreRow(
        breakdown,
        "known mask",
        level.submitted ? level.known_mask : "awaiting submission",
        { wide: true, hex: level.submitted }
      );
      scoreRow(
        breakdown,
        "guess mask",
        level.submitted ? level.guess_mask : "awaiting submission",
        { wide: true, hex: level.submitted }
      );
      scoreRow(breakdown, "base", formatScore(level.base_score));
      scoreRow(breakdown, "run bonus", `+${formatScore(level.run_bonus)}`);
      scoreRow(breakdown, "density bonus", `+${formatScore(level.density_bonus)}`);
      scoreRow(
        breakdown,
        "generation bonus",
        `+${formatScore(level.generations_bonus)}`
      );
      scoreRow(
        breakdown,
        "performance",
        formatScore(level.performance_score),
        { emphasis: true, title: "base + run + density + generation bonuses" }
      );
      scoreRow(
        breakdown,
        "known coverage",
        level.submitted
          ? `${level.known_bits}/64${level.known_correct ? " · correct" : " · incorrect"}`
          : "awaiting submission"
      );
      scoreRow(
        breakdown,
        "guess coverage",
        level.submitted
          ? `${level.guess_bits}/64` +
              (level.guess_bits
                ? ` · ${level.guess_correct_bits} right / ${level.guess_wrong_bits} wrong`
                : "")
          : "awaiting submission"
      );
      scoreRow(breakdown, "known weight", formatWeight(level.known_weight));
      scoreRow(
        breakdown,
        "guess weight",
        formatWeight(level.guess_weight),
        level.submitted && level.guess_bits
          ? {
              title:
                `${level.guess_correct_bits} correct / ${level.guess_wrong_bits} wrong ` +
                `across ${level.guess_bits} guessed bits`,
            }
          : {}
      );
      scoreRow(breakdown, "answer weight", formatWeight(level.answer_weight), {
        title: "known weight + guess weight",
      });
      scoreRow(
        breakdown,
        "weighted score",
        formatScore(level.weighted_score),
        { title: "performance × (known weight + guess weight)" }
      );
      scoreRow(
        breakdown,
        "exact bonus",
        level.exact_answer ? "+100,000" : "+0"
      );
      scoreRow(breakdown, "level score", formatScore(level.score), {
        emphasis: true,
      });
      details.append(breakdown);
      card.append(details);
      scoreLevels.append(card);
    }

    if (evaluation.error) {
      scoreNote.textContent = evaluation.error;
      scoreNote.classList.add("error");
    } else {
      scoreNote.textContent =
        "Open any level to inspect its 64 submitted bits. " +
        "Level score = performance × (known + guess weight) + exact bonus. " +
        "Run, density, and generation bonuses update after every completed run. " +
        (evaluation.cached
          ? "Restored from score_history.json."
          : "Progress is saved to score_history.json.");
      scoreNote.classList.remove("error");
    }
  }

  function updateRunNav() {
    const previous = document.getElementById("run-prev");
    const next = document.getElementById("run-next");
    if (!solution || solution.runs.length === 0) {
      runNav.hidden = true;
      return;
    }
    runNav.hidden = false;
    const run = solution.runs[runIndex];
    const position = globalThis.GolduckPlaybackState.runNavigationPosition(
      solution.runs,
      runIndex
    );
    runLabel.textContent =
      `L${run.level} run ${position.ordinal}/${position.total} · ` +
      `${run.generations} gens · ${run.cell_count} cells`;
    previous.disabled = Boolean(videoExport) || runIndex === 0;
    next.disabled = Boolean(videoExport) || runIndex >= solution.runs.length - 1;
  }

  function syncRunToLevel(level) {
    if (!solution || solution.runs.length === 0) return false;
    if (solution.runs[runIndex]?.level === level) return false;
    const matchingIndex = solution.runs.findIndex((run) => run.level === level);
    if (matchingIndex === -1) return false;
    runIndex = matchingIndex;
    return true;
  }

  async function showRun(index) {
    if (videoExport || !solution || solution.runs.length === 0) return;
    const preserveFinalGeneration =
      globalThis.GolduckPlaybackState.isAtFinalGeneration(sim, play.pending);
    runIndex = Math.max(0, Math.min(solution.runs.length - 1, index));
    const run = solution.runs[runIndex];
    useRunGenerationLimit();
    updateRunNav();
    if (!data || data.level !== run.level) {
      levelSelect.value = String(run.level);
      await loadLevel();
    } else {
      resetSim();
      draw();
    }
    if (preserveFinalGeneration && activeRun()) {
      seekTo(requestedGenerationLimit());
    }
  }

  function canonicalSeed(value) {
    const normalized = String(value || "").trim().toLowerCase();
    return normalized.startsWith("0x") ? normalized.slice(2) : normalized;
  }

  function setEvaluationLevel(levelScore) {
    if (!evaluation || !levelScore) return;
    evaluation.levels[levelScore.level] = levelScore;
  }

  function useFirstRunForDisplayedLevel() {
    if (!solution || !data || activeRun()) return false;
    const index = solution.runs.findIndex((run) => run.level === data.level);
    if (index < 0) return false;
    runIndex = index;
    useRunGenerationLimit();
    resetSim();
    return true;
  }

  function handleEvaluationEvent(event, seq) {
    if (seq !== evaluationSeq || !evaluation || !solution) return;
    if (event.totals) evaluation.totals = event.totals;

    if (event.type === "start") {
      evaluation.status = "running";
      evaluation.statusText = event.cached ? "restoring" : "planning runs";
      evaluation.seed = event.seed;
      evaluation.levels = {};
      for (const level of event.levels || []) setEvaluationLevel(level);
    } else if (event.type === "parallel_batch_started") {
      evaluation.status = "running";
      evaluation.statusText = `parallel 0/${event.total}`;
      evaluation.parallel = { completed: 0, total: event.total, round: event.round };
    } else if (event.type === "parallel_progress") {
      evaluation.status = "running";
      evaluation.statusText = `parallel ${event.completed}/${event.total}`;
      evaluation.parallel = {
        completed: event.completed,
        total: event.total,
        round: event.round,
      };
    } else if (event.type === "run_started") {
      evaluation.status = "running";
      evaluation.statusText = `scoring L${event.level} run ${event.run}`;
      evaluation.activeRun = event;
    } else if (event.type === "run_complete") {
      evaluation.activeRun = null;
      setEvaluationLevel(event.level_score);
      solution.runs.push(event.run);
      if (solution.runs.length === 1) runIndex = 0;
      useFirstRunForDisplayedLevel();
      updateRunNav();
      draw();
    } else if (event.type === "submission") {
      evaluation.activeRun = null;
      setEvaluationLevel(event.level_score);
    } else if (event.type === "complete") {
      evaluation.status = "complete";
      evaluation.statusText = event.cached ? "saved" : "complete";
      evaluation.cached = Boolean(event.cached);
      evaluation.activeRun = null;
      evaluation.totals = event.totals;
      evaluation.callCounts = event.call_counts;
      evaluation.levels = {};
      for (const level of event.levels || []) setEvaluationLevel(level);

      const selected = solution.runs[runIndex];
      solution.runs = event.runs || solution.runs;
      if (selected) {
        const selectedIndex = solution.runs.findIndex(
          (run) =>
            run.level === selected.level && run.level_run === selected.level_run
        );
        if (selectedIndex >= 0) runIndex = selectedIndex;
      }
      useFirstRunForDisplayedLevel();
      updateRunNav();
      if (!comparisonView.hidden) loadComparison();
    } else if (event.type === "error") {
      evaluation.status = "error";
      evaluation.activeRun = null;
      evaluation.error = event.error || "evaluation failed";
      setStatus(evaluation.error);
    }
    renderScore();
  }

  async function startEvaluation(seedValue) {
    if (!solution) return;
    const seed = canonicalSeed(seedValue);
    const key = `${solution.solution}:${seed}`;
    if (evaluation?.key === key && evaluation.status !== "error") return;

    if (evaluationAbort) evaluationAbort.abort();
    const controller = new AbortController();
    evaluationAbort = controller;
    const seq = ++evaluationSeq;
    solution.runs = [];
    runIndex = 0;
    evaluation = {
      key,
      seed,
      status: "starting",
      statusText: "starting",
      levels: {},
      totals: { score: 0, submitted_levels: 0, level_count: 0 },
      activeRun: null,
      error: null,
    };
    updateRunNav();
    resetSim();
    renderScore();

    try {
      const res = await fetch(
        `/api/evaluate/${encodeURIComponent(solution.solution)}?seed=${encodeURIComponent(seed)}`,
        { signal: controller.signal }
      );
      if (!res.ok) {
        const payload = await res.json();
        throw new Error(payload.error || "failed to start evaluation");
      }
      if (!res.body) throw new Error("streaming responses are not supported");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          handleEvaluationEvent(JSON.parse(line), seq);
        }
        if (done) break;
      }
      if (buffer.trim()) handleEvaluationEvent(JSON.parse(buffer), seq);
      if (seq === evaluationSeq && evaluation?.status === "running") {
        throw new Error("evaluation stream ended before completion");
      }
    } catch (err) {
      if (err.name === "AbortError" || seq !== evaluationSeq) return;
      evaluation.status = "error";
      evaluation.error = err.message || String(err);
      setStatus(evaluation.error);
      renderScore();
    }
  }

  function replaceSolutionOptions(names, selected) {
    solutionSelect.innerHTML = "";
    for (const name of names) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      solutionSelect.append(opt);
    }
    solutionSelect.disabled = names.length === 0;
    if (selected && names.includes(selected)) solutionSelect.value = selected;
  }

  function preferredSolution(names, candidates) {
    return candidates.find((candidate) => names.includes(candidate)) || "";
  }

  async function loadSolutions({ autoVisualize = false } = {}) {
    if (solutionSyncRunning) return false;
    solutionSyncRunning = true;
    const firstLoad = solutionVersions === null;
    try {
      const res = await fetch("/api/solutions", { cache: "no-store" });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.error || "failed to list solutions");

      const names = Array.isArray(payload.solutions) ? payload.solutions : [];
      const rawVersions = payload.solution_versions || {};
      const nextVersions = new Map(
        names.map((name) => [name, String(rawVersions[name] ?? name)])
      );
      const previousSelection = solutionSelect.value;
      const changed = firstLoad
        ? []
        : names.filter(
          (name) => solutionVersions.get(name) !== nextVersions.get(name)
        );
      const removedSelection = Boolean(
        !firstLoad && previousSelection && !names.includes(previousSelection)
      );
      const existingNames = Array.from(
        solutionSelect.options,
        (option) => option.value
      );
      const listChanged =
        existingNames.length !== names.length
        || names.some((name, index) => name !== existingNames[index]);

      let selected = names.includes(previousSelection) ? previousSelection : "";
      if (firstLoad) {
        selected = preferredSolution(names, [
          REQUESTED_SOLUTION,
          payload.newest_solution,
          payload.latest_solution,
          names[0],
        ]);
      } else if (changed.length > 0) {
        selected = changed.includes(payload.newest_solution)
          ? payload.newest_solution
          : changed.at(-1);
      } else if (!selected) {
        selected = preferredSolution(names, [
          payload.newest_solution,
          payload.latest_solution,
          names[0],
        ]);
      }

      if (listChanged) replaceSolutionOptions(names, selected);
      else if (selected) solutionSelect.value = selected;
      solutionVersions = nextVersions;

      if (firstLoad || listChanged || changed.length > 0) {
        window.dispatchEvent(new CustomEvent(SOLUTIONS_CHANGED_EVENT, {
          detail: {
            ...payload,
            solutions: names,
            solution_versions: Object.fromEntries(nextVersions),
          },
        }));
      }

      const shouldAutoVisualize = autoVisualize && (
        (firstLoad && Boolean(selected))
        || (!firstLoad && (changed.length > 0 || removedSelection))
      );
      if (shouldAutoVisualize) {
        if (selected) await loadSolution();
        else await loadLevel();
        if (!comparisonView.hidden) loadComparison();
        return true;
      }
      return false;
    } catch (error) {
      if (firstLoad) setStatus(error.message || String(error));
      return false;
    } finally {
      solutionSyncRunning = false;
    }
  }

  function pollSolutions() {
    if (document.hidden) return;
    void loadSolutions({ autoVisualize: true });
  }

  function startSolutionPolling() {
    if (solutionPollTimer !== null) return;
    solutionPollTimer = window.setInterval(
      pollSolutions,
      SOLUTION_POLL_INTERVAL_MS
    );
  }

  async function loadSolution(preferredSeed = null, preferredLevel = null) {
    const seq = ++solutionLoadSeq;
    const requestedSeed =
      typeof preferredSeed === "string" ? canonicalSeed(preferredSeed) : null;
    const requestedLevel = Number.isInteger(preferredLevel) ? preferredLevel : null;
    setStatus("");
    const name = solutionSelect.value;
    if (!name) {
      if (evaluationAbort) evaluationAbort.abort();
      evaluationAbort = null;
      evaluation = null;
      evaluationSeq++;
      solution = null;
      useRunGenerationLimit();
      updateRunNav();
      renderScore();
      resetSim();
      draw();
      return;
    }
    if (evaluationAbort) evaluationAbort.abort();
    evaluationAbort = null;
    evaluation = null;
    evaluationSeq++;
    solution = null;
    renderScore();
    try {
      const query = new URLSearchParams();
      if (requestedSeed) query.set("seed", requestedSeed);
      if (requestedLevel !== null) query.set("level", String(requestedLevel));
      const queryString = query.toString();
      const suffix = queryString ? `?${queryString}` : "";
      const res = await fetch(
        `/api/solution/${encodeURIComponent(name)}${suffix}`,
        { cache: "no-store" }
      );
      const payload = await res.json();
      if (seq !== solutionLoadSeq) return;
      if (!res.ok) {
        setStatus(payload.error || "failed to load solution");
        solution = null;
        updateRunNav();
        renderScore();
        draw();
        return;
      }
      solution = payload;
      const requestedRunIndex =
        requestedLevel === null
          ? -1
          : solution.runs.findIndex((run) => run.level === requestedLevel);
      runIndex = requestedRunIndex >= 0 ? requestedRunIndex : 0;
      if (requestedLevel !== null) {
        levelSelect.value = String(requestedLevel);
      }
      const saved = payload.saved_evaluation;
      const restoreSaved =
        saved && (!requestedSeed || canonicalSeed(saved.seed) === requestedSeed);
      if (requestedSeed) {
        seedInput.value = requestedSeed;
        if (!restoreSaved) solution.runs = [];
      } else if (payload.latest_seed) {
        seedInput.value = payload.latest_seed;
      }
      if (restoreSaved) {
        const levels = {};
        for (const level of saved.levels || []) levels[level.level] = level;
        evaluation = {
          key: `${payload.solution}:${saved.seed}`,
          seed: saved.seed,
          status: "complete",
          statusText: "saved",
          levels,
          totals: saved.totals || {},
          callCounts: saved.call_counts || {},
          activeRun: null,
          error: null,
          cached: true,
        };
      }
      if (solution.runs.length && requestedRunIndex >= 0) {
        useRunGenerationLimit();
      } else if (requestedLevel !== null) {
        generationInput.value = "";
      }
      renderScore();
      updateRunNav();
      resetSim();
      draw();
      await loadLevel();
    } catch (err) {
      if (seq !== solutionLoadSeq) return;
      setStatus(String(err));
    }
  }

  async function loadLevels() {
    const res = await fetch("/api/levels");
    const payload = await res.json();
    levelSelect.innerHTML = "";
    secretLevelSelect.innerHTML = "";
    activeLevelIds.clear();
    levelScoringRules.clear();
    for (const rule of payload.scoring || []) {
      levelScoringRules.set(Number(rule.level), rule);
    }
    const activeLevels = Array.isArray(payload.active_levels)
      ? payload.active_levels
      : (payload.levels || []).filter((id) => Number(id) >= 3);
    for (const id of activeLevels) activeLevelIds.add(Number(id));
    for (const id of payload.levels || []) {
      const opt = document.createElement("option");
      opt.value = String(id);
      opt.textContent = `Level ${id}`;
      levelSelect.append(opt);
      secretLevelSelect.append(opt.cloneNode(true));
    }
    if (Array.from(levelSelect.options).some(
      (option) => Number(option.value) === DEFAULT_LEVEL
    )) {
      levelSelect.value = String(DEFAULT_LEVEL);
      secretLevelSelect.value = String(DEFAULT_LEVEL);
    }
    if (comparisonPayload) renderComparison(comparisonPayload);
  }

  let loadSeq = 0;

  async function loadLevel() {
    setStatus("");
    const seq = ++loadSeq;
    const level = levelSelect.value;
    const seed = encodeURIComponent(seedInput.value.trim());
    try {
      const res = await fetch(`/api/level/${level}?seed=${seed}`);
      const payload = await res.json();
      if (seq !== loadSeq) return;
      if (!res.ok) {
        setStatus(payload.error || "failed to load level");
        return;
      }
      const previousLevel = data?.level;
      data = payload;
      seedInput.value = payload.seed_hex;
      generationInput.max = String(payload.generations.max);
      const runChanged = syncRunToLevel(payload.level);
      if (runChanged || (previousLevel !== payload.level && !solution)) {
        useRunGenerationLimit();
      } else if (!generationInput.value) {
        useRunGenerationLimit();
      }
      updateRunNav();
      resetSim();
      renderMeta(payload);
      renderScore();
      fitToFocus();
      if (solution) startEvaluation(payload.seed_hex);
    } catch (err) {
      setStatus(String(err));
    }
  }

  async function randomizeSeed() {
    seedInput.value = `0x${randomSeedHex()}`;
    await loadLevel();
  }

  canvas.addEventListener("pointerdown", (e) => {
    dragging = true;
    lastPointer = { x: e.clientX, y: e.clientY };
    canvas.classList.add("dragging");
    canvas.setPointerCapture(e.pointerId);
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!dragging || !lastPointer) return;
    const dx = e.clientX - lastPointer.x;
    const dy = e.clientY - lastPointer.y;
    view.x -= dx / view.scale;
    view.y -= dy / view.scale;
    clampViewToCanvas();
    lastPointer = { x: e.clientX, y: e.clientY };
    draw();
  });

  function endDrag(e) {
    dragging = false;
    lastPointer = null;
    canvas.classList.remove("dragging");
    if (e && canvas.hasPointerCapture?.(e.pointerId)) {
      canvas.releasePointerCapture(e.pointerId);
    }
  }

  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const before = screenToWorld(sx, sy);
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      view.scale = Math.min(80, Math.max(minimumViewScale(), view.scale * factor));
      const after = screenToWorld(sx, sy);
      view.x += before.x - after.x;
      view.y += before.y - after.y;
      clampViewToCanvas();
      draw();
    },
    { passive: false }
  );

  document.getElementById("load").addEventListener("click", loadLevel);
  visualizerTab.addEventListener("click", () => activateTab("visualizer"));
  ideaLabTab.addEventListener("click", () => activateTab("idea-lab"));
  secretTab.addEventListener("click", () => activateTab("secret"));
  comparisonTab.addEventListener("click", () => activateTab("comparison"));
  scoringTab.addEventListener("click", () => activateTab("scoring"));
  document
    .getElementById("scoring-open-visualizer")
    .addEventListener("click", () => activateTab("visualizer"));
  comparisonGenerate.addEventListener("click", () =>
    runComparisonBatch("random_seeds")
  );
  comparisonFillEmpty.addEventListener("click", () =>
    runComparisonBatch("fill_empty")
  );
  comparisonLevelSelect.addEventListener("change", () => {
    expandedComparisonScores.clear();
    if (comparisonPayload) renderComparison(comparisonPayload);
  });
  comparisonRestore.addEventListener("click", () => {
    if (comparisonBatchRunning) return;
    const solution = comparisonRestoreSelect.value;
    if (!solution || !excludedComparisonSolutions.has(solution)) return;
    excludedComparisonSolutions.delete(solution);
    persistExcludedComparisonSolutions();
    if (comparisonPayload) renderComparison(comparisonPayload);
  });
  comparisonRefresh.addEventListener("click", () => loadComparison());
  secretLevelSelect.addEventListener("change", () => loadSecretPreview());
  secretRandom.addEventListener("click", () =>
    loadSecretPreview({ randomize: true })
  );
  document.getElementById("randomize").addEventListener("click", randomizeSeed);
  document.getElementById("fit").addEventListener("click", fitToFocus);
  document.getElementById("fit-canvas").addEventListener("click", fitToCanvas);
  solutionSelect.addEventListener("change", () => loadSolution());
  document.getElementById("run-prev").addEventListener("click", () => showRun(runIndex - 1));
  document.getElementById("run-next").addEventListener("click", () => showRun(runIndex + 1));
  simPlayBtn.addEventListener("click", () => {
    if (!ensureSim()) return;
    if (!play.playing && sim.gen >= sim.limit) seekTo(0);
    play.playing = !play.playing;
    play.acc = 0;
    updateSimUI();
    scheduleTick();
  });
  simResetBtn.addEventListener("click", () => {
    play.playing = false;
    seekTo(0);
  });
  simBackBtn.addEventListener("click", () => {
    play.playing = false;
    if (ensureSim()) seekTo(sim.gen - 1);
  });
  simForwardBtn.addEventListener("click", () => {
    play.playing = false;
    if (ensureSim()) seekTo(sim.gen + 1);
  });
  simSpeedSelect.addEventListener("change", (e) => {
    play.speed = Number(e.target.value);
  });
  simExportBtn.addEventListener("click", startVideoExport);
  simSlider.addEventListener("input", () => {
    play.playing = false;
    seekTo(Number(simSlider.value));
  });
  showGrid.addEventListener("change", draw);
  showCanvas.addEventListener("change", draw);
  showCells.addEventListener("change", draw);
  seedInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadLevel();
  });
  function applyGenerationLimit() {
    const raw = generationInput.value.trim();
    const value = Number(raw);
    const max = data ? data.generations.max : 10000;
    if (!raw || !Number.isInteger(value) || value < 0 || value > max) {
      generationInput.value = String(sim ? sim.limit : defaultGenerationLimit());
      setStatus(`generations must be a whole number from 0 to ${max}`);
      return;
    }
    setStatus("");
    resetSim();
    draw();
  }
  generationInput.addEventListener("change", applyGenerationLimit);
  generationInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") applyGenerationLimit();
  });
  levelSelect.addEventListener("change", loadLevel);
  if (window.ResizeObserver) {
    const canvasResizeObserver = new window.ResizeObserver(resize);
    canvasResizeObserver.observe(canvas);
    const secretCanvasResizeObserver = new window.ResizeObserver(resizeSecretCanvas);
    secretCanvasResizeObserver.observe(secretCanvas);
  }
  window.addEventListener("resize", resize);
  window.addEventListener("resize", resizeSecretCanvas);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollSolutions();
  });

  activateTab(INITIAL_TAB);

  (async () => {
    resize();
    secretSeedInput.value = randomSeedHex();
    await loadLevels();
    if (!secretView.hidden) await loadSecretPreview();
    await loadSolutions();
    if (solutionSelect.value) await loadSolution();
    else await loadLevel();
    startSolutionPolling();
  })();
})();
