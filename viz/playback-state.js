(function exposeGolduckPlaybackState(root, factory) {
  "use strict";

  const playbackState = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = playbackState;
  } else {
    root.GolduckPlaybackState = playbackState;
  }
})(typeof self !== "undefined" ? self : globalThis, () => {
  "use strict";

  const VIDEO_MIME_TYPES = [
    "video/webm;codecs=vp9",
    "video/webm;codecs=vp8",
    "video/webm",
    "video/mp4;codecs=h264",
    "video/mp4",
  ];

  function isAtFinalGeneration(simulation, pendingGeneration = null) {
    if (!simulation) return false;
    const generation = Number(simulation.gen);
    const limit = Number(simulation.limit);
    if (!Number.isFinite(generation) || !Number.isFinite(limit) || limit < 0) {
      return false;
    }
    return generation >= limit || pendingGeneration === limit;
  }

  function runNavigationPosition(runs, runIndex) {
    if (
      !Array.isArray(runs) ||
      !Number.isInteger(runIndex) ||
      runIndex < 0 ||
      runIndex >= runs.length
    ) {
      return null;
    }
    const selectedLevel = runs[runIndex]?.level;
    let ordinal = 0;
    let total = 0;
    runs.forEach((run, index) => {
      if (run?.level !== selectedLevel) return;
      total++;
      if (index <= runIndex) ordinal++;
    });
    return { ordinal, total };
  }

  function selectVideoMimeType(isTypeSupported) {
    if (typeof isTypeSupported !== "function") return "";
    for (const mimeType of VIDEO_MIME_TYPES) {
      try {
        if (isTypeSupported(mimeType)) return mimeType;
      } catch (_error) {
        // Some browser implementations throw for codecs they do not recognize.
      }
    }
    return "";
  }

  function videoExportFilename({
    solutionName,
    level,
    runNumber,
    generations,
    mimeType,
  }) {
    const stem = String(solutionName || "visualizer")
      .replace(/\.wasm$/i, "")
      .replace(/[^a-z0-9_-]+/gi, "-")
      .replace(/^-+|-+$/g, "") || "visualizer";
    const parts = ["golduck", stem, `l${Number(level) || 0}`];
    if (Number.isInteger(runNumber) && runNumber > 0) {
      parts.push(`run-${runNumber}`);
    }
    parts.push(`${Math.max(0, Number(generations) || 0)}g`, "120gps");
    const extension = String(mimeType || "").includes("mp4") ? "mp4" : "webm";
    return `${parts.join("-")}.${extension}`;
  }

  return {
    isAtFinalGeneration,
    runNavigationPosition,
    selectVideoMimeType,
    videoExportFilename,
  };
});
