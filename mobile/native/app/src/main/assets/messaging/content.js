// LearnGraph Native Bridge —— content script
// 在页面 document_start 注入 window.LearnGraphNative，接口与旧版
// addJavascriptInterface 的 NativeBridge 保持一致（方法名/参数语义）。
// 差异：同步方法（getInboxItems / getInboxImageDataUrl / consumeShortcutAction）
// 现在返回 Promise（GeckoView 无同步原生通道），前端 native-bridge.ts 已对应异步化。
// 另监听 background 广播的 {type:"eval"} 消息，执行原生注入的 JS
// （登录态同步 / 拍照回调），供 GeckoView 原生侧调用。
(function () {
  if (window.__lgNativeBridgeInstalled) return;
  window.__lgNativeBridgeInstalled = true;

  var seq = 0;

  function call(method, args) {
    var id = ++seq;
    try {
      return browser.runtime.sendMessage({ id: id, method: method, args: args || [] });
    } catch (e) {
      return Promise.resolve(null);
    }
  }

  window.LearnGraphNative = {
    clearAuth: function () { return call("clearAuth"); },
    download: function (url, fileName) { return call("download", [url, fileName]); },
    saveBase64: function (dataUrl, fileName) { return call("saveBase64", [dataUrl, fileName]); },
    getInboxItems: function () { return call("getInboxItems"); },
    clearInboxItem: function (id) { return call("clearInboxItem", [id]); },
    clearInbox: function () { return call("clearInbox"); },
    getInboxImageDataUrl: function (id) { return call("getInboxImageDataUrl", [id]); },
    takePhoto: function () { return call("takePhoto"); },
    consumeShortcutAction: function () { return call("consumeShortcutAction"); },
    haptic: function (intensity) { return call("haptic", [intensity]); },
    replyHaptic: function () { return call("replyHaptic"); },
    startReplyVibration: function () { return call("startReplyVibration"); },
    stopReplyVibration: function () { return call("stopReplyVibration"); },
    stepHaptic: function () { return call("stepHaptic"); },
    celebration: function () { return call("celebration"); },
    chime: function () { return call("chime"); },
    speak: function (text) { return call("speak", [text]); },
    // 登录态回写（原生注入的 JS 在发现 token 不一致时调用）
    __reportToken: function (token, workspaceId) { return call("__reportToken", [token, workspaceId]); }
  };

  // 原生 → 页面 JS 执行（登录态注入 / 拍照回调）
  browser.runtime.onMessage.addListener(function (msg) {
    if (msg && msg.type === "eval") {
      try {
        eval(msg.js);
      } catch (e) {}
    }
    return false;
  });
})();
