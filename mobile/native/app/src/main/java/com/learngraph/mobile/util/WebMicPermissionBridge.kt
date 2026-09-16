package com.learngraph.mobile.util

import android.Manifest
import android.net.Uri
import android.util.Log
import android.webkit.PermissionRequest
import androidx.fragment.app.FragmentActivity

/**
 * 把 WebView 的麦克风申请（`getUserMedia({audio})`）与系统运行时权限串起来的桥。
 *
 * **唯一不变量：只有系统真的把 `RECORD_AUDIO` 授予了本 App，才对网页 `grant()`。**
 *
 * 为什么不能直接 grant —— 旧代码（v0.15.0）在这里是无条件放行，结果网页里
 * 「只要涉及到麦克风就 permission denied」：
 *
 *  - `PermissionRequest.grant()` 只表示「这个网页被允许使用麦克风」，它**不代表**
 *    本 App 进程持有系统录音权限。系统没授权时 chromium 建不出音频输入流，
 *    `getUserMedia` 会 reject 成 `NotAllowedError`，其 `message` 字面就是
 *    "Permission denied" —— 被前端原样显示出来。
 *  - 正确顺序是先拿到系统授权，再放行 WebView 的 request；两者缺一不可。
 *
 * 生命周期约定：
 *  - 同一时刻只挂一个在途 request；新请求到来时旧的立刻 deny，避免网页重复调用
 *    `getUserMedia` 时堆出一串永远不落地的 promise。
 *  - 网页跳转时 WebView 回调 `onPermissionRequestCanceled`，必须清引用 —— 对已取消的
 *    request 调 `grant()`/`deny()` 会抛异常。
 *  - `grant()`/`deny()` 必须在主线程调用（本类所有入口都在主线程）。
 */
class WebMicPermissionBridge(
    /** 当前可见的 Activity；返回 null 时一律拒绝，不静默挂起。 */
    private val activity: () -> FragmentActivity?,
    /**
     * 允许使用麦克风的来源（懒解析，因为服务器地址可能被用户在连接页改写）。
     * 通常传「配置的服务器地址」+「WebView 当前顶层页面地址」两项。
     */
    private val trustedOrigins: () -> List<Uri>,
) {

    companion object {
        private const val TAG = "WebMicPermission"
    }

    private var pending: PermissionRequest? = null

    fun onPermissionRequest(request: PermissionRequest) {
        // 只放行纯音频捕获：同时申请摄像头的一律拒绝（隐私最小化；拍照走
        // PhotoCapture 的原生通道，不经过网页）。
        if (!isAudioOnly(request)) {
            Log.i(TAG, "拒绝非纯音频捕获申请：${request.resources.joinToString()}")
            deny(request)
            return
        }
        if (!isTrustedOrigin(request)) {
            Log.w(TAG, "拒绝非本服务器的麦克风申请：origin=${request.origin}")
            deny(request)
            return
        }
        val host = activity()
        if (host == null) {
            deny(request)
            return
        }
        // 系统权限已到手（含 Android 11+ 的「仅本次允许」）→ 直接放行网页侧。
        if (PermissionGate.isGranted(host, Manifest.permission.RECORD_AUDIO)) {
            grant(request)
            return
        }

        // 系统权限还没有：把网页的 request 挂住，先解决系统授权。
        // 挂起期间网页的 getUserMedia 保持 pending，用户授权后立即放行，
        // 不会出现「先失败再让用户重试」的割裂体验。
        pending?.let { runCatching { it.deny() } }
        pending = request
        PermissionGate.request(Manifest.permission.RECORD_AUDIO) { result ->
            // 回调期间页面若已跳转，pending 会被 onPermissionRequestCanceled 清掉。
            val held = pending ?: return@request
            pending = null
            if (result.granted) grant(held) else deny(held)
        }
    }

    /** 网页跳转 / 重复申请时 WebView 会取消挂起的申请，必须同步清引用。 */
    fun onPermissionRequestCanceled(request: PermissionRequest) {
        if (pending === request) pending = null
    }

    private fun isAudioOnly(request: PermissionRequest): Boolean {
        val resources = request.resources
        return resources.isNotEmpty() &&
            resources.all { it == PermissionRequest.RESOURCE_AUDIO_CAPTURE }
    }

    private fun isTrustedOrigin(request: PermissionRequest): Boolean {
        val origin = request.origin ?: return false
        val originHost = origin.host ?: return false
        return trustedOrigins().any { trusted ->
            val host = trusted.host ?: return@any false
            host.equals(originHost, ignoreCase = true) &&
                effectivePort(origin) == effectivePort(trusted)
        }
    }

    private fun effectivePort(uri: Uri): Int {
        if (uri.port != -1) return uri.port
        return when (uri.scheme?.lowercase()) {
            "https", "wss" -> 443
            "http", "ws" -> 80
            else -> -1
        }
    }

    private fun grant(request: PermissionRequest) {
        try {
            request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
        } catch (error: Exception) {
            Log.w(TAG, "grant 失败（request 可能已被取消）", error)
        }
    }

    private fun deny(request: PermissionRequest) {
        try {
            request.deny()
        } catch (error: Exception) {
            Log.w(TAG, "deny 失败（request 可能已被取消）", error)
        }
    }
}

/** 把 URL/地址解析成可比较 host+port 的 Uri；不可解析或无 host 时返回 null。 */
fun parseTrustedOrigin(value: String?): Uri? {
    val raw = value?.trim().orEmpty()
    if (raw.isEmpty()) return null
    return runCatching { Uri.parse(raw) }.getOrNull()?.takeIf { !it.host.isNullOrBlank() }
}
