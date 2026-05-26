(function () {
  function hasBridge() {
    return Boolean(window.ClawDnDNative && typeof window.ClawDnDNative.request === "function");
  }

  window.OpenWorldsNative = {
    hasBridge,
    request(type, payload) {
      if (!hasBridge()) {
        return Promise.reject(new Error("OpenWorlds is running without the native ClawDnD bridge."));
      }
      return window.ClawDnDNative.request(type, payload || {});
    },
  };
})();
