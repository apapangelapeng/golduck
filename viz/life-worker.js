"use strict";

importScripts("/static/life-engine.js");

self.addEventListener("message", (event) => {
  const request = event.data || {};
  if (request.type !== "run") return;
  const started = performance.now();
  try {
    const cells = new Float64Array(request.cells);
    const result = self.GolduckLifeEngine.runPackedLife(
      cells,
      request.generations,
      request.maximumLiveCells
    );
    self.postMessage({
      type: "result",
      id: request.id,
      generations: request.generations,
      elapsedMs: performance.now() - started,
      cells: result.buffer,
    }, [result.buffer]);
  } catch (error) {
    self.postMessage({
      type: "error",
      id: request.id,
      message: error?.message || String(error),
    });
  }
});
