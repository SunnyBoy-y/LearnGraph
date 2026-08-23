package com.learngraph.mobile.ui.web

import android.content.Context
import org.json.JSONObject
import org.mozilla.geckoview.GeckoResult
import org.mozilla.geckoview.GeckoRuntime
import org.mozilla.geckoview.GeckoRuntimeSettings
import org.mozilla.geckoview.WebExtension

/**
 * GeckoView 内嵌内核全局单例（v0.14.0）。
 *
 *  - 整个应用共享一个 GeckoRuntime（内核进程只起一次，Cookie/localStorage
 *    按 runtime 持久化，跨会话保持登录态）
 *  - 加载内置 WebExtension（assets/messaging/），向页面注入
 *    window.LearnGraphNative 桥（GeckoView 没有 addJavascriptInterface，
 *    原生双向通信必须走 WebExtension 消息通道）
 *  - 原生 → 页面 JS 执行（登录态注入/拍照回调）：经 WebExtension Port 广播
 *    {type:"eval", js}，由 content script 执行
 *
 * 注意：GeckoView 首次创建内核较慢（秒级），页面加载会略慢于系统 WebView，
 * 属正常现象；换来的是内核版本完全由 APK 决定，不再受手机 ROM 的 WebView 影响。
 */
object GeckoRuntimeHolder {

    private const val EXTENSION_PATH = "resource://android/assets/messaging/"
    private const val EXTENSION_ID = "messaging@learngraph"
    private const val NATIVE_APP = "learngraph"

    lateinit var runtime: GeckoRuntime
        private set

    /** 当前注入的桥处理器（WebAppScreen 注册），处理 content script 发来的消息 */
    @Volatile
    var bridgeHandler: BridgeMessageHandler? = null

    /** 到 WebExtension background 的连接 Port（原生 → 页面 JS 用） */
    @Volatile
    private var bridgePort: WebExtension.Port? = null

    private var extensionLoad: GeckoResult<WebExtension?>? = null

    fun init(context: Context) {
        if (::runtime.isInitialized) return
        runtime = GeckoRuntime.create(
            context.applicationContext,
            GeckoRuntimeSettings.Builder()
                .remoteDebuggingEnabled(false)
                .build(),
        )
    }

    /**
     * 确保 WebExtension 已加载并注册消息委托。可多次调用（幂等）。
     * 返回 GeckoResult，完成后新打开的页面导航即会注入 content script。
     */
    fun ensureExtension(): GeckoResult<WebExtension?> {
        extensionLoad?.let { return it }
        val result = runtime.webExtensionController
            .ensureBuiltIn(EXTENSION_PATH, EXTENSION_ID)
            .map { ext ->
                ext?.setMessageDelegate(
                    object : WebExtension.MessageDelegate {
                        // extension 侧 connectNative("learngraph") → 建立 Port
                        override fun onConnect(port: WebExtension.Port) {
                            bridgePort = port
                            port.setDelegate(object : WebExtension.PortDelegate {
                                override fun onPortMessage(message: Any, port: WebExtension.Port) {
                                    val raw = (message as? String)
                                        ?: (message as? JSONObject)?.toString()
                                        ?: return
                                    val response = bridgeHandler?.handle(raw)
                                    if (response != null) {
                                        response.accept { resp ->
                                            val payload = resp as? String ?: return@accept
                                            runCatching { port.postMessage(JSONObject(payload)) }
                                        }
                                    }
                                }
                            })
                        }

                        // sendNativeMessage 无连接消息兜底
                        override fun onMessage(
                            nativeApp: String,
                            message: Any,
                            sender: WebExtension.MessageSender,
                        ): GeckoResult<Any>? {
                            val raw = (message as? String)
                                ?: (message as? JSONObject)?.toString()
                                ?: return null
                            return bridgeHandler?.handle(raw)
                        }
                    },
                    NATIVE_APP,
                )
                ext
            }
        extensionLoad = result
        return result
    }

    /** 原生 → 页面执行 JS（登录态注入 / 拍照回调），经 WebExtension Port 广播 */
    fun evalJs(js: String) {
        val port = bridgePort ?: return
        runCatching {
            port.postMessage(JSONObject().put("type", "eval").put("js", js))
        }
    }
}

/** 网页 → 原生桥消息处理器（WebAppScreen 实现，分发到具体功能） */
interface BridgeMessageHandler {
    /**
     * 处理一条来自页面 content script 的消息（JSON：{id, method, args}），
     * 返回响应 JSON（{id, result}）的 GeckoResult；null 表示不响应。
     */
    fun handle(messageJson: String): GeckoResult<Any>?
}
