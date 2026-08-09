"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  LAB_FORMAT,
  LAB_VERSION,
  PATTERNS,
  parseRle,
  normalizeCells,
  cellBounds,
  transformCells,
  buildStampArray,
  cellsToSet,
  setToCells,
  stepLife,
  projectedExactScore,
  solutionRunToPlacement,
  validateLabState,
  panViewByKey,
  rasterizeCellLine,
} = require("../viz/idea-lab.js");
const {
  cellsToPacked,
  packedToCells,
  packedCellsInRect,
  runPackedLife,
  stepPackedLife,
  stepPackedLifeInRect,
} = require("../viz/life-engine.js");
const {
  isAtFinalGeneration,
  runNavigationPosition,
  selectVideoMimeType,
  videoExportFilename,
} = require("../viz/playback-state.js");
const { urlWithTab } = require("../viz/tab-state.js");

function referenceLife(cells) {
  const live = new Set(cells.map(([x, y]) => `${x},${y}`));
  const counts = new Map();
  for (const liveKey of live) {
    const [x, y] = liveKey.split(",").map(Number);
    for (let deltaY = -1; deltaY <= 1; deltaY++) {
      for (let deltaX = -1; deltaX <= 1; deltaX++) {
        if (deltaX === 0 && deltaY === 0) continue;
        const key = `${x + deltaX},${y + deltaY}`;
        counts.set(key, (counts.get(key) || 0) + 1);
      }
    }
  }
  const next = [];
  for (const [key, count] of counts) {
    if (count === 3 || (count === 2 && live.has(key))) {
      next.push(key.split(",").map(Number));
    }
  }
  return next.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
}

test("final-generation playback remains sticky while changing runs", () => {
  assert.equal(isAtFinalGeneration({ gen: 2418, limit: 2418 }), true);
  assert.equal(isAtFinalGeneration({ gen: 100, limit: 2418 }, 2418), true);
  assert.equal(isAtFinalGeneration({ gen: 2417, limit: 2418 }), false);
  assert.equal(isAtFinalGeneration({ gen: 100, limit: 2418 }, 2000), false);
  assert.equal(isAtFinalGeneration(null), false);
});

test("run navigation counts available runs instead of the level limit", () => {
  const runs = [
    ...Array.from({ length: 10 }, (_, index) => ({ level: 3, level_run: index + 1 })),
    ...Array.from({ length: 7 }, (_, index) => ({ level: 4, level_run: index + 1 })),
  ];
  assert.deepEqual(runNavigationPosition(runs, 9), { ordinal: 10, total: 10 });
  assert.deepEqual(runNavigationPosition(runs, 16), { ordinal: 7, total: 7 });
  assert.equal(runNavigationPosition(runs, 17), null);
});

test("video export chooses a supported format and stable run filename", () => {
  assert.equal(
    selectVideoMimeType((mimeType) => mimeType === "video/webm;codecs=vp8"),
    "video/webm;codecs=vp8"
  );
  assert.equal(selectVideoMimeType(() => false), "");
  assert.equal(
    videoExportFilename({
      solutionName: "bw57.wasm",
      level: 2,
      runNumber: 16,
      generations: 1325,
      mimeType: "video/webm;codecs=vp9",
    }),
    "golduck-bw57-l2-run-16-1325g-120gps.webm"
  );
});

test("tab URLs preserve the rest of the location", () => {
  assert.equal(
    urlWithTab("http://localhost:5000/?seed=abc#score", "idea-lab"),
    "http://localhost:5000/?seed=abc&tab=idea-lab#score"
  );
  assert.equal(
    urlWithTab("http://localhost:5000/?tab=secret&seed=abc", "scoring"),
    "http://localhost:5000/?tab=scoring&seed=abc"
  );
});

test("bulk stamp arrays place a spaced fleet in one normalized cell set", () => {
  const glider = PATTERNS.find((pattern) => pattern.id === "glider").cells;
  const fleet = buildStampArray(glider, 3, 2, 1, 2);
  assert.equal(fleet.length, 30);
  assert.deepEqual(
    {
      minX: Math.min(...fleet.map(([x]) => x)),
      maxX: Math.max(...fleet.map(([x]) => x)),
      minY: Math.min(...fleet.map(([, y]) => y)),
      maxY: Math.max(...fleet.map(([, y]) => y)),
    },
    { minX: 0, maxX: 10, minY: 0, maxY: 7 }
  );
  assert(fleet.some(([x, y]) => x === 1 && y === 0));
  assert(fleet.some(([x, y]) => x === 5 && y === 0));
  assert(fleet.some(([x, y]) => x === 1 && y === 5));

  const hwss = PATTERNS.find((pattern) => pattern.id === "hwss").cells;
  assert.throws(() => buildStampArray(hwss, 64, 64, 0, 0), /limited/);
});

