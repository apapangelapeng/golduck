(function exposeGolduckTabState(root, factory) {
  "use strict";

  const tabState = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = tabState;
  } else {
    root.GolduckTabState = tabState;
  }
})(typeof self !== "undefined" ? self : globalThis, () => {
  "use strict";

  function urlWithTab(currentUrl, tab) {
    const url = new URL(currentUrl);
    url.searchParams.set("tab", tab);
    return url.toString();
  }

  return { urlWithTab };
});
