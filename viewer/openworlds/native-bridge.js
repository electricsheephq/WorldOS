(function () {
  function hasBridge() {
    return Boolean(window.WorldOSNative && typeof window.WorldOSNative.request === "function");
  }

  window.OpenWorldsNative = {
    hasBridge,
    request(type, payload) {
      if (!hasBridge()) {
        return Promise.reject(new Error("OpenWorlds is running without the native WorldOS bridge."));
      }
      return window.WorldOSNative.request(type, payload || {});
    },
  };
})();
