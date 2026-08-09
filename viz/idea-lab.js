(() => {
  "use strict";

  const LAB_FORMAT = "golduck-idea-lab";
  const LAB_VERSION = 1;
  const MAX_CUSTOM_CELLS = 20_000;
  const MAX_BULK_CELLS = 50_000;
  const MAX_BULK_AXIS = 64;
  const MAX_PLACEMENTS = 1_000;
  const MAX_STATE_CELLS = 250_000;
  const MAX_LIVE_CELLS = 250_000;
  const MAX_COORDINATE = 10_000_000;
  const MAX_LEVEL_GENERATIONS = 200_000;
  const SNAPSHOT_EVERY = 32;
  const MAX_SNAPSHOTS = 64;
  const MAX_SNAPSHOT_BYTES = 24 * 1024 * 1024;
  const WORKER_BATCH_GENERATIONS = 16;
  const PROGRESS_PAINT_INTERVAL_MS = 80;
  const PLAYBACK_SPEEDS = Object.freeze([10, 30, 120, 500, 100_000]);
  const DEFAULT_PLAYBACK_SPEED = 30;
  const DEFAULT_STEP_SIZE = 1;
  const SOLUTIONS_CHANGED_EVENT = "golduck:solutions-changed";
  const CELL_KEY_STRIDE = 2 ** 26;
  const CELL_KEY_OFFSET = 2 ** 25;
  const LIFE_ENGINE = globalThis.GolduckLifeEngine
    || (typeof require === "function" ? require("./life-engine.js") : null);

  const PATTERNS = [
    {
      id: "glider",
      name: "Glider",
      detail: "5 cells · c/4 diagonal",
      period: 4,
      translation: [1, 1],
      cells: [[1, 0], [2, 1], [0, 2], [1, 2], [2, 2]],
    },
    {
      id: "lwss",
      name: "LWSS",
      detail: "9 cells · c/2 orthogonal",
      period: 4,
      translation: [-2, 0],
      cells: [
        [1, 0], [4, 0], [0, 1], [0, 2], [4, 2],
        [0, 3], [1, 3], [2, 3], [3, 3],
      ],
    },
    {
      id: "mwss",
      name: "MWSS",
      detail: "11 cells · c/2 orthogonal",
      period: 4,
      translation: [-2, 0],
      cells: [
        [3, 0], [1, 1], [5, 1], [0, 2], [0, 3], [5, 3],
        [0, 4], [1, 4], [2, 4], [3, 4], [4, 4],
      ],
    },
    {
      id: "hwss",
      name: "HWSS",
      detail: "13 cells · c/2 orthogonal",
      period: 4,
      translation: [-2, 0],
      cells: [
        [3, 0], [4, 0], [1, 1], [6, 1], [0, 2], [0, 3], [6, 3],
        [0, 4], [1, 4], [2, 4], [3, 4], [4, 4], [5, 4],
      ],
    },
    {
      id: "loafer",
      name: "Loafer",
      detail: "20 cells · c/7 orthogonal",
      period: 7,
      translation: [-1, 0],
      cells: [
        [1, 0], [2, 0], [5, 0], [7, 0], [8, 0], [0, 1], [3, 1],
        [6, 1], [7, 1], [1, 2], [3, 2], [2, 3], [8, 4], [6, 5],
        [7, 5], [8, 5], [5, 6], [6, 7], [7, 8], [8, 8],
      ],
    },
    {
      id: "copperhead",
      name: "Copperhead",
      detail: "28 cells · c/10 orthogonal",
      period: 10,
      translation: [0, -1],
      cells: [
        [1, 0], [2, 0], [5, 0], [6, 0], [3, 1], [4, 1], [3, 2],
        [4, 2], [0, 3], [2, 3], [5, 3], [7, 3], [0, 4], [7, 4],
        [0, 6], [7, 6], [1, 7], [2, 7], [5, 7], [6, 7], [2, 8],
        [3, 8], [4, 8], [5, 8], [3, 10], [4, 10], [3, 11], [4, 11],
      ],
    },
    {
      id: "weekender",
      name: "Weekender",
      detail: "36 cells · 2c/7 orthogonal",
      period: 7,
      translation: [0, -2],
      cells: [
        [1, 0], [14, 0], [1, 1], [14, 1], [0, 2], [2, 2], [13, 2],
        [15, 2], [1, 3], [14, 3], [1, 4], [14, 4], [2, 5], [6, 5],
        [7, 5], [8, 5], [9, 5], [13, 5], [6, 6], [7, 6], [8, 6],
        [9, 6], [2, 7], [3, 7], [4, 7], [5, 7], [10, 7], [11, 7],
        [12, 7], [13, 7], [4, 9], [11, 9], [5, 10], [6, 10],
        [9, 10], [10, 10],
      ],
    },
    {
      id: "crab",
      name: "Crab",
      detail: "25 cells · c/4 diagonal",
      period: 4,
      translation: [-1, -1],
      cells: [
        [8, 0], [9, 0], [7, 1], [8, 1], [9, 2], [11, 3], [12, 3],
        [10, 4], [9, 6], [12, 6], [1, 7], [2, 7], [8, 7], [9, 7],
        [0, 8], [1, 8], [7, 8], [2, 9], [7, 9], [9, 9], [4, 10],
        [5, 10], [8, 10], [4, 11], [5, 11],
      ],
    },
    {
      id: "canada-goose",
      name: "Canada goose",
      detail: "36 cells · c/4 diagonal",
      period: 4,
      translation: [-1, -1],
      cells: [
        [0, 0], [1, 0], [2, 0], [0, 1], [10, 1], [11, 1], [1, 2],
        [8, 2], [9, 2], [10, 2], [12, 2], [3, 3], [4, 3], [7, 3],
        [8, 3], [4, 4], [8, 5], [4, 6], [5, 6], [9, 6], [3, 7],
        [5, 7], [7, 7], [8, 7], [3, 8], [5, 8], [8, 8], [10, 8],
        [11, 8], [2, 9], [7, 9], [8, 9], [2, 10], [3, 10], [2, 11],
        [3, 11],
      ],
    },
    {
      id: "blinker",
      name: "Blinker",
      detail: "3 cells · period 2",
      cells: [[0, 0], [1, 0], [2, 0]],
    },
    {
      id: "beacon",
      name: "Beacon",
      detail: "6 cells · period 2",
      cells: [[0, 0], [1, 0], [0, 1], [3, 2], [2, 3], [3, 3]],
    },
    {
      id: "pentadecathlon",
      name: "Pentadecathlon",
      detail: "12 cells · period 15",
      cells: [
        [2, 0], [7, 0], [0, 1], [1, 1], [3, 1], [4, 1],
        [5, 1], [6, 1], [8, 1], [9, 1], [2, 2], [7, 2],
      ],
    },
    {
      id: "r-pentomino",
      name: "R-pentomino",
      detail: "5 cells · methuselah",
      cells: [[1, 0], [2, 0], [0, 1], [1, 1], [1, 2]],
    },
    {
      id: "acorn",
      name: "Acorn",
      detail: "7 cells · long growth",
      cells: [[1, 0], [3, 1], [0, 2], [1, 2], [4, 2], [5, 2], [6, 2]],
    },
    {
      id: "diehard",
      name: "Diehard",
      detail: "7 cells · dies at 130",
      cells: [[6, 0], [0, 1], [1, 1], [1, 2], [5, 2], [6, 2], [7, 2]],
    },
  ];

  function normalizeCells(cells) {
    if (!Array.isArray(cells) || cells.length === 0) return [];
    const unique = new Map();
    for (const cell of cells) {
      if (!Array.isArray(cell) || cell.length !== 2) {
        throw new Error("each cell must be an [x, y] pair");
      }
      const x = Number(cell[0]);
      const y = Number(cell[1]);
      if (!Number.isInteger(x) || !Number.isInteger(y)) {
        throw new Error("cell coordinates must be integers");
      }
      unique.set(cellKey(x, y), [x, y]);
    }
    const values = [...unique.values()];
    const minX = Math.min(...values.map(([x]) => x));
    const minY = Math.min(...values.map(([, y]) => y));
    return values
      .map(([x, y]) => [x - minX, y - minY])
      .sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  }

  function cellBounds(cells) {
    if (!cells.length) return { x: 0, y: 0, w: 0, h: 0 };
    const xs = cells.map(([x]) => x);
    const ys = cells.map(([, y]) => y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    return { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
  }

  function transformCells(cells, rotation = 0, mirrored = false) {
    const turns = ((Number(rotation) % 4) + 4) % 4;
    const transformed = cells.map(([sourceX, sourceY]) => {
      let x = mirrored ? -sourceX : sourceX;
      let y = sourceY;
      for (let turn = 0; turn < turns; turn++) {
        [x, y] = [-y, x];
      }
      return [x, y];
    });
    return normalizeCells(transformed);
  }

  function buildStampArray(cells, columns, rows, gapX = 0, gapY = 0) {
    const base = normalizeCells(cells);
    if (!base.length) throw new Error("bulk placement needs a non-empty stamp");
    const checkedColumns = checkedInteger(columns, "bulk columns", 1, MAX_BULK_AXIS);
    const checkedRows = checkedInteger(rows, "bulk rows", 1, MAX_BULK_AXIS);
    const checkedGapX = checkedInteger(gapX, "bulk horizontal gap", 0, 10_000);
    const checkedGapY = checkedInteger(gapY, "bulk vertical gap", 0, 10_000);
    const requestedCells = base.length * checkedColumns * checkedRows;
    if (requestedCells > MAX_BULK_CELLS) {
      throw new Error(
        `bulk fleets are limited to ${MAX_BULK_CELLS.toLocaleString("en-US")} live cells`
      );
    }
    const bounds = cellBounds(base);
    const pitchX = bounds.w + checkedGapX;
    const pitchY = bounds.h + checkedGapY;
    const unique = new Map();
    for (let row = 0; row < checkedRows; row++) {
      for (let column = 0; column < checkedColumns; column++) {
        const offsetX = column * pitchX;
        const offsetY = row * pitchY;
        for (const [x, y] of base) {
          unique.set(cellKey(x + offsetX, y + offsetY), [x + offsetX, y + offsetY]);
        }
      }
    }
    return [...unique.values()].sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  }

  function parseRle(input) {
    if (typeof input !== "string" || !input.trim()) {
      throw new Error("paste a non-empty RLE pattern");
    }
    const body = input
      .replace(/\r/g, "")
      .split("\n")
      .filter((line) => !/^\s*#/.test(line) && !/^\s*x\s*=/.test(line))
      .join("")
      .replace(/\s/g, "");
    if (!body) throw new Error("RLE has no pattern body");

    const cells = [];
    let x = 0;
    let y = 0;
    let digits = "";
    let ended = false;
    for (const token of body) {
      if (/[0-9]/.test(token)) {
        digits += token;
        if (digits.length > 7) throw new Error("RLE run length is too large");
        continue;
      }
      const count = digits ? Number(digits) : 1;
      digits = "";
      if (!Number.isSafeInteger(count) || count <= 0) {
        throw new Error("RLE run lengths must be positive integers");
      }
      if (token === "b") {
        x += count;
      } else if (token === "o") {
        if (cells.length + count > MAX_CUSTOM_CELLS) {
          throw new Error(`custom stamps are limited to ${MAX_CUSTOM_CELLS.toLocaleString("en-US")} live cells`);
        }
        for (let offset = 0; offset < count; offset++) cells.push([x + offset, y]);
        x += count;
      } else if (token === "$") {
        y += count;
        x = 0;
      } else if (token === "!") {
        ended = true;
        break;
      } else {
        throw new Error(`unsupported RLE token ${JSON.stringify(token)}`);
      }
      if (x > 10_000 || y > 10_000) throw new Error("custom stamp dimensions are too large");
    }
    if (!ended) throw new Error("RLE pattern must end with !");
    if (!cells.length) throw new Error("RLE pattern has no live cells");
    return normalizeCells(cells);
  }

  function cellKey(x, y) {
    return (x + CELL_KEY_OFFSET) * CELL_KEY_STRIDE + y + CELL_KEY_OFFSET;
  }

  function keyCell(key) {
    const packedX = Math.floor(key / CELL_KEY_STRIDE);
    const packedY = key - packedX * CELL_KEY_STRIDE;
    return [packedX - CELL_KEY_OFFSET, packedY - CELL_KEY_OFFSET];
  }

  function cellsToSet(cells) {
    return new Set(cells.map(([x, y]) => cellKey(x, y)));
  }

  function setToCells(cells) {
    if (cells instanceof Float64Array && LIFE_ENGINE) {
      return LIFE_ENGINE.packedToCells(cells);
    }
    return [...cells].map(keyCell);
  }

  function stepLife(liveCells) {
    if (!LIFE_ENGINE) throw new Error("optimized Life engine is unavailable");
    const packed = LIFE_ENGINE.normalizedPacked(liveCells);
    return new Set(LIFE_ENGINE.stepPackedLife(packed));
  }

  function projectedExactScore(stats, contestantArea, maxGenerations) {
    if (!Array.isArray(stats) || stats.length === 0) {
      throw new Error("score stats must contain at least one run");
    }
    if (!Number.isInteger(contestantArea) || contestantArea <= 0) {
      throw new Error("contestant area must be a positive integer");
    }
    if (!Number.isInteger(maxGenerations) || maxGenerations < 0) {
      throw new Error("maximum generations must be a non-negative integer");
    }
    const checkedStats = stats.map((stat, index) => {
      if (!stat || typeof stat !== "object") {
        throw new Error(`score stats[${index}] must be an object`);
      }
      const cellCount = checkedInteger(
        stat.cellCount,
        `score stats[${index}].cellCount`,
        0,
        contestantArea
      );
      const generations = checkedInteger(
        stat.generations,
        `score stats[${index}].generations`,
        0,
        maxGenerations
      );
      return { cellCount, generations };
    });
    const clamp01 = (value) => Math.min(1, Math.max(0, value));
    const densityRatio = Math.max(...checkedStats.map((stat) =>
      clamp01(Math.log(stat.cellCount + 1) / Math.log(contestantArea + 1))
    ));
    const generationsRatio = maxGenerations === 0
      ? 0
      : Math.max(...checkedStats.map((stat) =>
        clamp01(Math.log(stat.generations + 1) / Math.log(maxGenerations + 1))
      ));
    const runBonus = 100_000 / (Math.max(checkedStats.length, 1) + 1);
    const densityBonus = 25_000 * (1 - densityRatio);
    const generationsBonus = 25_000 * (1 - generationsRatio);
    const baseScore = 900_000;
    const performanceScore = baseScore + runBonus + densityBonus + generationsBonus;
    const exactBonus = 100_000;
    return {
      runs: checkedStats.length,
      maxCellCount: Math.max(...checkedStats.map((stat) => stat.cellCount)),
      maxGenerationsUsed: Math.max(...checkedStats.map((stat) => stat.generations)),
      baseScore,
      runBonus,
      densityBonus,
      generationsBonus,
      performanceScore,
      exactBonus,
      score: performanceScore + exactBonus,
    };
  }

  function solutionRunToPlacement(run, solutionName = "Solution") {
    if (!run || typeof run !== "object" || Array.isArray(run)) {
      throw new Error("solution run must be an object");
    }
    const level = checkedInteger(run.level, "solution run level", 0, 31);
    const generations = checkedInteger(
      run.generations,
      "solution run generations",
      0,
      MAX_LEVEL_GENERATIONS
    );
    const levelRun = Number.isInteger(run.level_run) && run.level_run > 0
      ? checkedInteger(run.level_run, "solution run number", 1, 10_000)
      : 1;
    if (!Array.isArray(run.cells) || run.cells.length > MAX_STATE_CELLS) {
      throw new Error(
        `solution run cells must be an array with at most ${MAX_STATE_CELLS.toLocaleString("en-US")} entries`
      );
    }
    const safeName = typeof solutionName === "string" && solutionName.trim()
      ? solutionName.trim()
      : "Solution";
    if (run.cells.length === 0) {
      return { level, levelRun, generations, placement: null };
    }
    const absoluteCells = validateCellArray(run.cells, "solution run cells", {
      maxCells: MAX_STATE_CELLS,
    });
    const bounds = cellBounds(absoluteCells);
    const cells = normalizeCells(absoluteCells);
    return {
      level,
      levelRun,
      generations,
      placement: {
        stampId: "solution",
        name: `${safeName} · L${level} run ${levelRun}`.slice(0, 60),
        x: bounds.x,
        y: bounds.y,
        cells,
      },
    };
  }

  function canonicalSeed(value) {
    const normalized = String(value || "").trim().toLowerCase();
    const seed = normalized.startsWith("0x") ? normalized.slice(2) : normalized;
    if (!/^[0-9a-f]{32}$/.test(seed)) {
      throw new Error("seed must contain exactly 32 hexadecimal digits");
    }
    return seed;
  }

  function checkedInteger(value, label, minimum, maximum) {
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      throw new Error(`${label} must be an integer from ${minimum} to ${maximum}`);
    }
    return value;
  }

  function validatePlaybackSettings(value) {
    const playback = value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
    const speed = Number(playback.speed ?? DEFAULT_PLAYBACK_SPEED);
    if (!PLAYBACK_SPEEDS.includes(speed)) {
      throw new Error(
        `playback.speed must be one of ${PLAYBACK_SPEEDS.join(", ")}`
      );
    }
    return {
      speed,
      stepSize: checkedInteger(
        playback.stepSize ?? DEFAULT_STEP_SIZE,
        "playback.stepSize",
        1,
        MAX_LEVEL_GENERATIONS
      ),
    };
  }

  function validateScoreContext(value, level) {
    if (value === null || value === undefined) return null;
    if (typeof value !== "object" || Array.isArray(value)) {
      throw new Error("scoreContext must be an object or null");
    }
    const contextLevel = checkedInteger(value.level, "scoreContext.level", 0, 31);
    if (contextLevel !== level) throw new Error("scoreContext level must match lab level");
    if (!Array.isArray(value.stats) || value.stats.length === 0 || value.stats.length > MAX_PLACEMENTS) {
      throw new Error(`scoreContext.stats must contain 1–${MAX_PLACEMENTS} runs`);
    }
    const stats = value.stats.map((stat, index) => {
      if (!stat || typeof stat !== "object" || Array.isArray(stat)) {
        throw new Error(`scoreContext.stats[${index}] must be an object`);
      }
      return {
        cellCount: checkedInteger(
          stat.cellCount,
          `scoreContext.stats[${index}].cellCount`,
          0,
          MAX_STATE_CELLS
        ),
        generations: checkedInteger(
          stat.generations,
          `scoreContext.stats[${index}].generations`,
          0,
          MAX_LEVEL_GENERATIONS
        ),
      };
    });
    return {
      solution: typeof value.solution === "string"
        ? (value.solution.trim() || "Saved solution").slice(0, 200)
        : "Saved solution",
      level: contextLevel,
      selectedIndex: checkedInteger(
        value.selectedIndex,
        "scoreContext.selectedIndex",
        0,
        stats.length - 1
      ),
      stats,
    };
  }

  function validateCellArray(value, label, options = {}) {
    const maxCells = options.maxCells || MAX_STATE_CELLS;
    const relative = Boolean(options.relative);
    if (!Array.isArray(value) || value.length === 0 || value.length > maxCells) {
      throw new Error(`${label} must contain 1–${maxCells.toLocaleString("en-US")} cells`);
    }
    return value.map((cell, index) => {
      if (!Array.isArray(cell) || cell.length !== 2) {
        throw new Error(`${label}[${index}] must be an [x, y] pair`);
      }
      const limit = relative ? 10_000 : MAX_COORDINATE;
      const minimum = relative ? 0 : -limit;
      return [
        checkedInteger(cell[0], `${label}[${index}].x`, minimum, limit),
        checkedInteger(cell[1], `${label}[${index}].y`, minimum, limit),
      ];
    });
  }

  function validateLabState(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("lab state must be a JSON object");
    }
    if (value.format !== LAB_FORMAT || value.version !== LAB_VERSION) {
      throw new Error(`expected ${LAB_FORMAT} version ${LAB_VERSION}`);
    }
    const level = checkedInteger(value.level, "level", 0, 31);
    const placementsValue = value.placements;
    if (!Array.isArray(placementsValue) || placementsValue.length > MAX_PLACEMENTS) {
      throw new Error(`placements must be an array with at most ${MAX_PLACEMENTS.toLocaleString("en-US")} entries`);
    }
    let placementCellCount = 0;
    const placements = placementsValue.map((placement, index) => {
      if (!placement || typeof placement !== "object" || Array.isArray(placement)) {
        throw new Error(`placements[${index}] must be an object`);
      }
      const cells = validateCellArray(
        placement.cells,
        `placements[${index}].cells`,
        { relative: true, maxCells: MAX_STATE_CELLS }
      );
      placementCellCount += cells.length;
      if (placementCellCount > MAX_STATE_CELLS) {
        throw new Error(`placements contain more than ${MAX_STATE_CELLS.toLocaleString("en-US")} cells`);
      }
      const name = typeof placement.name === "string" ? placement.name.trim() : "Stamp";
      return {
        name: (name || "Stamp").slice(0, 60),
        stampId: typeof placement.stampId === "string" ? placement.stampId.slice(0, 60) : "saved",
        x: checkedInteger(placement.x, `placements[${index}].x`, -MAX_COORDINATE, MAX_COORDINATE),
        y: checkedInteger(placement.y, `placements[${index}].y`, -MAX_COORDINATE, MAX_COORDINATE),
        cells,
      };
    });

    let customStamp = null;
    if (value.customStamp !== null && value.customStamp !== undefined) {
      if (typeof value.customStamp !== "object" || Array.isArray(value.customStamp)) {
        throw new Error("customStamp must be an object or null");
      }
      const name = typeof value.customStamp.name === "string"
        ? value.customStamp.name.trim().slice(0, 60)
        : "Custom stamp";
      customStamp = {
        id: "custom",
        name: name || "Custom stamp",
        detail: "custom RLE",
        cells: validateCellArray(value.customStamp.cells, "customStamp.cells", {
          relative: true,
          maxCells: MAX_CUSTOM_CELLS,
        }),
      };
    }

    const generation = checkedInteger(
      value.generation || 0,
      "generation",
      0,
      MAX_LEVEL_GENERATIONS
    );
    let liveCells = null;
    if (generation > 0) {
      liveCells = validateCellArray(value.liveCells, "liveCells", {
        maxCells: MAX_STATE_CELLS,
      });
    }
    const viewState = value.view && typeof value.view === "object" ? value.view : {};
    const view = {
      x: Number.isFinite(viewState.x) ? viewState.x : 0,
      y: Number.isFinite(viewState.y) ? viewState.y : 0,
      scale: Number.isFinite(viewState.scale) && viewState.scale > 0
        ? Math.min(80, viewState.scale)
        : 4,
    };
    const displayValue = value.display && typeof value.display === "object"
      ? value.display
      : {};
    const bulkValue = value.bulk && typeof value.bulk === "object" && !Array.isArray(value.bulk)
      ? value.bulk
      : {};
    const scoreContext = validateScoreContext(value.scoreContext, level);
    const playback = validatePlaybackSettings(value.playback);
    return {
      format: LAB_FORMAT,
      version: LAB_VERSION,
      level,
      seed: canonicalSeed(value.seed),
      placements,
      customStamp,
      selectedStampId: typeof value.selectedStampId === "string"
        ? value.selectedStampId
        : "glider",
      rotation: checkedInteger(value.rotation || 0, "rotation", 0, 3),
      mirrored: Boolean(value.mirrored),
      tool: ["stamp", "pixel", "remove", "pan"].includes(value.tool) ? value.tool : "stamp",
      target: checkedInteger(
        value.target || 0,
        "target",
        0,
        MAX_LEVEL_GENERATIONS
      ),
      generation,
      liveCells,
      scoreContext,
      bulk: {
        enabled: Boolean(bulkValue.enabled),
        columns: checkedInteger(bulkValue.columns ?? 8, "bulk.columns", 1, MAX_BULK_AXIS),
        rows: checkedInteger(bulkValue.rows ?? 1, "bulk.rows", 1, MAX_BULK_AXIS),
        gapX: checkedInteger(bulkValue.gapX ?? 4, "bulk.gapX", 0, 10_000),
        gapY: checkedInteger(bulkValue.gapY ?? 4, "bulk.gapY", 0, 10_000),
      },
      playback,
      view,
      display: {
        grid: displayValue.grid !== false,
        regions: displayValue.regions !== false,
        secret: displayValue.secret !== false,
      },
    };
  }

  function panViewByKey(currentView, key, fast = false) {
    const next = { ...currentView };
    const distance = (fast ? 240 : 80) / Math.max(0.0001, currentView.scale);
    switch (key.toLowerCase()) {
      case "w": next.y -= distance; break;
      case "a": next.x -= distance; break;
      case "s": next.y += distance; break;
      case "d": next.x += distance; break;
      default: break;
    }
    return next;
  }

  function rasterizeCellLine(start, end) {
    if (
      !start || !end
      || !Number.isInteger(start.x) || !Number.isInteger(start.y)
      || !Number.isInteger(end.x) || !Number.isInteger(end.y)
    ) {
      throw new Error("cell line endpoints must use integer x and y coordinates");
    }
    const cells = [];
    let x = start.x;
    let y = start.y;
    const dx = Math.abs(end.x - start.x);
    const dy = Math.abs(end.y - start.y);
    const stepX = start.x < end.x ? 1 : -1;
    const stepY = start.y < end.y ? 1 : -1;
    let error = dx - dy;
    while (true) {
      cells.push([x, y]);
      if (x === end.x && y === end.y) break;
      const doubled = error * 2;
      if (doubled > -dy) {
        error -= dy;
        x += stepX;
      }
      if (doubled < dx) {
        error += dx;
        y += stepY;
      }
    }
    return cells;
  }

  const core = {
    LAB_FORMAT,
    LAB_VERSION,
    PATTERNS,
    PLAYBACK_SPEEDS,
    normalizeCells,
    cellBounds,
    transformCells,
    buildStampArray,
    parseRle,
    cellsToSet,
    setToCells,
    stepLife,
    projectedExactScore,
    solutionRunToPlacement,
    validateLabState,
    panViewByKey,
    rasterizeCellLine,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = core;
  if (typeof document === "undefined") return;

  const canvas = document.getElementById("idea-board");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const panel = document.querySelector(".lab-panel");
  const levelSelect = document.getElementById("lab-level");
  const seedInput = document.getElementById("lab-seed");
  const solutionSelect = document.getElementById("lab-solution");
  const solutionRunSelect = document.getElementById("lab-solution-run");
  const loadSolutionButton = document.getElementById("lab-load-solution");
  const solutionNote = document.getElementById("lab-solution-note");
  const palette = document.getElementById("lab-palette");
  const selectedStampLabel = document.getElementById("lab-selected-stamp");
  const orientationLabel = document.getElementById("lab-orientation");
  const bulkEnabled = document.getElementById("lab-bulk-enabled");
  const bulkColumns = document.getElementById("lab-bulk-columns");
  const bulkRows = document.getElementById("lab-bulk-rows");
  const bulkGapX = document.getElementById("lab-bulk-gap-x");
  const bulkGapY = document.getElementById("lab-bulk-gap-y");
  const bulkSummary = document.getElementById("lab-bulk-summary");
  const placementList = document.getElementById("lab-placement-list");
  const cellCount = document.getElementById("lab-cell-count");
  const undoButton = document.getElementById("lab-undo");
  const clearButton = document.getElementById("lab-clear");
  const generationLabel = document.getElementById("lab-generation");
  const targetInput = document.getElementById("lab-target");
  const stepInput = document.getElementById("lab-sim-step");
  const backButton = document.getElementById("lab-sim-back");
  const playButton = document.getElementById("lab-sim-play");
  const forwardButton = document.getElementById("lab-sim-forward");
  const speedSelect = document.getElementById("lab-sim-speed");
  const scoreTotal = document.getElementById("lab-score-total");
  const scoreStatus = document.getElementById("lab-score-status");
  const scoreAssumption = document.getElementById("lab-score-assumption");
  const scoreOutputs = {
    baseScore: document.getElementById("lab-score-base"),
    runBonus: document.getElementById("lab-score-runs"),
    densityBonus: document.getElementById("lab-score-density"),
    generationsBonus: document.getElementById("lab-score-generations"),
    exactBonus: document.getElementById("lab-score-exact"),
  };
  const showGrid = document.getElementById("lab-show-grid");
  const showRegions = document.getElementById("lab-show-regions");
  const showSecret = document.getElementById("lab-show-secret");
  const status = document.getElementById("lab-status");
  const fileInput = document.getElementById("lab-file");
  const toolButtons = {
    stamp: document.getElementById("lab-tool-stamp"),
    pixel: document.getElementById("lab-tool-pixel"),
    remove: document.getElementById("lab-tool-remove"),
    pan: document.getElementById("lab-tool-pan"),
  };

  const COLORS = {
    background: "#fbfcfc",
    ink: "#13262f",
    secret: "#1f7a6c",
    stamp: "#c9892d",
    stampDark: "#8a5a12",
    invalid: "#c45c26",
    contestant: "#c45c26",
    viewing: "#2a6f9e",
    canvas: "#7a9aa3",
    gridMinor: "rgba(19, 38, 47, 0.055)",
    gridMajor: "rgba(19, 38, 47, 0.13)",
    axis: "rgba(19, 38, 47, 0.48)",
  };

  let initialized = false;
  let active = false;
  let data = null;
  let loadSequence = 0;
  let solutionLoadSequence = 0;
  let solutionManifest = null;
  let pendingSolutionChoices = null;
  let scoreContext = null;
  let placements = [];
  let history = [];
  let nextPlacementId = 1;
  let customStamp = null;
  let selectedStamp = PATTERNS[0];
  let rotation = 0;
  let mirrored = false;
  let tool = "stamp";
  let view = { x: 0, y: 0, scale: 4 };
  let hoverCell = null;
  let hoverPlacement = null;
  let dragging = false;
  let lastPointer = null;
  let pixelDrawing = false;
  let pixelDrawAlive = true;
  let pixelStrokeChanged = 0;
  let pixelStrokeVisited = new Set();
  let lastPixelCell = null;
  let spaceHeld = false;
  let sim = null;
  let simulationToken = 0;
  let simulationBusy = false;
  let simulationWorker = null;
  let simulationWorkerSequence = 0;
  const simulationWorkerRequests = new Map();
  let play = {
    playing: false,
    speed: DEFAULT_PLAYBACK_SPEED,
    acc: 0,
    lastTime: 0,
    frame: null,
  };

  function randomSeedHex() {
    const words = new Uint32Array(4);
    window.crypto.getRandomValues(words);
    return Array.from(words, (word) => word.toString(16).padStart(8, "0")).join("");
  }

  function setStatus(message, error = false) {
    status.hidden = !message;
    status.textContent = message || "";
    status.classList.toggle("error", Boolean(message) && error);
  }

  function canvasRect() {
    return data?.rects?.find((rect) => rect.name === "canvas") || null;
  }

  function contestantRect() {
    return data?.rects?.find((rect) => rect.name === "contestant") || null;
  }

  function minimumScale() {
    const bounds = canvasRect();
    if (!bounds) return 0.02;
    return Math.max(
      0.0001,
      Math.min(canvas.clientWidth / (bounds.w * 1.08), canvas.clientHeight / (bounds.h * 1.08))
    );
  }

  function clampView() {
    const bounds = canvasRect();
    view.scale = Math.min(80, Math.max(minimumScale(), view.scale));
    if (!bounds) return;
    const halfWidth = canvas.clientWidth / (2 * view.scale);
    const halfHeight = canvas.clientHeight / (2 * view.scale);
    if (halfWidth * 2 >= bounds.w) view.x = bounds.x + bounds.w / 2;
    else view.x = Math.max(bounds.x + halfWidth, Math.min(bounds.x + bounds.w - halfWidth, view.x));
    if (halfHeight * 2 >= bounds.h) view.y = bounds.y + bounds.h / 2;
    else view.y = Math.max(bounds.y + halfHeight, Math.min(bounds.y + bounds.h - halfHeight, view.y));
  }

  function worldToScreen(x, y) {
    return {
      x: (x - view.x) * view.scale + canvas.clientWidth / 2,
      y: (y - view.y) * view.scale + canvas.clientHeight / 2,
    };
  }

  function screenToWorld(x, y) {
    return {
      x: (x - canvas.clientWidth / 2) / view.scale + view.x,
      y: (y - canvas.clientHeight / 2) / view.scale + view.y,
    };
  }

  function fitRect(bounds, padding = 0.1, minimum = 0) {
    if (!bounds || canvas.clientWidth <= 0 || canvas.clientHeight <= 0) return;
    const panelRight = canvas.clientWidth >= 720
      ? Math.ceil(panel.getBoundingClientRect().right + 18)
      : 0;
    const availableWidth = Math.max(1, canvas.clientWidth - panelRight);
    const scaleX = availableWidth / Math.max(1, bounds.w * (1 + padding));
    const scaleY = canvas.clientHeight / Math.max(1, bounds.h * (1 + padding));
    view.scale = Math.min(40, Math.max(minimum, Math.min(scaleX, scaleY)));
    const targetX = panelRight + availableWidth / 2;
    view.x = bounds.x + bounds.w / 2 - (targetX - canvas.clientWidth / 2) / view.scale;
    view.y = bounds.y + bounds.h / 2;
    clampView();
    draw();
  }

  function fitStart() {
    fitRect(contestantRect(), 0.08, 4);
  }

  function fitAll() {
    if (data?.focus) fitRect(data.focus, 0.12, 0);
  }

  function resize() {
    const bounds = canvas.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(bounds.width * dpr));
    canvas.height = Math.max(1, Math.floor(bounds.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    clampView();
    draw();
  }

  function niceGridStep(scale) {
    if (scale >= 7) return 1;
    const target = 70 / Math.max(scale, 0.0001);
    const magnitude = 10 ** Math.floor(Math.log10(target));
    const normalized = target / magnitude;
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
    ctx.beginPath();
    for (let x = x0; x <= bottomRight.x; x += step) {
      const point = worldToScreen(x, 0);
      ctx.moveTo(Math.round(point.x) + 0.5, 0);
      ctx.lineTo(Math.round(point.x) + 0.5, canvas.clientHeight);
    }
    for (let y = y0; y <= bottomRight.y; y += step) {
      const point = worldToScreen(0, y);
      ctx.moveTo(0, Math.round(point.y) + 0.5);
      ctx.lineTo(canvas.clientWidth, Math.round(point.y) + 0.5);
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function drawGrid() {
    if (!showGrid.checked) return;
    const step = niceGridStep(view.scale);
    if (step === 1) drawGridLines(1, COLORS.gridMinor);
    drawGridLines(step === 1 ? 10 : step, COLORS.gridMajor);
    const origin = worldToScreen(0, 0);
    ctx.beginPath();
    ctx.moveTo(Math.round(origin.x) + 0.5, 0);
    ctx.lineTo(Math.round(origin.x) + 0.5, canvas.clientHeight);
    ctx.moveTo(0, Math.round(origin.y) + 0.5);
    ctx.lineTo(canvas.clientWidth, Math.round(origin.y) + 0.5);
    ctx.strokeStyle = COLORS.axis;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  function rgba(hex, alpha) {
    const value = hex.replace("#", "");
    const red = parseInt(value.slice(0, 2), 16);
    const green = parseInt(value.slice(2, 4), 16);
    const blue = parseInt(value.slice(4, 6), 16);
    return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
  }

  function drawRegion(rectangle) {
    const point = worldToScreen(rectangle.x, rectangle.y);
    const width = rectangle.w * view.scale;
    const height = rectangle.h * view.scale;
    const color = COLORS[rectangle.name] || COLORS.canvas;
    ctx.save();
    ctx.fillStyle = rgba(color, rectangle.name === "canvas" ? 0.018 : 0.08);
    ctx.strokeStyle = color;
    ctx.lineWidth = rectangle.name === "canvas" ? 2.5 : 2;
    if (rectangle.name === "canvas") ctx.setLineDash([9, 6]);
    ctx.fillRect(point.x, point.y, width, height);
    ctx.strokeRect(point.x, point.y, width, height);
    ctx.restore();
    if (rectangle.name !== "canvas" && view.scale > 0.3) {
      ctx.fillStyle = color;
      ctx.font = "600 11px Sora, sans-serif";
      ctx.fillText(rectangle.name, point.x + 6, point.y - 6);
    }
  }

  function drawCellArray(cells, color, alpha = 1, originX = 0, originY = 0) {
    const size = Math.max(1, view.scale);
    ctx.fillStyle = alpha === 1 ? color : rgba(color, alpha);
    for (const [relativeX, relativeY] of cells) {
      const point = worldToScreen(originX + relativeX, originY + relativeY);
      if (
        point.x < -size || point.y < -size ||
        point.x > canvas.clientWidth + size || point.y > canvas.clientHeight + size
      ) continue;
      const gap = view.scale >= 5 ? Math.min(1, view.scale * 0.1) : 0;
      ctx.fillRect(
        point.x + gap / 2,
        point.y + gap / 2,
        Math.max(1, size - gap),
        Math.max(1, size - gap)
      );
    }
  }

  function drawCellSet(cells, color) {
    const size = Math.max(1, view.scale);
    const gap = view.scale >= 5 ? Math.min(1, view.scale * 0.1) : 0;
    const halfWidth = canvas.clientWidth / 2;
    const halfHeight = canvas.clientHeight / 2;
    const left = view.x - halfWidth / view.scale - 1;
    const right = view.x + halfWidth / view.scale + 1;
    const top = view.y - halfHeight / view.scale - 1;
    const bottom = view.y + halfHeight / view.scale + 1;
    ctx.fillStyle = color;
    for (const key of cells) {
      const packedX = Math.floor(key / CELL_KEY_STRIDE);
      const x = packedX - CELL_KEY_OFFSET;
      const y = key - packedX * CELL_KEY_STRIDE - CELL_KEY_OFFSET;
      if (x < left || x > right || y < top || y > bottom) continue;
      const screenX = (x - view.x) * view.scale + halfWidth;
      const screenY = (y - view.y) * view.scale + halfHeight;
      ctx.fillRect(
        screenX + gap / 2,
        screenY + gap / 2,
        Math.max(1, size - gap),
        Math.max(1, size - gap)
      );
    }
  }

  function currentStampCells() {
    return transformCells(selectedStamp.cells, rotation, mirrored);
  }

  function readBulkSettings() {
    return {
      enabled: bulkEnabled.checked,
      columns: checkedInteger(Number(bulkColumns.value), "bulk columns", 1, MAX_BULK_AXIS),
      rows: checkedInteger(Number(bulkRows.value), "bulk rows", 1, MAX_BULK_AXIS),
      gapX: checkedInteger(Number(bulkGapX.value), "bulk horizontal gap", 0, 10_000),
      gapY: checkedInteger(Number(bulkGapY.value), "bulk vertical gap", 0, 10_000),
    };
  }

  function currentStampSpec() {
    const base = currentStampCells();
    if (!bulkEnabled.checked) {
      return {
        cells: base,
        copies: 1,
        stampId: selectedStamp.id,
        name: selectedStamp.name,
      };
    }
    const settings = readBulkSettings();
    const copies = settings.columns * settings.rows;
    return {
      cells: buildStampArray(
        base,
        settings.columns,
        settings.rows,
        settings.gapX,
        settings.gapY
      ),
      copies,
      stampId: `${selectedStamp.id}-fleet`,
      name: `${selectedStamp.name.slice(0, 40)} fleet ${settings.columns}×${settings.rows}`,
    };
  }

  function updateBulkUI() {
    const enabled = bulkEnabled.checked;
    for (const input of [bulkColumns, bulkRows, bulkGapX, bulkGapY]) {
      input.disabled = !enabled;
    }
    bulkSummary.classList.remove("error");
    if (!enabled) {
      bulkSummary.textContent = "Single stamp · enable Bulk fleet to place an array with one click.";
      draw();
      return;
    }
    try {
      const spec = currentStampSpec();
      const bounds = cellBounds(spec.cells);
      bulkSummary.textContent =
        `${spec.copies.toLocaleString("en-US")} copies · `
        + `${spec.cells.length.toLocaleString("en-US")} live cells · `
        + `${bounds.w.toLocaleString("en-US")}×${bounds.h.toLocaleString("en-US")} cells · `
        + "click anchors the top-left copy";
    } catch (error) {
      bulkSummary.textContent = error.message || String(error);
      bulkSummary.classList.add("error");
    }
    draw();
  }

  function placementFits(anchorX, anchorY, cells = currentStampCells()) {
    const bounds = contestantRect();
    if (!bounds) return false;
    return cells.every(([x, y]) => {
      const worldX = anchorX + x;
      const worldY = anchorY + y;
      return (
        worldX >= bounds.x && worldX < bounds.x + bounds.w &&
        worldY >= bounds.y && worldY < bounds.y + bounds.h
      );
    });
  }

  function findPlacementAt(x, y) {
    for (let index = placements.length - 1; index >= 0; index--) {
      const placement = placements[index];
      if (placement.cells.some(([dx, dy]) => placement.x + dx === x && placement.y + dy === y)) {
        return placement;
      }
    }
    return null;
  }

  function setPlacedPixel(x, y, alive) {
    if (alive) {
      if (findPlacementAt(x, y)) return false;
      const bounds = contestantRect();
      if (!bounds) return false;
      let drawing = placements.find((placement) =>
        placement.stampId === "pixel-drawing"
        && placement.x === bounds.x
        && placement.y === bounds.y
      );
      if (!drawing) {
        drawing = {
          id: nextPlacementId++,
          stampId: "pixel-drawing",
          name: "Pixel drawing",
          x: bounds.x,
          y: bounds.y,
          cells: [],
        };
        placements.push(drawing);
      }
      drawing.cells.push([x - drawing.x, y - drawing.y]);
      return true;
    }

    let changed = false;
    for (let index = placements.length - 1; index >= 0; index--) {
      const placement = placements[index];
      const remaining = placement.cells.filter(
        ([dx, dy]) => placement.x + dx !== x || placement.y + dy !== y
      );
      if (remaining.length === placement.cells.length) continue;
      changed = true;
      if (remaining.length) placement.cells = remaining;
      else placements.splice(index, 1);
    }
    return changed;
  }

  function drawPixelLine(start, end) {
    for (const [x, y] of rasterizeCellLine(start, end)) {
      const key = cellKey(x, y);
      if (pixelStrokeVisited.has(key)) continue;
      pixelStrokeVisited.add(key);
      if (!placementFits(x, y, [[0, 0]])) continue;
      if (setPlacedPixel(x, y, pixelDrawAlive)) pixelStrokeChanged++;
    }
    lastPixelCell = { ...end };
  }

  function beginPixelStroke(event) {
    const cell = pointerCell(event);
    if (!placementFits(cell.x, cell.y, [[0, 0]])) {
      setStatus("Draw pixels inside the orange contestant start area.", true);
      return;
    }
    editAtGenerationZero();
    rememberState();
    pixelDrawing = true;
    pixelDrawAlive = !findPlacementAt(cell.x, cell.y);
    pixelStrokeChanged = 0;
    pixelStrokeVisited = new Set();
    lastPixelCell = cell;
    canvas.classList.add("drawing");
    canvas.setPointerCapture(event.pointerId);
    drawPixelLine(cell, cell);
    invalidateSimulation();
    draw();
  }

  function finishPixelStroke(event) {
    if (!pixelDrawing) return false;
    pixelDrawing = false;
    canvas.classList.remove("drawing");
    for (const placement of placements) {
      if (placement.stampId === "pixel-drawing") {
        placement.cells.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
      }
    }
    renderPlacements();
    updateSimulationUI();
    const action = pixelDrawAlive ? "drawn" : "erased";
    setStatus(
      `${pixelStrokeChanged.toLocaleString("en-US")} pixel${pixelStrokeChanged === 1 ? "" : "s"} ${action}.`
    );
    lastPixelCell = null;
    pixelStrokeVisited = new Set();
    if (event && canvas.hasPointerCapture?.(event.pointerId)) {
      canvas.releasePointerCapture(event.pointerId);
    }
    draw();
    return true;
  }

  function drawGhost() {
    if (!hoverCell || !data || (sim && sim.generation > 0)) return;
    if (tool === "stamp") {
      let cells;
      try {
        cells = currentStampSpec().cells;
      } catch {
        return;
      }
      const valid = placementFits(hoverCell.x, hoverCell.y, cells);
      const color = valid ? COLORS.secret : COLORS.invalid;
      drawCellArray(cells, color, 0.52, hoverCell.x, hoverCell.y);
      const bounds = cellBounds(cells);
      const point = worldToScreen(hoverCell.x + bounds.x, hoverCell.y + bounds.y);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(point.x, point.y, bounds.w * view.scale, bounds.h * view.scale);
    } else if (tool === "pixel") {
      const valid = placementFits(hoverCell.x, hoverCell.y, [[0, 0]]);
      const erasing = pixelDrawing ? !pixelDrawAlive : Boolean(findPlacementAt(hoverCell.x, hoverCell.y));
      const color = !valid || erasing ? COLORS.invalid : COLORS.stamp;
      drawCellArray([[0, 0]], color, 0.5, hoverCell.x, hoverCell.y);
      const point = worldToScreen(hoverCell.x, hoverCell.y);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(point.x, point.y, view.scale, view.scale);
    } else if (tool === "remove" && hoverPlacement) {
      drawCellArray(hoverPlacement.cells, COLORS.invalid, 0.62, hoverPlacement.x, hoverPlacement.y);
    }
  }

  function draw() {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = COLORS.background;
    ctx.fillRect(0, 0, width, height);
    drawGrid();
    if (!data) return;
    if (showRegions.checked) {
      const ordered = [...data.rects].sort((a, b) => (a.name === "canvas" ? -1 : 0) - (b.name === "canvas" ? -1 : 0));
      for (const rectangle of ordered) drawRegion(rectangle);
    }
    if (sim && sim.generation > 0) {
      drawCellSet(sim.cells, COLORS.ink);
    } else {
      if (showSecret.checked) drawCellArray(data.cells || [], COLORS.secret);
      for (const placement of placements) {
        drawCellArray(placement.cells, COLORS.stamp, 1, placement.x, placement.y);
      }
      drawGhost();
    }
  }

  function drawPatternPreview(preview, cells) {
    const previewCtx = preview.getContext("2d");
    const size = 48;
    preview.width = size;
    preview.height = size;
    previewCtx.clearRect(0, 0, size, size);
    const bounds = cellBounds(cells);
    const scale = Math.max(2, Math.min(8, 34 / Math.max(bounds.w, bounds.h, 1)));
    const offsetX = (size - bounds.w * scale) / 2;
    const offsetY = (size - bounds.h * scale) / 2;
    previewCtx.fillStyle = COLORS.secret;
    for (const [x, y] of cells) {
      previewCtx.fillRect(offsetX + x * scale, offsetY + y * scale, Math.max(1, scale - 1), Math.max(1, scale - 1));
    }
  }

  function availablePatterns() {
    return customStamp ? [...PATTERNS, customStamp] : PATTERNS;
  }

  function renderPalette() {
    palette.innerHTML = "";
    for (const pattern of availablePatterns()) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "lab-stamp";
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(pattern.id === selectedStamp.id));
      if (pattern.id === selectedStamp.id) button.classList.add("active");
      const preview = document.createElement("canvas");
      preview.setAttribute("aria-hidden", "true");
      drawPatternPreview(preview, pattern.cells);
      const copy = document.createElement("span");
      copy.className = "lab-stamp-copy";
      const name = document.createElement("strong");
      name.textContent = pattern.name;
      const detail = document.createElement("small");
      detail.textContent = pattern.detail;
      copy.append(name, detail);
      button.append(preview, copy);
      button.addEventListener("click", () => selectStamp(pattern));
      palette.append(button);
    }
    selectedStampLabel.textContent = selectedStamp.name;
  }

  function selectStamp(pattern) {
    selectedStamp = pattern;
    setTool("stamp");
    renderPalette();
    updateBulkUI();
  }

  function updateOrientation() {
    orientationLabel.textContent = `${rotation * 90}°${mirrored ? " · mirrored" : ""}`;
    updateBulkUI();
  }

  function rotate(delta) {
    rotation = (rotation + delta + 4) % 4;
    updateOrientation();
  }

  function setTool(nextTool) {
    tool = nextTool;
    canvas.dataset.tool = tool;
    for (const [name, button] of Object.entries(toolButtons)) {
      const selected = name === tool;
      button.classList.toggle("active", selected);
      button.classList.toggle("ghost", !selected);
      button.setAttribute("aria-pressed", String(selected));
    }
    hoverPlacement = hoverCell ? findPlacementAt(hoverCell.x, hoverCell.y) : null;
    draw();
  }

  function clonePlacements(value = placements) {
    return value.map((placement) => ({
      ...placement,
      cells: placement.cells.map(([x, y]) => [x, y]),
    }));
  }

  function rememberState() {
    history.push(clonePlacements());
    if (history.length > 100) history.shift();
  }

  function invalidateSimulation() {
    simulationToken++;
    terminateSimulationWorker();
    simulationBusy = false;
    play.playing = false;
    play.acc = 0;
    sim = null;
    updateSimulationUI();
  }

  function editAtGenerationZero() {
    if (sim && sim.generation > 0) {
      invalidateSimulation();
      setStatus("Returned to generation 0 before editing the starting pattern.");
    }
  }

  function placeStamp(x, y) {
    editAtGenerationZero();
    let spec;
    try {
      spec = currentStampSpec();
    } catch (error) {
      setStatus(error.message || String(error), true);
      return;
    }
    const cells = spec.cells;
    if (!placementFits(x, y, cells)) {
      setStatus(
        spec.copies > 1
          ? "The entire bulk fleet must fit inside the contestant start area."
          : "The entire stamp must fit inside the contestant start area.",
        true
      );
      return;
    }
    rememberState();
    placements.push({
      id: nextPlacementId++,
      stampId: spec.stampId,
      name: spec.name,
      x,
      y,
      cells,
    });
    invalidateSimulation();
    renderPlacements();
    setStatus(
      spec.copies > 1
        ? `${spec.name} placed as one ${spec.cells.length.toLocaleString("en-US")}-cell fleet at ${x}, ${y}.`
        : `${selectedStamp.name} placed at ${x}, ${y}.`
    );
    draw();
  }

  function removePlacement(id) {
    const index = placements.findIndex((placement) => placement.id === id);
    if (index < 0) return;
    editAtGenerationZero();
    rememberState();
    const [removed] = placements.splice(index, 1);
    invalidateSimulation();
    renderPlacements();
    setStatus(`${removed.name} removed.`);
    draw();
  }

  function removeAt(x, y) {
    const placement = findPlacementAt(x, y);
    if (!placement) {
      setStatus("No stamp occupies that cell.", true);
      return;
    }
    removePlacement(placement.id);
  }

  function uniquePlacedCellCount() {
    const cells = new Set();
    for (const placement of placements) {
      for (const [x, y] of placement.cells) cells.add(cellKey(placement.x + x, placement.y + y));
    }
    return cells.size;
  }

  function formatScore(value) {
    return value.toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    });
  }

  function clearScoreOutputs() {
    scoreTotal.textContent = "—";
    for (const output of Object.values(scoreOutputs)) output.textContent = "—";
  }

  function currentScoreStats(target) {
    const current = {
      cellCount: uniquePlacedCellCount(),
      generations: target,
    };
    if (
      !scoreContext
      || scoreContext.level !== Number(levelSelect.value)
      || !Array.isArray(scoreContext.stats)
      || scoreContext.selectedIndex < 0
      || scoreContext.selectedIndex >= scoreContext.stats.length
    ) {
      return { stats: [current], context: null };
    }
    const stats = scoreContext.stats.map((stat, index) =>
      index === scoreContext.selectedIndex ? current : { ...stat }
    );
    return { stats, context: scoreContext };
  }

  function updateScoreUI() {
    if (!data) {
      clearScoreOutputs();
      scoreStatus.textContent = "Load a level, then run to the target generation.";
      return;
    }
    let target;
    try {
      target = targetGeneration();
    } catch (error) {
      clearScoreOutputs();
      scoreStatus.textContent = error.message || String(error);
      return;
    }
    const { stats, context } = currentScoreStats(target);
    scoreAssumption.textContent = context
      ? `Projects ${context.solution} with this selected run changed; assumes a correct answer with all 64 bits known.`
      : "Assumes this canvas is one run and a correct answer with all 64 bits known.";
    const generation = sim?.generation || 0;
    if (generation !== target) {
      clearScoreOutputs();
      scoreStatus.textContent = generation < target
        ? `Run ${formatScore(target - generation)} more generation${target - generation === 1 ? "" : "s"} to calculate.`
        : `Return to target generation ${formatScore(target)} to calculate.`;
      return;
    }
    try {
      const bounds = contestantRect();
      if (!bounds) throw new Error("level has no contestant scoring area");
      const result = projectedExactScore(
        stats,
        bounds.w * bounds.h,
        data.generations.max
      );
      scoreTotal.textContent = formatScore(result.score);
      for (const [key, output] of Object.entries(scoreOutputs)) {
        output.textContent = formatScore(result[key]);
      }
      scoreStatus.textContent =
        `Target reached · ${result.runs} run${result.runs === 1 ? "" : "s"} · `
        + `max ${formatScore(result.maxCellCount)} cells · `
        + `max ${formatScore(result.maxGenerationsUsed)} generations`;
    } catch (error) {
      clearScoreOutputs();
      scoreStatus.textContent = `Score unavailable: ${error.message || String(error)}`;
    }
  }

  function renderPlacements() {
    placementList.innerHTML = "";
    for (const placement of [...placements].reverse()) {
      const item = document.createElement("li");
      item.className = "lab-placement-item";
      const copy = document.createElement("span");
      copy.className = "lab-placement-copy";
      const name = document.createElement("strong");
      name.textContent = placement.name;
      const details = document.createElement("small");
      const bounds = cellBounds(placement.cells);
      details.textContent = `${placement.x + bounds.x},${placement.y + bounds.y} · ${bounds.w}×${bounds.h} · ${placement.cells.length} cells`;
      copy.append(name, details);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "ghost";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => removePlacement(placement.id));
      item.append(copy, remove);
      placementList.append(item);
    }
    const cells = uniquePlacedCellCount();
    cellCount.textContent = `${cells.toLocaleString("en-US")} cell${cells === 1 ? "" : "s"}`;
    undoButton.disabled = history.length === 0;
    clearButton.disabled = placements.length === 0;
  }

  function undo() {
    if (!history.length) return;
    placements = history.pop();
    nextPlacementId = Math.max(0, ...placements.map((placement) => placement.id)) + 1;
    invalidateSimulation();
    renderPlacements();
    setStatus("Last edit undone.");
    draw();
  }

  function clearPlacements() {
    if (!placements.length) return;
    editAtGenerationZero();
    rememberState();
    placements = [];
    invalidateSimulation();
    renderPlacements();
    setStatus("All pattern cells cleared. Use Undo last to restore them.");
    draw();
  }

  function simulationCancelledError() {
    const error = new Error("simulation cancelled");
    error.name = "SimulationCancelledError";
    return error;
  }

  function terminateSimulationWorker() {
    if (simulationWorker) simulationWorker.terminate();
    simulationWorker = null;
    if (simulationWorkerRequests.size) {
      const error = simulationCancelledError();
      for (const request of simulationWorkerRequests.values()) request.reject(error);
      simulationWorkerRequests.clear();
    }
  }

  function ensureSimulationWorker() {
    if (simulationWorker) return simulationWorker;
    if (typeof Worker === "undefined") return null;
    simulationWorker = new Worker("/static/life-worker.js", { name: "idea-lab-life" });
    simulationWorker.addEventListener("message", (event) => {
      const payload = event.data || {};
      const request = simulationWorkerRequests.get(payload.id);
      if (!request) return;
      simulationWorkerRequests.delete(payload.id);
      if (payload.type === "error") {
        request.reject(new Error(payload.message || "Life worker failed"));
        return;
      }
      request.resolve({
        cells: new Float64Array(payload.cells),
        generations: payload.generations,
        elapsedMs: payload.elapsedMs,
      });
    });
    simulationWorker.addEventListener("error", (event) => {
      const error = new Error(event.message || "Life worker failed");
      const requests = [...simulationWorkerRequests.values()];
      simulationWorkerRequests.clear();
      if (simulationWorker) simulationWorker.terminate();
      simulationWorker = null;
      for (const request of requests) request.reject(error);
    });
    return simulationWorker;
  }

  function runSimulationBatch(cells, generations) {
    const worker = ensureSimulationWorker();
    if (!worker) {
      return new Promise((resolve, reject) => {
        setTimeout(() => {
          const started = performance.now();
          try {
            resolve({
              cells: LIFE_ENGINE.runPackedLife(cells, generations, MAX_LIVE_CELLS),
              generations,
              elapsedMs: performance.now() - started,
            });
          } catch (error) {
            reject(error);
          }
        }, 0);
      });
    }
    const id = ++simulationWorkerSequence;
    const transferable = cells.slice();
    return new Promise((resolve, reject) => {
      simulationWorkerRequests.set(id, { resolve, reject });
      worker.postMessage({
        type: "run",
        id,
        generations,
        maximumLiveCells: MAX_LIVE_CELLS,
        cells: transferable.buffer,
      }, [transferable.buffer]);
    });
  }

  function initialCellSet() {
    const cells = new Set();
    for (const [x, y] of data?.cells || []) cells.add(cellKey(x, y));
    for (const placement of placements) {
      for (const [x, y] of placement.cells) cells.add(cellKey(placement.x + x, placement.y + y));
    }
    return LIFE_ENGINE.normalizedPacked(cells);
  }

  function createSimulation() {
    const initial = initialCellSet();
    return {
      generation: 0,
      initial,
      cells: initial,
      snapshots: new Map([[0, initial]]),
      snapshotBytes: initial.byteLength,
      engineMs: 0,
    };
  }

  function ensureSimulation() {
    if (!sim) sim = createSimulation();
    return sim;
  }

  function saveSnapshot(force = false) {
    if (!sim || (!force && sim.generation % SNAPSHOT_EVERY !== 0)) return;
    const previous = sim.snapshots.get(sim.generation);
    if (previous === sim.cells) return;
    if (previous) sim.snapshotBytes -= previous.byteLength;
    const snapshot = sim.cells.slice();
    sim.snapshots.set(sim.generation, snapshot);
    sim.snapshotBytes += snapshot.byteLength;
    while (
      sim.snapshots.size > MAX_SNAPSHOTS
      || sim.snapshotBytes > MAX_SNAPSHOT_BYTES
    ) {
      const removable = [...sim.snapshots.keys()].find(
        (generation) => generation !== 0 && generation !== sim.generation
      );
      if (removable === undefined) break;
      sim.snapshotBytes -= sim.snapshots.get(removable).byteLength;
      sim.snapshots.delete(removable);
    }
  }

  function applySimulationResult(result) {
    sim.cells = result.cells;
    sim.generation += result.generations;
    sim.engineMs += result.elapsedMs || 0;
    saveSnapshot();
  }

  function targetGeneration() {
    const minimum = data?.generations?.min ?? 0;
    const maximum = data?.generations?.max ?? 10_000;
    const value = Number(targetInput.value);
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      throw new Error(`target generation must be a whole number from ${minimum} to ${maximum}`);
    }
    return value;
  }

  function simulationStepSize() {
    return checkedInteger(
      Number(stepInput.value),
      "step size",
      1,
      MAX_LEVEL_GENERATIONS
    );
  }

  function updateSimulationStepUI() {
    let step;
    try {
      step = simulationStepSize();
    } catch {
      return;
    }
    const formatted = step.toLocaleString("en-US");
    const noun = step === 1 ? "generation" : "generations";
    backButton.textContent = `−${formatted}`;
    backButton.title = `Step back ${formatted} ${noun}`;
    backButton.setAttribute("aria-label", backButton.title);
    forwardButton.textContent = `+${formatted}`;
    forwardButton.title = `Step forward ${formatted} ${noun}`;
    forwardButton.setAttribute("aria-label", forwardButton.title);
  }

  function stepSimulation(direction) {
    try {
      const generation = ensureSimulation().generation;
      const step = simulationStepSize();
      void seekTo(generation + direction * step);
    } catch (error) {
      setStatus(error.message || String(error), true);
    }
  }

  function updateSimulationUI() {
    const generation = sim?.generation || 0;
    const liveCount = sim?.cells?.length ?? initialCellSet().length;
    generationLabel.textContent =
      `gen ${generation.toLocaleString("en-US")} · ${liveCount.toLocaleString("en-US")} live`
      + (simulationBusy ? " · computing" : "");
    playButton.textContent = play.playing ? "❚❚" : "▶";
    updateScoreUI();
  }

  function pauseSimulation() {
    play.playing = false;
    play.acc = 0;
    updateSimulationUI();
  }

  function restoreForTarget(target) {
    ensureSimulation();
    if (target >= sim.generation) return;
    const candidates = [...sim.snapshots.keys()].filter((generation) => generation <= target);
    const generation = candidates.length ? Math.max(...candidates) : 0;
    const cells = sim.snapshots.get(generation) || sim.initial;
    sim.generation = generation;
    sim.cells = cells.slice();
  }

  async function seekTo(target) {
    const maximum = data?.generations?.max ?? 10_000;
    const destination = Math.max(0, Math.min(maximum, target));
    pauseSimulation();
    const token = ++simulationToken;
    terminateSimulationWorker();
    restoreForTarget(destination);
    simulationBusy = sim.generation < destination;
    updateSimulationUI();
    let lastPaint = performance.now();
    try {
      while (sim.generation < destination) {
        const untilSnapshot = SNAPSHOT_EVERY - (sim.generation % SNAPSHOT_EVERY || 0);
        const batch = Math.min(
          WORKER_BATCH_GENERATIONS,
          destination - sim.generation,
          untilSnapshot
        );
        const result = await runSimulationBatch(sim.cells, batch);
        if (token !== simulationToken) throw simulationCancelledError();
        applySimulationResult(result);
        const now = performance.now();
        if (now - lastPaint >= PROGRESS_PAINT_INTERVAL_MS || sim.generation === destination) {
          updateSimulationUI();
          draw();
          lastPaint = now;
        }
      }
      if (token !== simulationToken) return;
      saveSnapshot(true);
      simulationBusy = false;
      updateSimulationUI();
      draw();
      setStatus(`Simulation at generation ${destination.toLocaleString("en-US")}.`);
    } catch (error) {
      if (error?.name === "SimulationCancelledError") return;
      if (token === simulationToken) simulationToken++;
      terminateSimulationWorker();
      simulationBusy = false;
      play.playing = false;
      setStatus(error.message || String(error), true);
      updateSimulationUI();
    }
  }

  function resetSimulation() {
    simulationToken++;
    terminateSimulationWorker();
    simulationBusy = false;
    pauseSimulation();
    sim = createSimulation();
    updateSimulationUI();
    draw();
    setStatus("Simulation reset to generation 0.");
  }

  function schedulePlay() {
    if (play.frame === null) play.frame = requestAnimationFrame(playTick);
  }

  function playTick(timestamp) {
    play.frame = null;
    if (!play.playing || !active) return;
    let destination;
    try {
      destination = targetGeneration();
      ensureSimulation();
      if (sim.generation >= destination) {
        pauseSimulation();
        return;
      }
      const elapsed = Math.min(0.25, Math.max(0, (timestamp - play.lastTime) / 1000));
      play.lastTime = timestamp;
      play.acc = play.speed >= 1000
        ? 100_000
        : Math.min(play.acc + elapsed * play.speed, 1_000);
      if (play.acc < 1) {
        schedulePlay();
        return;
      }
      const requested = play.speed >= 1000
        ? WORKER_BATCH_GENERATIONS
        : Math.max(1, Math.floor(play.acc));
      const untilSnapshot = SNAPSHOT_EVERY - (sim.generation % SNAPSHOT_EVERY || 0);
      const batch = Math.min(requested, destination - sim.generation, untilSnapshot);
      const token = simulationToken;
      simulationBusy = true;
      updateSimulationUI();
      runSimulationBatch(sim.cells, batch).then((result) => {
        if (token !== simulationToken) return;
        applySimulationResult(result);
        play.acc = Math.max(0, play.acc - batch);
        simulationBusy = false;
        updateSimulationUI();
        draw();
        if (sim.generation >= destination) {
          saveSnapshot(true);
          pauseSimulation();
          setStatus(`Simulation at generation ${destination.toLocaleString("en-US")}.`);
        } else if (play.playing && active) {
          schedulePlay();
        }
      }).catch((error) => {
        if (error?.name === "SimulationCancelledError") return;
        if (token === simulationToken) simulationToken++;
        terminateSimulationWorker();
        simulationBusy = false;
        play.playing = false;
        setStatus(error.message || String(error), true);
        updateSimulationUI();
      });
    } catch (error) {
      pauseSimulation();
      setStatus(error.message || String(error), true);
    }
  }

  function togglePlay() {
    try {
      if (simulationBusy && !play.playing) {
        simulationToken++;
        terminateSimulationWorker();
        simulationBusy = false;
        updateSimulationUI();
        setStatus("Simulation paused at the last completed generation.");
        return;
      }
      if (play.playing) {
        play.playing = false;
        play.acc = 0;
        simulationToken++;
        terminateSimulationWorker();
        simulationBusy = false;
        updateSimulationUI();
        return;
      }
      const destination = targetGeneration();
      ensureSimulation();
      if (sim.generation >= destination) {
        if (destination === 0) return;
        sim = createSimulation();
      }
      play.playing = true;
      play.acc = 0;
      play.lastTime = performance.now();
      updateSimulationUI();
      if (play.playing) schedulePlay();
    } catch (error) {
      setStatus(error.message || String(error), true);
    }
  }

  function validatePlacementsForLevel(importedPlacements) {
    const bounds = contestantRect();
    if (!bounds) throw new Error("loaded level has no contestant start area");
    for (const placement of importedPlacements) {
      if (!placementFits(placement.x, placement.y, placement.cells)) {
        throw new Error(`${placement.name} at ${placement.x},${placement.y} falls outside the contestant start area`);
      }
    }
  }

  function buildSavedState() {
    if (simulationBusy) {
      simulationToken++;
      terminateSimulationWorker();
      simulationBusy = false;
    }
    pauseSimulation();
    const currentSimulation = ensureSimulation();
    const state = {
      format: LAB_FORMAT,
      version: LAB_VERSION,
      savedAt: new Date().toISOString(),
      level: Number(levelSelect.value),
      seed: canonicalSeed(seedInput.value),
      placements: placements.map((placement) => ({
        stampId: placement.stampId,
        name: placement.name,
        x: placement.x,
        y: placement.y,
        cells: placement.cells,
      })),
      customStamp: customStamp
        ? { name: customStamp.name, cells: customStamp.cells }
        : null,
      selectedStampId: selectedStamp.id,
      rotation,
      mirrored,
      tool,
      target: targetGeneration(),
      generation: currentSimulation.generation,
      liveCells: currentSimulation.generation > 0
        ? setToCells(currentSimulation.cells)
        : null,
      scoreContext: scoreContext
        ? {
          solution: scoreContext.solution,
          level: scoreContext.level,
          selectedIndex: scoreContext.selectedIndex,
          stats: scoreContext.stats.map((stat) => ({ ...stat })),
        }
        : null,
      bulk: readBulkSettings(),
      playback: {
        speed: play.speed,
        stepSize: simulationStepSize(),
      },
      view: { ...view },
      display: {
        grid: showGrid.checked,
        regions: showRegions.checked,
        secret: showSecret.checked,
      },
    };
    return state;
  }

  function saveJson() {
    try {
      const state = buildSavedState();
      const blob = new Blob([`${JSON.stringify(state, null, 2)}\n`], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `golduck-idea-lab-L${state.level}-${state.seed.slice(0, 8)}.json`;
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setStatus("Idea Lab state saved as JSON.");
    } catch (error) {
      setStatus(error.message || String(error), true);
    }
  }

  async function applyLoadedState(rawState) {
    const state = validateLabState(rawState);
    scoreContext = null;
    const availableLevel = [...levelSelect.options].some(
      (option) => Number(option.value) === state.level
    );
    if (!availableLevel) throw new Error(`level ${state.level} is not available`);
    levelSelect.value = String(state.level);
    seedInput.value = state.seed;
    if (!await loadLevel({ quiet: true })) {
      throw new Error("the saved level and seed could not be loaded");
    }
    validatePlacementsForLevel(state.placements);
    nextPlacementId = 1;
    placements = state.placements.map((placement) => ({
      ...placement,
      id: nextPlacementId++,
      cells: placement.cells.map(([x, y]) => [x, y]),
    }));
    history = [];
    customStamp = state.customStamp;
    selectedStamp = availablePatterns().find((pattern) => pattern.id === state.selectedStampId)
      || PATTERNS[0];
    rotation = state.rotation;
    mirrored = state.mirrored;
    targetInput.value = String(
      Math.min(Math.max(state.target, data.generations.min), data.generations.max)
    );
    if (state.scoreContext) {
      const bounds = contestantRect();
      const area = bounds ? bounds.w * bounds.h : 0;
      if (
        state.scoreContext.stats.length > data.max_runs
        || state.scoreContext.stats.some((stat) =>
          stat.cellCount > area || stat.generations > data.generations.max
        )
      ) {
        throw new Error("saved score context is not valid for this level");
      }
      scoreContext = state.scoreContext;
    }
    showGrid.checked = state.display.grid;
    showRegions.checked = state.display.regions;
    showSecret.checked = state.display.secret;
    bulkEnabled.checked = state.bulk.enabled;
    bulkColumns.value = String(state.bulk.columns);
    bulkRows.value = String(state.bulk.rows);
    bulkGapX.value = String(state.bulk.gapX);
    bulkGapY.value = String(state.bulk.gapY);
    stepInput.value = String(state.playback.stepSize);
    speedSelect.value = String(state.playback.speed);
    play.speed = state.playback.speed;
    updateSimulationStepUI();
    view = state.view;
    clampView();
    sim = createSimulation();
    if (state.generation > data.generations.max) {
      throw new Error(`saved generation exceeds this level's maximum of ${data.generations.max}`);
    }
    if (state.generation > 0) {
      sim.generation = state.generation;
      sim.cells = LIFE_ENGINE.cellsToPacked(state.liveCells);
      const snapshot = sim.cells.slice();
      sim.snapshots.set(state.generation, snapshot);
      sim.snapshotBytes += snapshot.byteLength;
    }
    setTool(state.tool);
    renderPalette();
    renderPlacements();
    updateOrientation();
    updateSimulationUI();
    draw();
    setStatus("Idea Lab state loaded.");
  }

  async function loadJsonFile(file) {
    if (!file) return;
    if (file.size > 25 * 1024 * 1024) {
      setStatus("JSON state files are limited to 25 MB.", true);
      return;
    }
    try {
      const value = JSON.parse(await file.text());
      await applyLoadedState(value);
    } catch (error) {
      setStatus(`Could not load state: ${error.message || String(error)}`, true);
    } finally {
      fileInput.value = "";
    }
  }

  async function loadLevel(options = {}) {
    const sequence = ++loadSequence;
    let seed;
    try {
      seed = canonicalSeed(seedInput.value);
    } catch (error) {
      setStatus(error.message, true);
      return false;
    }
    setStatus("Loading known secret and level regions…");
    try {
      const response = await fetch(
        `/api/level/${encodeURIComponent(levelSelect.value)}?seed=${encodeURIComponent(seed)}`,
        { cache: "no-store" }
      );
      const payload = await response.json();
      if (sequence !== loadSequence) return false;
      if (!response.ok) throw new Error(payload.error || "failed to load level");
      data = payload;
      seedInput.value = payload.seed_hex;
      targetInput.min = String(payload.generations.min);
      targetInput.max = String(payload.generations.max);
      if (!options.quiet) {
        placements = [];
        history = [];
        nextPlacementId = 1;
        scoreContext = null;
      }
      const currentTarget = Number(targetInput.value);
      targetInput.value = String(
        Number.isInteger(currentTarget)
          ? Math.min(Math.max(payload.generations.min, currentTarget), payload.generations.max)
          : Math.min(Math.max(payload.generations.min, 1000), payload.generations.max)
      );
      invalidateSimulation();
      renderPlacements();
      renderSolutionRunOptions();
      fitStart();
      updateSimulationUI();
      if (!options.quiet) setStatus("Level loaded. Stamp or draw pixels inside the orange contestant area.");
      return true;
    } catch (error) {
      if (sequence !== loadSequence) return false;
      setStatus(error.message || String(error), true);
      return false;
    }
  }

  function pointerCell(event) {
    const bounds = canvas.getBoundingClientRect();
    const world = screenToWorld(event.clientX - bounds.left, event.clientY - bounds.top);
    return { x: Math.floor(world.x), y: Math.floor(world.y) };
  }

  function updateHover(event) {
    hoverCell = pointerCell(event);
    hoverPlacement = tool === "remove" ? findPlacementAt(hoverCell.x, hoverCell.y) : null;
  }

  function startPan(event) {
    dragging = true;
    lastPointer = { x: event.clientX, y: event.clientY };
    canvas.classList.add("dragging");
    canvas.setPointerCapture(event.pointerId);
  }

  function endPan(event) {
    dragging = false;
    lastPointer = null;
    canvas.classList.remove("dragging");
    if (event && canvas.hasPointerCapture?.(event.pointerId)) {
      canvas.releasePointerCapture(event.pointerId);
    }
  }

  function useCustomStamp() {
    try {
      const cells = parseRle(document.getElementById("lab-custom-rle").value);
      const rawName = document.getElementById("lab-custom-name").value.trim();
      customStamp = {
        id: "custom",
        name: (rawName || "Custom stamp").slice(0, 60),
        detail: `${cells.length.toLocaleString("en-US")} cells · custom RLE`,
        cells,
      };
      selectedStamp = customStamp;
      rotation = 0;
      mirrored = false;
      renderPalette();
      updateOrientation();
      setTool("stamp");
      setStatus(`${customStamp.name} is ready to stamp.`);
    } catch (error) {
      setStatus(error.message || String(error), true);
    }
  }

  function solutionRunCellCount(run) {
    if (Number.isInteger(run?.cell_count) && run.cell_count >= 0) {
      return run.cell_count;
    }
    return Array.isArray(run?.cells) ? run.cells.length : 0;
  }

  function renderSolutionRunOptions() {
    const previous = solutionRunSelect.value;
    solutionRunSelect.innerHTML = "";
    const level = Number(levelSelect.value);
    const runs = Array.isArray(solutionManifest?.runs) ? solutionManifest.runs : [];
    const matching = [];
    let fallbackRun = 0;
    runs.forEach((run, index) => {
      if (run?.level !== level) return;
      fallbackRun++;
      const runNumber = Number.isInteger(run.level_run) && run.level_run > 0
        ? run.level_run
        : fallbackRun;
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent =
        `Run ${runNumber} · ${Number(run.generations || 0).toLocaleString("en-US")} gen · `
        + `${solutionRunCellCount(run).toLocaleString("en-US")} cells`;
      solutionRunSelect.append(option);
      matching.push(String(index));
    });
    if (matching.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = solutionManifest
        ? `No saved Level ${level} runs`
        : "Choose a solution";
      solutionRunSelect.append(option);
    } else if (matching.includes(previous)) {
      solutionRunSelect.value = previous;
    }
    const available = matching.length > 0;
    solutionRunSelect.disabled = !available;
    loadSolutionButton.disabled = !available;
    if (solutionManifest) {
      const seed = solutionManifest.saved_evaluation?.seed;
      solutionNote.textContent = available
        ? `Latest completed seed ${seed || "unknown"} · loading replaces the current pattern and sets the run target.`
        : `The latest completed evaluation has no run for Level ${level}.`;
    }
  }

  async function loadSolutionManifest() {
    const sequence = ++solutionLoadSequence;
    const name = solutionSelect.value;
    solutionManifest = null;
    solutionRunSelect.disabled = true;
    loadSolutionButton.disabled = true;
    solutionNote.textContent = name
      ? `Loading saved runs for ${name}…`
      : "No solution is selected.";
    renderSolutionRunOptions();
    if (!name) return;
    try {
      const response = await fetch(`/api/solution/${encodeURIComponent(name)}`, {
        cache: "no-store",
      });
      const payload = await response.json();
      if (sequence !== solutionLoadSequence) return;
      if (!response.ok) throw new Error(payload.error || "failed to load solution");
      solutionManifest = payload;
      renderSolutionRunOptions();
      if (!Array.isArray(payload.runs) || payload.runs.length === 0) {
        solutionNote.textContent =
          "No compatible completed evaluation is saved. Run this solution in the Visualizer first.";
      }
    } catch (error) {
      if (sequence !== solutionLoadSequence) return;
      solutionManifest = null;
      renderSolutionRunOptions();
      solutionNote.textContent = error.message || String(error);
    }
  }

  async function loadSolutionChoices(providedPayload = null) {
    solutionSelect.innerHTML = "";
    try {
      let payload = providedPayload;
      if (!payload) {
        const response = await fetch("/api/solutions", { cache: "no-store" });
        payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || "failed to list solutions");
        }
      }
      if (!payload || typeof payload !== "object") {
        throw new Error("failed to list solutions");
      }
      const names = Array.isArray(payload.solutions) ? payload.solutions : [];
      solutionSelect.disabled = false;
      for (const name of names) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        solutionSelect.append(option);
      }
      if (names.length === 0) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "No Wasm solutions found";
        solutionSelect.append(option);
        solutionSelect.disabled = true;
        renderSolutionRunOptions();
        return;
      }
      const visualizerSelection = document.getElementById("solution")?.value;
      const preferred = [visualizerSelection, payload.latest_solution]
        .find((candidate) => names.includes(candidate));
      solutionSelect.value = preferred || names[0];
      await loadSolutionManifest();
    } catch (error) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Solutions unavailable";
      solutionSelect.append(option);
      solutionSelect.disabled = true;
      solutionNote.textContent = error.message || String(error);
      renderSolutionRunOptions();
    }
  }

  function buildSolutionScoreContext(selectedRun) {
    const level = Number(selectedRun.level);
    const levelRuns = (solutionManifest?.runs || []).filter((run) => run?.level === level);
    const selectedIndex = levelRuns.indexOf(selectedRun);
    const bounds = contestantRect();
    if (
      selectedIndex < 0
      || !bounds
      || levelRuns.length === 0
      || levelRuns.length > Number(data?.max_runs || 0)
    ) return null;
    const area = bounds.w * bounds.h;
    const stats = levelRuns.map((run) => ({
      cellCount: solutionRunCellCount(run),
      generations: run.generations,
    }));
    if (stats.some((stat) =>
      !Number.isInteger(stat.cellCount)
      || stat.cellCount < 0
      || stat.cellCount > area
      || !Number.isInteger(stat.generations)
      || stat.generations < 0
      || stat.generations > data.generations.max
    )) return null;
    return {
      solution: solutionManifest.solution,
      level,
      selectedIndex,
      stats,
    };
  }

  async function loadSelectedSolutionRun() {
    const runIndex = Number(solutionRunSelect.value);
    const run = solutionManifest?.runs?.[runIndex];
    if (!run) {
      setStatus("Choose a saved solution run first.", true);
      return;
    }
    loadSolutionButton.disabled = true;
    try {
      const converted = solutionRunToPlacement(run, solutionManifest.solution);
      const savedSeed = canonicalSeed(solutionManifest.saved_evaluation?.seed);
      const availableLevel = [...levelSelect.options].some(
        (option) => Number(option.value) === converted.level
      );
      if (!availableLevel) throw new Error(`level ${converted.level} is not available`);
      levelSelect.value = String(converted.level);
      seedInput.value = savedSeed;
      scoreContext = null;
      if (!await loadLevel({ quiet: true })) {
        throw new Error("the solution's saved level and seed could not be loaded");
      }
      if (
        converted.generations < data.generations.min
        || converted.generations > data.generations.max
      ) {
        throw new Error(
          `run generation ${converted.generations} is outside this level's valid range`
        );
      }
      const imported = converted.placement ? [converted.placement] : [];
      validatePlacementsForLevel(imported);
      placements = imported.map((placement) => ({
        ...placement,
        id: 1,
        cells: placement.cells.map(([x, y]) => [x, y]),
      }));
      history = [];
      nextPlacementId = placements.length + 1;
      targetInput.value = String(converted.generations);
      scoreContext = buildSolutionScoreContext(run);
      invalidateSimulation();
      renderPlacements();
      renderSolutionRunOptions();
      fitStart();
      updateSimulationUI();
      draw();
      const cellCountValue = converted.placement?.cells.length || 0;
      const scoreScope = scoreContext
        ? ` using all ${scoreContext.stats.length} Level ${converted.level} runs for scoring`
        : "";
      setStatus(
        `Loaded ${solutionManifest.solution} Level ${converted.level} run ${converted.levelRun}: `
        + `${cellCountValue.toLocaleString("en-US")} cells, target ${converted.generations.toLocaleString("en-US")}${scoreScope}.`
      );
    } catch (error) {
      setStatus(`Could not load solution run: ${error.message || String(error)}`, true);
    } finally {
      loadSolutionButton.disabled = solutionRunSelect.disabled;
    }
  }

  function bindEvents() {
    document.getElementById("lab-load").addEventListener("click", () => loadLevel());
    document.getElementById("lab-randomize").addEventListener("click", () => {
      seedInput.value = randomSeedHex();
      loadLevel();
    });
    document.getElementById("lab-fit-start").addEventListener("click", fitStart);
    document.getElementById("lab-fit-all").addEventListener("click", fitAll);
    solutionSelect.addEventListener("change", loadSolutionManifest);
    solutionRunSelect.addEventListener("change", () => {
      loadSolutionButton.disabled = !solutionRunSelect.value;
    });
    loadSolutionButton.addEventListener("click", loadSelectedSolutionRun);
    document.getElementById("lab-rotate-left").addEventListener("click", () => rotate(-1));
    document.getElementById("lab-rotate-right").addEventListener("click", () => rotate(1));
    document.getElementById("lab-flip").addEventListener("click", () => {
      mirrored = !mirrored;
      updateOrientation();
    });
    bulkEnabled.addEventListener("change", () => {
      setTool("stamp");
      updateBulkUI();
    });
    for (const input of [bulkColumns, bulkRows, bulkGapX, bulkGapY]) {
      input.addEventListener("input", updateBulkUI);
    }
    for (const [name, button] of Object.entries(toolButtons)) {
      button.addEventListener("click", () => setTool(name));
    }
    document.getElementById("lab-use-custom").addEventListener("click", useCustomStamp);
    undoButton.addEventListener("click", undo);
    clearButton.addEventListener("click", clearPlacements);
    document.getElementById("lab-sim-reset").addEventListener("click", resetSimulation);
    backButton.addEventListener("click", () => stepSimulation(-1));
    forwardButton.addEventListener("click", () => stepSimulation(1));
    playButton.addEventListener("click", togglePlay);
    speedSelect.addEventListener("change", () => {
      play.speed = Number(speedSelect.value);
    });
    stepInput.addEventListener("input", updateSimulationStepUI);
    targetInput.addEventListener("input", updateScoreUI);
    document.getElementById("lab-run-target").addEventListener("click", () => {
      try {
        seekTo(targetGeneration());
      } catch (error) {
        setStatus(error.message || String(error), true);
      }
    });
    for (const toggle of [showGrid, showRegions, showSecret]) {
      toggle.addEventListener("change", draw);
    }
    document.getElementById("lab-save-json").addEventListener("click", saveJson);
    document.getElementById("lab-load-json").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => loadJsonFile(fileInput.files?.[0]));
    seedInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadLevel();
    });
    levelSelect.addEventListener("change", () => loadLevel());

    canvas.addEventListener("pointerdown", (event) => {
      if (!data) return;
      updateHover(event);
      if (event.button === 1 || tool === "pan" || spaceHeld) {
        event.preventDefault();
        startPan(event);
      } else if (event.button === 0 && tool === "stamp") {
        placeStamp(hoverCell.x, hoverCell.y);
      } else if (event.button === 0 && tool === "pixel") {
        event.preventDefault();
        beginPixelStroke(event);
      } else if (event.button === 0 && tool === "remove") {
        removeAt(hoverCell.x, hoverCell.y);
      }
    });
    canvas.addEventListener("pointermove", (event) => {
      if (pixelDrawing && lastPixelCell) {
        updateHover(event);
        drawPixelLine(lastPixelCell, hoverCell);
      } else if (dragging && lastPointer) {
        const dx = event.clientX - lastPointer.x;
        const dy = event.clientY - lastPointer.y;
        view.x -= dx / view.scale;
        view.y -= dy / view.scale;
        lastPointer = { x: event.clientX, y: event.clientY };
        clampView();
      } else {
        updateHover(event);
      }
      draw();
    });
    canvas.addEventListener("pointerup", (event) => {
      if (!finishPixelStroke(event)) endPan(event);
    });
    canvas.addEventListener("pointercancel", (event) => {
      if (!finishPixelStroke(event)) endPan(event);
    });
    canvas.addEventListener("pointerleave", () => {
      if (!dragging) {
        hoverCell = null;
        hoverPlacement = null;
        draw();
      }
    });
    canvas.addEventListener("contextmenu", (event) => event.preventDefault());
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      const bounds = canvas.getBoundingClientRect();
      const x = event.clientX - bounds.left;
      const y = event.clientY - bounds.top;
      const before = screenToWorld(x, y);
      view.scale = Math.min(80, Math.max(minimumScale(), view.scale * (event.deltaY < 0 ? 1.12 : 1 / 1.12)));
      const after = screenToWorld(x, y);
      view.x += before.x - after.x;
      view.y += before.y - after.y;
      clampView();
      draw();
    }, { passive: false });

    document.addEventListener("keydown", (event) => {
      if (!active) return;
      const target = event.target;
      const typing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
      if (event.code === "Space" && !typing) {
        event.preventDefault();
        spaceHeld = true;
        canvas.classList.add("temporary-pan");
        return;
      }
      if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (["w", "a", "s", "d"].includes(key)) {
        event.preventDefault();
        view = panViewByKey(view, key, event.shiftKey);
        clampView();
        draw();
      } else if (key === "r") rotate(event.shiftKey ? -1 : 1);
      else if (key === "f") {
        mirrored = !mirrored;
        updateOrientation();
      } else if (key === "b") {
        bulkEnabled.checked = !bulkEnabled.checked;
        setTool("stamp");
        updateBulkUI();
      } else if (key === "z") undo();
      else if (key === "p") setTool("pixel");
      else if (event.key === "1") setTool("stamp");
      else if (event.key === "2") setTool("remove");
      else if (event.key === "3") setTool("pan");
    });
    document.addEventListener("keyup", (event) => {
      if (event.code === "Space") {
        spaceHeld = false;
        canvas.classList.remove("temporary-pan");
      }
    });

    if (window.ResizeObserver) {
      const observer = new ResizeObserver(resize);
      observer.observe(canvas);
    }
    window.addEventListener("resize", resize);
  }

  async function initialize() {
    if (initialized) return;
    initialized = true;
    bindEvents();
    renderPalette();
    renderPlacements();
    updateOrientation();
    updateSimulationStepUI();
    setTool("stamp");
    resize();
    try {
      const response = await fetch("/api/levels", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "failed to load levels");
      const activeLevels = Array.isArray(payload.active_levels)
        ? payload.active_levels
        : (payload.levels || []).filter((level) => Number(level) >= 3);
      for (const level of activeLevels) {
        const option = document.createElement("option");
        option.value = String(level);
        option.textContent = `Level ${level}`;
        levelSelect.append(option);
      }
      await loadLevel();
      await loadSolutionChoices(pendingSolutionChoices);
    } catch (error) {
      setStatus(error.message || String(error), true);
    }
  }

  window.addEventListener(SOLUTIONS_CHANGED_EVENT, (event) => {
    if (!event.detail || typeof event.detail !== "object") return;
    pendingSolutionChoices = event.detail;
    if (initialized && levelSelect.options.length > 0) {
      void loadSolutionChoices(pendingSolutionChoices);
    }
  });

  window.ideaLab = {
    activate() {
      active = true;
      initialize();
      requestAnimationFrame(resize);
    },
    deactivate() {
      active = false;
      if (simulationBusy) {
        simulationToken++;
        terminateSimulationWorker();
        simulationBusy = false;
      }
      pauseSimulation();
      spaceHeld = false;
      canvas.classList.remove("temporary-pan");
    },
  };
})();
