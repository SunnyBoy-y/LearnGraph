// LearnGraph Native Bridge —— background script
// 职责：content script 与原生壳之间的消息中转。
// content → background(runtime.onMessage) → connectNative port → 原生
// 原生响应 → port.onMessage → background 按 id 路由 → content 的 sendMessage Promise
// 原生主动执行 JS → port.onMessage({type:"eval"}) → runtime.sendMessage 广播给 content scripts
(function () {
  var port = null;
  var pending = Object.create(null); // id -> sendResponse

  function ensurePort() {
    if (port) return true;
    try {
      port = browser.runtime.connectNative("learngraph");
    } catch (e) {
      return false;
    }
    port.onMessage.addListener(function (resp) {
      // 原生主动执行 JS（登录态注入 / 拍照回调）
      if (resp && typeof resp === "object" && resp.type === "eval") {
        browser.runtime.sendMessage({ type: "eval", js: resp.js }).catch(function () {});
        return;
      }
      // 原生响应 {id, result}，路由回对应的 content 调用方
      if (resp && typeof resp === "object" && resp.id != null) {
        var resolve = pending[String(resp.id)];
        if (resolve) {
          pending[String(resp.id)] = null;
          resolve(resp.result);
        }
      } else if (typeof resp === "string") {
        try {
          var parsed = JSON.parse(resp);
          if (parsed && parsed.id != null) {
            var r = pending[String(parsed.id)];
            if (r) {
              pending[String(parsed.id)] = null;
              r(parsed.result);
            }
          }
        } catch (e) {}
      }
    });
    return true;
  }

  browser.runtime.onMessage.addListener(function (message, sender, sendResponse) {
    if (!message || typeof message !== "object" || !message.method) {
      return false;
    }
    if (!ensurePort()) {
      sendResponse({ id: message.id, result: null });
      return false;
    }
    var id = String(message.id);
    pending[id] = sendResponse;
    try {
      port.postMessage(JSON.stringify(message));
    } catch (e) {
      if (pending[id]) { pending[id] = null; sendResponse({ id: message.id, result: null }); }
    }
    return true; // 异步 sendResponse
  });
})();