test("solution runs become editable placements without losing world coordinates", () => {
  const converted = solutionRunToPlacement({
    level: 2,
    level_run: 4,
    generations: 2600,
    cells: [[-20, 8], [-18, 9], [-20, 8]],
  }, "probe.wasm");
  assert.equal(converted.level, 2);
  assert.equal(converted.levelRun, 4);
  assert.equal(converted.generations, 2600);
  assert.deepEqual(converted.placement, {
    stampId: "solution",
    name: "probe.wasm · L2 run 4",
    x: -20,
    y: 8,
    cells: [[0, 0], [2, 1]],
  });
});

test("Idea Lab accepts Level 4's full generation range", () => {
  const converted = solutionRunToPlacement({
    level: 4,
    level_run: 1,
    generations: 200_000,
    cells: [],
  });
  assert.equal(converted.generations, 200_000);

  const state = validateLabState({
    format: LAB_FORMAT,
    version: LAB_VERSION,
    level: 4,
    seed: "44".repeat(16),
    placements: [],
    target: 200_000,
    generation: 200_000,
    liveCells: [[0, 0]],
    scoreContext: {
      solution: "level4.wasm",
      level: 4,
      selectedIndex: 0,
      stats: [{ cellCount: 0, generations: 200_000 }],
    },
  });
  assert.equal(state.target, 200_000);
  assert.equal(state.generation, 200_000);
  assert.equal(state.scoreContext.stats[0].generations, 200_000);
});

test("projected exact score matches the official one-run best-case formula", () => {
  const score = projectedExactScore(
    [{ cellCount: 0, generations: 0 }],
    200_000,
    10_000
  );
  assert.equal(score.runs, 1);
  assert.equal(score.baseScore, 900_000);
  assert.equal(score.runBonus, 50_000);
  assert.equal(score.densityBonus, 25_000);
  assert.equal(score.generationsBonus, 25_000);
  assert.equal(score.exactBonus, 100_000);
  assert.equal(score.score, 1_100_000);
});

test("WASD pans the view at a zoom-independent screen speed", () => {
  const original = { x: 10, y: 30, scale: 4 };
  assert.deepEqual(panViewByKey(original, "w"), { x: 10, y: 10, scale: 4 });
  assert.deepEqual(panViewByKey(original, "d"), { x: 30, y: 30, scale: 4 });
  assert.deepEqual(panViewByKey(original, "a", true), { x: -50, y: 30, scale: 4 });
  assert.deepEqual(original, { x: 10, y: 30, scale: 4 });
});

test("pixel strokes cover every grid cell between pointer samples", () => {
  assert.deepEqual(
    rasterizeCellLine({ x: 2, y: 4 }, { x: 6, y: 4 }),
    [[2, 4], [3, 4], [4, 4], [5, 4], [6, 4]]
  );
  assert.deepEqual(
    rasterizeCellLine({ x: 3, y: 5 }, { x: 0, y: 2 }),
    [[3, 5], [2, 4], [1, 3], [0, 2]]
  );
  assert.throws(
    () => rasterizeCellLine({ x: 0.5, y: 0 }, { x: 1, y: 1 }),
    /integer/
  );
});

test("built-in spaceship stamps have their canonical live-cell counts", () => {
  const counts = Object.fromEntries(PATTERNS.map((pattern) => [pattern.id, pattern.cells.length]));
  assert.deepEqual(
    {
      glider: counts.glider,
      lwss: counts.lwss,
      mwss: counts.mwss,
      hwss: counts.hwss,
    },
    { glider: 5, lwss: 9, mwss: 11, hwss: 13 }
  );

  for (const id of ["glider", "lwss", "mwss", "hwss"]) {
    const pattern = PATTERNS.find((candidate) => candidate.id === id);
    let live = cellsToSet(pattern.cells);
    for (let generation = 0; generation < 4; generation++) live = stepLife(live);
    const evolved = transformCells(setToCells(live), 0, false);
    assert.deepEqual(evolved, pattern.cells, `${id} must translate after four generations`);
  }
});

