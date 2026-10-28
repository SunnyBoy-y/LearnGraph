package com.learngraph.mobile.ui.web

import android.content.Context
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.data.NativeAppearanceStore
import com.learngraph.mobile.LearnGraphApp
import org.json.JSONObject
import java.io.ByteArrayInputStream

/** Both entry points share one asset graph; requests keep the selected server's ordinary origin. */
object NativeWebAssets {
    fun response(
        context: Context,
        request: WebResourceRequest,
        server: String,
        auth: AuthStore.AuthState,
        offlineDocument: Boolean = false,
    ): WebResourceResponse? {
        if (request.method != "GET") return null
        val url = request.url.toString()
        val asset = NativeRendererPolicy.assetPath(url, server)
        if (asset != null) return runCatching {
            WebResourceResponse(mime(asset), "UTF-8", context.assets.open(asset))
        }.getOrElse { missing() }
        if (!offlineDocument || !request.isForMainFrame ||
            !NativeRendererPolicy.canUseOfflineApplication(url, server, auth.workspaceId.orEmpty())) return null
        return runCatching {
            val identity = JSONObject().apply {
                put("learngraph.access_token", auth.token ?: JSONObject.NULL)
                put("learngraph.workspace_id", auth.workspaceId ?: JSONObject.NULL)
                put("learngraph.device_id", auth.deviceId)
                put("learngraph.username", auth.username ?: JSONObject.NULL)
                put("learngraph.user_id", auth.userId ?: JSONObject.NULL)
                put("learngraph.display_name", auth.displayName ?: JSONObject.NULL)
                put("learngraph.session_id", auth.sessionId ?: JSONObject.NULL)
                put("learngraph.avatar_url", JSONObject.NULL)
                put("learngraph.must_change_password", JSONObject.NULL)
                (context.applicationContext as? LearnGraphApp)?.let { app ->
                    put("lg-theme", app.appearanceStore.observe(NativeAppearanceStore.scope(auth)).value)
                }
            }
            val bootstrap = """<script>(function(){if(window.top!==window)return;var values=${NativeRendererPolicy.scriptJson(identity.toString())};Object.keys(values).forEach(function(key){var value=values[key];if(value===null)localStorage.removeItem(key);else localStorage.setItem(key,String(value));});document.documentElement.classList.add('is-native-app');})();</script>"""
            // The fallback may be older than the remote server. Record which routes
            // it has actually implemented before any remote API is called.
            val capabilityValue = NativeRendererPolicy.scriptJson(
                JSONObject.quote(
                    auth.workspaceId.orEmpty() + "\n" + NativeRendererPolicy.offlineShellCapabilities(auth.workspaceId).joinToString(","),
                ),
            )
            val capabilities = """<script>document.documentElement.setAttribute('data-lg-offline-shell','true');window.dispatchEvent(new CustomEvent('learngraph:offline-capabilities',{detail:$capabilityValue}));</script>"""
            val html = context.assets.open("renderer/native-app.html").bufferedReader().use { it.readText() }
            check(html.contains("<!--native-bootstrap-->"))
            WebResourceResponse("text/html", "UTF-8", 200, "OK", mapOf("Cache-Control" to "no-store", "X-Content-Type-Options" to "nosniff"),
                ByteArrayInputStream(html.replace("<!--native-bootstrap-->", bootstrap + capabilities).toByteArray(Charsets.UTF_8)))
        }.getOrElse { missing() }
    }

    private fun missing() = WebResourceResponse("text/plain", "UTF-8", 404, "Not Found", emptyMap(), ByteArrayInputStream(byteArrayOf()))
    private fun mime(path: String) = when (path.substringAfterLast('.').lowercase()) {
        "js", "mjs" -> "text/javascript"; "css" -> "text/css"; "html" -> "text/html"
        "woff2" -> "font/woff2"; "woff" -> "font/woff"; "ttf" -> "font/ttf"
        "wasm" -> "application/wasm"; "svg" -> "image/svg+xml"; "png" -> "image/png"
        "jpg", "jpeg" -> "image/jpeg"; "webp" -> "image/webp"; "json" -> "application/json"
        else -> "application/octet-stream"
    }
}
