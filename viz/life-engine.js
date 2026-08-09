(function exposeGolduckLifeEngine(root, factory) {
  "use strict";

  const engine = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = engine;
  else root.GolduckLifeEngine = engine;
})(typeof self !== "undefined" ? self : globalThis, () => {
  "use strict";

  // Numeric cell keys stay exact while avoiding a string allocation per cell.
  // The coordinate range matches Idea Lab's validated +/-10,000,000 bounds.
  const CELL_KEY_STRIDE = 2 ** 26;
  const CELL_KEY_OFFSET = 2 ** 25;
  const TILE_SIZE = 64;
  const TILE_KEY_STRIDE = 2 ** 20;
  const TILE_KEY_OFFSET = 2 ** 19;
  const MAX_DENSE_AREA = 8_000_000;
  const DENSE_AREA_PER_CELL = 24;
  const NEIGHBOR_OFFSETS = new Float64Array([
    -CELL_KEY_STRIDE - 1,
    -CELL_KEY_STRIDE,
    -CELL_KEY_STRIDE + 1,
    -1,
    1,
    CELL_KEY_STRIDE - 1,
    CELL_KEY_STRIDE,
    CELL_KEY_STRIDE + 1,
  ]);

  function packCell(x, y) {
    return (x + CELL_KEY_OFFSET) * CELL_KEY_STRIDE + y + CELL_KEY_OFFSET;
  }

  function unpackCell(key) {
    const packedX = Math.floor(key / CELL_KEY_STRIDE);
    const packedY = key - packedX * CELL_KEY_STRIDE;
    return [packedX - CELL_KEY_OFFSET, packedY - CELL_KEY_OFFSET];
  }

  function normalizedPacked(keys) {
    const packed = keys instanceof Float64Array
      ? keys.slice()
      : Float64Array.from(keys || []);
    if (packed.length < 2) return packed;
    packed.sort();
    let write = 1;
    for (let read = 1; read < packed.length; read++) {
      if (packed[read] !== packed[write - 1]) packed[write++] = packed[read];
    }
    return write === packed.length ? packed : packed.slice(0, write);
  }

  function cellsToPacked(cells) {
    const packed = new Float64Array(cells.length);
    for (let index = 0; index < cells.length; index++) {
      packed[index] = packCell(cells[index][0], cells[index][1]);
    }
    return normalizedPacked(packed);
  }

  function packedToCells(packed) {
    const cells = new Array(packed.length);
    for (let index = 0; index < packed.length; index++) {
      cells[index] = unpackCell(packed[index]);
    }
    return cells;
  }

  function packedCellsInRect(input, rect) {
    const packed = input instanceof Float64Array
      ? input
      : Float64Array.from(input || []);
    const left = Number(rect?.x);
    const top = Number(rect?.y);
    const width = Number(rect?.w);
    const height = Number(rect?.h);
    if (
      !Number.isInteger(left)
      || !Number.isInteger(top)
      || !Number.isInteger(width)
      || !Number.isInteger(height)
      || width < 0
      || height < 0
    ) {
      throw new Error("simulation bounds must be an integer rectangle");
    }
    const right = left + width;
    const bottom = top + height;
    let kept = null;
    let count = 0;
    for (let index = 0; index < packed.length; index++) {
      const key = packed[index];
      const packedX = Math.floor(key / CELL_KEY_STRIDE);
      const packedY = key - packedX * CELL_KEY_STRIDE;
      const x = packedX - CELL_KEY_OFFSET;
      const y = packedY - CELL_KEY_OFFSET;
      if (x >= left && x < right && y >= top && y < bottom) {
        if (kept) kept[count] = key;
        count++;
      } else if (!kept) {
        kept = new Float64Array(packed.length);
        kept.set(packed.subarray(0, index));
      }
    }
    return kept ? kept.slice(0, count) : packed;
  }

  function packedBounds(packed) {
    if (!packed.length) return { minX: 0, minY: 0, maxX: -1, maxY: -1, area: 0 };
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (let index = 0; index < packed.length; index++) {
      const key = packed[index];
      const x = Math.floor(key / CELL_KEY_STRIDE);
      const y = key - x * CELL_KEY_STRIDE;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    return {
      minX,
      minY,
      maxX,
      maxY,
      area: (maxX - minX + 1) * (maxY - minY + 1),
    };
  }

  function denseStep(live, bounds, maximumLiveCells) {
    const minX = bounds.minX - 1;
    const minY = bounds.minY - 1;
    const width = bounds.maxX - bounds.minX + 3;
    const height = bounds.maxY - bounds.minY + 3;
    const area = width * height;
    const grid = new Uint8Array(area);

    for (let index = 0; index < live.length; index++) {
      const key = live[index];
      const x = Math.floor(key / CELL_KEY_STRIDE);
      const y = key - x * CELL_KEY_STRIDE;
      const cell = (y - minY) * width + x - minX;
      grid[cell] |= 16;
      grid[cell - width - 1]++;
      grid[cell - width]++;
      grid[cell - width + 1]++;
      grid[cell - 1]++;
      grid[cell + 1]++;
      grid[cell + width - 1]++;
      grid[cell + width]++;
      grid[cell + width + 1]++;
    }

    // A generation can contain at most N survivors plus 8N/3 births.
    const capacity = Math.min(area, live.length * 4 + 16, maximumLiveCells + 1);
    const next = new Float64Array(capacity);
    let count = 0;
    for (let row = 0; row < height; row++) {
      const rowOffset = row * width;
      const packedY = minY + row;
      for (let column = 0; column < width; column++) {
        const value = grid[rowOffset + column];
        const neighbors = value & 15;
        if (neighbors === 3 || (neighbors === 2 && (value & 16))) {
          if (count >= maximumLiveCells) {
            throw new Error(
              `simulation exceeded ${maximumLiveCells.toLocaleString("en-US")} live cells`
            );
          }
          next[count++] = (minX + column) * CELL_KEY_STRIDE + packedY;
        }
      }
    }
    return count === next.length ? next : next.slice(0, count);
  }

  function sparseStep(input, maximumLiveCells) {
    const live = input.slice();
    live.sort();
    const neighbors = new Float64Array(live.length * NEIGHBOR_OFFSETS.length);
    let neighborIndex = 0;
    for (let index = 0; index < live.length; index++) {
      const key = live[index];
      for (let offset = 0; offset < NEIGHBOR_OFFSETS.length; offset++) {
        neighbors[neighborIndex++] = key + NEIGHBOR_OFFSETS[offset];
      }
    }
    neighbors.sort();

    const capacity = Math.min(live.length * 4 + 16, maximumLiveCells + 1);
    const next = new Float64Array(capacity);
    let count = 0;
    let liveIndex = 0;
    for (let index = 0; index < neighbors.length;) {
      const key = neighbors[index];
      let end = index + 1;
      while (end < neighbors.length && neighbors[end] === key) end++;
      while (liveIndex < live.length && live[liveIndex] < key) liveIndex++;
      const neighborCount = end - index;
      if (
        neighborCount === 3
        || (neighborCount === 2 && liveIndex < live.length && live[liveIndex] === key)
      ) {
        if (count >= maximumLiveCells) {
          throw new Error(
            `simulation exceeded ${maximumLiveCells.toLocaleString("en-US")} live cells`
          );
        }
        next[count++] = key;
      }
      index = end;
    }
    return count === next.length ? next : next.slice(0, count);
  }

  function tileKey(tileX, tileY) {
    return (tileX + TILE_KEY_OFFSET) * TILE_KEY_STRIDE + tileY + TILE_KEY_OFFSET;
  }

  function splitComponents(live) {
    const tileByKey = new Map();
    const cellTiles = new Int32Array(live.length);
    const tiles = [];

    for (let index = 0; index < live.length; index++) {
      const key = live[index];
      const packedX = Math.floor(key / CELL_KEY_STRIDE);
      const packedY = key - packedX * CELL_KEY_STRIDE;
      const tileX = Math.floor((packedX - CELL_KEY_OFFSET) / TILE_SIZE);
      const tileY = Math.floor((packedY - CELL_KEY_OFFSET) / TILE_SIZE);
      const keyForTile = tileKey(tileX, tileY);
      let tileIndex = tileByKey.get(keyForTile);
      if (tileIndex === undefined) {
        tileIndex = tiles.length;
        tileByKey.set(keyForTile, tileIndex);
        tiles.push({ x: tileX, y: tileY });
      }
      cellTiles[index] = tileIndex;
    }

    const parents = Int32Array.from(tiles, (_, index) => index);
    function find(index) {
      let root = index;
      while (parents[root] !== root) root = parents[root];
      while (parents[index] !== index) {
        const parent = parents[index];
        parents[index] = root;
        index = parent;
      }
      return root;
    }
    function union(left, right) {
      const leftRoot = find(left);
      const rightRoot = find(right);
      if (leftRoot !== rightRoot) parents[rightRoot] = leftRoot;
    }

    const neighborTiles = [[1, 0], [0, 1], [1, 1], [1, -1]];
    for (let index = 0; index < tiles.length; index++) {
      const tile = tiles[index];
      for (const [deltaX, deltaY] of neighborTiles) {
        const neighbor = tileByKey.get(tileKey(tile.x + deltaX, tile.y + deltaY));
        if (neighbor !== undefined) union(index, neighbor);
      }
    }

    const componentByRoot = new Map();
    const tileComponents = new Int32Array(tiles.length);
    const counts = [];
    for (let index = 0; index < tiles.length; index++) {
      const root = find(index);
      let component = componentByRoot.get(root);
      if (component === undefined) {
        component = counts.length;
        componentByRoot.set(root, component);
        counts.push(0);
      }
      tileComponents[index] = component;
    }
    for (let index = 0; index < cellTiles.length; index++) {
      counts[tileComponents[cellTiles[index]]]++;
    }

    const components = counts.map((count) => new Float64Array(count));
    const offsets = new Uint32Array(counts.length);
    for (let index = 0; index < live.length; index++) {
      const component = tileComponents[cellTiles[index]];
      components[component][offsets[component]++] = live[index];
    }
    return components;
  }

  function stepPackedLife(input, maximumLiveCells = Infinity) {
    const live = input instanceof Float64Array ? input : Float64Array.from(input || []);
    if (!live.length) return new Float64Array(0);
    const limit = Number.isFinite(maximumLiveCells)
      ? Math.max(0, Math.floor(maximumLiveCells))
      : Number.MAX_SAFE_INTEGER;
    const components = splitComponents(live);
    const results = [];
    let total = 0;

    for (const component of components) {
      const bounds = packedBounds(component);
      const paddedArea = (bounds.maxX - bounds.minX + 3) * (bounds.maxY - bounds.minY + 3);
      const dense = paddedArea <= MAX_DENSE_AREA
        && paddedArea <= Math.max(4096, component.length * DENSE_AREA_PER_CELL);
      const result = dense
        ? denseStep(component, bounds, limit - total)
        : sparseStep(component, limit - total);
      total += result.length;
      if (total > limit) {
        throw new Error(`simulation exceeded ${limit.toLocaleString("en-US")} live cells`);
      }
      results.push(result);
    }

    const next = new Float64Array(total);
    let offset = 0;
    for (const result of results) {
      next.set(result, offset);
      offset += result.length;
    }
    return next;
  }

  function stepPackedLifeInRect(input, rect, maximumLiveCells = Infinity) {
    const bounded = packedCellsInRect(input, rect);
    return packedCellsInRect(
      stepPackedLife(bounded, maximumLiveCells),
      rect
    );
  }

  function runPackedLife(input, generations, maximumLiveCells = Infinity) {
    if (!Number.isInteger(generations) || generations < 0) {
      throw new Error("generations must be a non-negative integer");
    }
    let live = input instanceof Float64Array ? input : Float64Array.from(input || []);
    for (let generation = 0; generation < generations; generation++) {
      live = stepPackedLife(live, maximumLiveCells);
    }
    return live;
  }

  return {
    CELL_KEY_STRIDE,
    CELL_KEY_OFFSET,
    packCell,
    unpackCell,
    normalizedPacked,
    cellsToPacked,
    packedToCells,
    packedCellsInRect,
    packedBounds,
    stepPackedLife,
    stepPackedLifeInRect,
    runPackedLife,
  };
});