test("the expanded moving-stamp palette translates every spaceship by its advertised period", () => {
  const expected = {
    glider: 5,
    lwss: 9,
    mwss: 11,
    hwss: 13,
    loafer: 20,
    copperhead: 28,
    weekender: 36,
    crab: 25,
    "canada-goose": 36,
  };
  const moving = PATTERNS.filter((pattern) => pattern.translation);
  assert.deepEqual(
    Object.fromEntries(moving.map((pattern) => [pattern.id, pattern.cells.length])),
    expected
  );

  for (const pattern of moving) {
    let live = cellsToSet(pattern.cells);
    for (let generation = 0; generation < pattern.period; generation++) {
      live = stepLife(live);
    }
    const evolved = setToCells(live);
    const initialBounds = cellBounds(pattern.cells);
    const evolvedBounds = cellBounds(evolved);
    assert.deepEqual(normalizeCells(evolved), pattern.cells, `${pattern.id} must keep its shape`);
    assert.deepEqual(
      [evolvedBounds.x - initialBounds.x, evolvedBounds.y - initialBounds.y],
      pattern.translation,
      `${pattern.id} must translate after one period`
    );
  }
});

test("RLE parsing and rotation preserve a normalized glider stamp", () => {
  const glider = parseRle("x = 3, y = 3, rule = B3/S23\nbob$2bo$3o!");
  assert.deepEqual(glider, PATTERNS.find((pattern) => pattern.id === "glider").cells);
  for (let rotation = 0; rotation < 4; rotation++) {
    const transformed = transformCells(glider, rotation, rotation % 2 === 1);
    assert.equal(transformed.length, 5);
    assert.equal(Math.min(...transformed.map(([x]) => x)), 0);
    assert.equal(Math.min(...transformed.map(([, y]) => y)), 0);
  }
});

test("the sparse Life step evolves a blinker", () => {
  const horizontal = cellsToSet([[0, 0], [1, 0], [2, 0]]);
  const vertical = setToCells(stepLife(horizontal)).sort((a, b) => a[1] - b[1]);
  assert.deepEqual(vertical, [[1, -1], [1, 0], [1, 1]]);
});

test("full-canvas playback keeps travelers alive past the old hidden margin", () => {
  const canvas = { x: -5000, y: -5000, w: 10000, h: 10000 };
  const glider = PATTERNS.find((pattern) => pattern.id === "glider").cells
    .map(([x, y]) => [x, y + 400]);
  let live = cellsToPacked(glider);
  for (let generation = 0; generation < 6000; generation++) {
    live = stepPackedLifeInRect(live, canvas, 1000);
  }
  const cells = packedToCells(live);
  assert.equal(cells.length, 5);
  assert(Math.min(...cells.map(([x]) => x)) >= 1500);
  assert(Math.min(...cells.map(([, y]) => y)) >= 1900);
});

test("full-canvas playback clips cells at the displayed rectangle", () => {
  const canvas = { x: -2, y: -3, w: 5, h: 7 };
  const clipped = packedToCells(packedCellsInRect(cellsToPacked([
    [-3, 0],
    [-2, -3],
    [2, 3],
    [3, 0],
  ]), canvas));
  assert.deepEqual(clipped, [[-2, -3], [2, 3]]);
});

test("full-canvas playback applies Life at the displayed edge", () => {
  const canvas = { x: 0, y: 0, w: 5, h: 5 };
  const next = packedToCells(stepPackedLifeInRect(
    cellsToPacked([[0, 0], [1, 0], [2, 0]]),
    canvas
  )).sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  assert.deepEqual(next, [[1, 0], [1, 1]]);
});

test("typed Life engine matches the reference across dense and disconnected components", () => {
  let cells = [];
  let random = 0x9e3779b9;
  for (let index = 0; index < 180; index++) {
    random = (Math.imul(random, 1664525) + 1013904223) >>> 0;
    const x = (random % 90) - 45;
    random = (Math.imul(random, 1664525) + 1013904223) >>> 0;
    const y = (random % 90) - 45;
    cells.push([x, y]);
  }
  cells.push([5000, 5000], [5001, 5000], [5000, 5001], [5001, 5001]);

  let packed = cellsToPacked(cells);
  for (let generation = 0; generation < 12; generation++) {
    cells = referenceLife(cells);
    packed = stepPackedLife(packed);
    assert.deepEqual(
      packedToCells(packed).sort((a, b) => a[1] - b[1] || a[0] - b[0]),
      cells,
      `generation ${generation + 1}`
    );
  }
});

test("typed Life engine handles quadratic Spacefiller growth within the UI cell limit", () => {
  const spacefiller = parseRle(`x = 25, y = 35, rule = B3/S23
5b3o9b3o$5bo2bo8bo2bo$5bo6bo4bo$5bo5b3o3bo$5bo5bob2o2bo$
5bo6b3o2bo$6bo2bo2b3o3bo$8bo3b3o$9bo5b2o$10b2o$10bo$11b2o$
bo8bobobo8bo$o5bobobobob2o2bo5bo$o5b3o3bobob3o5bo$
5obo3b2o2bo3bob5o$8bobo2b2obo$7b2obobobob2o$8bob2o2bobo$
5obo3bo2b2o3bob5o$o5b3obobo3b3o5bo$o5bo2b2obobobobo5bo$
bo8bobobo8bo$12b2o$14bo$13b2o$8b2o5bo$10b3o3bo$
6bo3b3o2bo2bo$7bo2b3o6bo$7bo2b2obo5bo$7bo3b3o5bo$
7bo4bo6bo$4bo2bo8bo2bo$5b3o9b3o!`);
  const evolved = runPackedLife(cellsToPacked(spacefiller), 400, 250_000);
  assert.equal(evolved.length, 43_597);
  assert.throws(
    () => runPackedLife(cellsToPacked(spacefiller), 1, 100),
    /exceeded 100 live cells/
  );
});

test("packed numeric cell keys preserve large signed coordinates", () => {
  const cells = [[-10_000_000, 10_000_000], [10_000_000, -10_000_000]];
  const packed = cellsToSet(cells);
  assert.equal(typeof packed.values().next().value, "number");
  assert.deepEqual(setToCells(packed), cells);

  const baseX = 9_000_000;
  const baseY = -9_000_000;
  const horizontal = cellsToSet([
    [baseX, baseY],
    [baseX + 1, baseY],
    [baseX + 2, baseY],
  ]);
  const vertical = setToCells(stepLife(horizontal)).sort((a, b) => a[1] - b[1]);
  assert.deepEqual(vertical, [
    [baseX + 1, baseY - 1],
    [baseX + 1, baseY],
    [baseX + 1, baseY + 1],
  ]);
});

test("a glider translates one cell after four generations", () => {
  const glider = PATTERNS.find((pattern) => pattern.id === "glider").cells;
  let live = cellsToSet(glider);
  for (let generation = 0; generation < 4; generation++) live = stepLife(live);
  const translated = setToCells(live)
    .map(([x, y]) => [x - 1, y - 1])
    .sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  assert.deepEqual(translated, glider);
});

test("saved lab state validates stamps without requiring scoring masks", () => {
  const state = validateLabState({
    format: LAB_FORMAT,
    version: LAB_VERSION,
    level: 1,
    seed: "ab".repeat(16),
    placements: [
      {
        stampId: "glider",
        name: "Glider",
        x: -20,
        y: -400,
        cells: PATTERNS[0].cells,
      },
    ],
    selectedStampId: "glider",
    rotation: 0,
    mirrored: false,
    tool: "stamp",
    target: 1000,
    generation: 0,
    liveCells: null,
    bulk: { enabled: true, columns: 8, rows: 2, gapX: 4, gapY: 6 },
    playback: { speed: 500, stepSize: 64 },
    scoreContext: {
      solution: "probe.wasm",
      level: 1,
      selectedIndex: 0,
      stats: [{ cellCount: 5, generations: 1000 }],
    },
    view: { x: 0, y: -400, scale: 8 },
    display: { grid: true, regions: true, secret: true },
  });
  assert.equal(state.placements.length, 1);
  assert.equal(state.seed, "ab".repeat(16));
  assert.deepEqual(state.scoreContext.stats, [{ cellCount: 5, generations: 1000 }]);
  assert.deepEqual(state.bulk, {
    enabled: true,
    columns: 8,
    rows: 2,
    gapX: 4,
    gapY: 6,
  });
  assert.deepEqual(state.playback, { speed: 500, stepSize: 64 });
  assert.equal("known_mask" in state, false);
  assert.equal("guess_mask" in state, false);
});

test("saved lab state preserves the pixel drawing tool", () => {
  const state = validateLabState({
    format: LAB_FORMAT,
    version: LAB_VERSION,
    level: 0,
    seed: "00".repeat(16),
    placements: [],
    tool: "pixel",
    target: 1000,
  });
  assert.equal(state.tool, "pixel");
  assert.deepEqual(state.playback, { speed: 30, stepSize: 1 });
});

test("saved lab state rejects unsupported playback controls", () => {
  const base = {
    format: LAB_FORMAT,
    version: LAB_VERSION,
    level: 0,
    seed: "00".repeat(16),
    placements: [],
    target: 1000,
  };
  assert.throws(
    () => validateLabState({ ...base, playback: { speed: 501, stepSize: 1 } }),
    /playback\.speed/
  );
  assert.throws(
    () => validateLabState({ ...base, playback: { speed: 500, stepSize: 0 } }),
    /playback\.stepSize/
  );
});

test("invalid or unbounded RLE input is rejected", () => {
  assert.throws(() => parseRle("bob$2bo$3o"), /end with/);
  assert.throws(() => parseRle("10000001o!"), /run length|limited|large/);
});
