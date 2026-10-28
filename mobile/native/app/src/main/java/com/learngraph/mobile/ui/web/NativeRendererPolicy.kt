package com.learngraph.mobile.ui.web

import java.net.URI

/** Dedicated local assets retain the selected server origin; API requests use the ordinary web client. */
object NativeRendererPolicy {
    const val PREFIX = "/__lg_renderer_v1/"

    fun origin(value: String): String? = runCatching {
        val uri = URI(value)
        if (uri.scheme?.lowercase() !in listOf("http", "https") || uri.host.isNullOrBlank() || uri.userInfo != null) null
        else URI(uri.scheme.lowercase(), null, uri.host.lowercase(), uri.port, null, null, null).toString()
    }.getOrNull()

    fun sameOrigin(first: String, second: String): Boolean {
        fun normalized(value: String): String? = origin(value)?.let {
            val uri = URI(it)
            "${uri.scheme}://${uri.host}:${if (uri.port >= 0) uri.port else if (uri.scheme == "https") 443 else 80}"
        }
        return normalized(first)?.let { it == normalized(second) } ?: false
    }

    fun assetPath(url: String, server: String): String? = runCatching {
        if (!sameOrigin(url, server)) return null
        val path = URI(url).path
        if (!path.startsWith(PREFIX)) return null
        val relative = path.removePrefix(PREFIX)
        if (relative.isBlank() || relative.split('/').any { it.isBlank() || it == "." || it == ".." } || relative.contains('\\')) null
        else "renderer/$relative"
    }.getOrNull()

    /**
     * Normal Web navigation must reach the server so deployment updates are visible.
     * The packaged document is only a transport failure fallback.
     */
    fun canUseOfflineApplication(url: String, server: String, workspaceId: String): Boolean =
        isApplicationDocument(url, server, workspaceId) && !hasServedWebShell(url)

    fun canRetryPackagedApplication(url: String, server: String, workspaceId: String): Boolean =
        isApplicationDocument(url, server, workspaceId)

    fun isPackagedApplicationRequest(url: String, server: String): Boolean =
        sameOrigin(url, server) && runCatching { URI(url).path.startsWith(PREFIX) }.getOrDefault(false)

    /** Only revisit the response cache; never replace a broken or offline document here. */
    fun shouldRevalidateOnResume(url: String?, server: String, fallbackActive: Boolean): Boolean = runCatching {
        !fallbackActive && !url.isNullOrBlank() && sameOrigin(url, server)
    }.getOrDefault(false)

    /** A stale packaged shell should preserve local state but not call APIs it did not implement. */
    fun offlineShellCapabilities(workspaceId: String?): Set<String> =
        if (workspaceId.isNullOrBlank()) setOf("auth", "auth-brand", "appearance")
        else setOf("workspace", "sessions", "messages", "composer", "appearance", "downloads")

    /** Only the active workspace can become an internal native navigation destination. */
    fun workspaceDestination(url: String, server: String, workspaceId: String): String? = runCatching {
        val absolute = URI(server.trimEnd('/') + "/").resolve(url)
        if (!sameOrigin(absolute.toString(), server)) return null
        val prefix = "/w/$workspaceId/"
        if (!absolute.path.startsWith(prefix)) return null
        val path = absolute.path.removePrefix(prefix)
        if (path.isBlank() || path.split('/').any { it == ".." || it == "." } || path.contains('\\')) return null
        absolute.rawPath.removePrefix("/w/${URI(null, null, workspaceId, null).rawPath}/") +
            (absolute.rawQuery?.let { "?$it" } ?: "") + (absolute.rawFragment?.let { "#$it" } ?: "")
    }.getOrNull()

    fun scriptJson(json: String): String = json.replace("<", "\\u003c").replace(">", "\\u003e")
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    /** A delayed Web preference save cannot update a different native login or workspace. */
    fun acceptsPreferenceScope(server: String, token: String?, workspaceId: String?,
        requestedOrigin: String, requestedToken: String?, requestedWorkspaceId: String?): Boolean =
        !token.isNullOrBlank() && !workspaceId.isNullOrBlank() && token == requestedToken &&
            workspaceId == requestedWorkspaceId && sameOrigin(server, requestedOrigin)

    fun featureUrl(server: String, workspaceId: String, feature: String): String? = runCatching {
        if (feature.length > 4000 || feature.startsWith('/') || feature.contains('\\') || feature.contains("://")) return null
        val encodedWorkspace = URI(null, null, workspaceId, null).rawPath
        val target = "${origin(server) ?: return null}/w/$encodedWorkspace/$feature"
        if (workspaceDestination(target, server, workspaceId) == null) null else target
    }.getOrNull()

    /** Only a document this WebView loaded from the server may enter its own worker cache. */
    fun markServedWebShell(url: String?, server: String): String? = runCatching {
        if (url.isNullOrBlank() || !sameOrigin(url, server)) null
        else URI(url).let { uri ->
            "${uri.scheme}://${uri.rawAuthority}${uri.rawPath}" +
                (uri.rawQuery?.let { "?$it" } ?: "")
        }
    }.getOrNull()

    fun hasServedWebShell(url: String): Boolean =
        loadedServerShells.any { NativeRendererPolicy.sameOrigin(it, url) && URI(it).rawPath == URI(url).rawPath }

    fun recordServedWebShell(url: String?, server: String) {
        markServedWebShell(url, server)?.let { loadedServerShells.add(it) }
    }

    private val loadedServerShells = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()

    /** SPA route classification for offline fallback; online documents always stay on the server. */
    fun isApplicationDocument(url: String, server: String, workspaceId: String): Boolean = runCatching {
        if (!sameOrigin(url, server)) return false
        val path = URI(url).path
        path in setOf("/", "/login", "/auth/login", "/auth/change-password") ||
            workspaceId.isNotBlank() && (path == "/w/$workspaceId" || workspaceDestination(url, server, workspaceId) != null)
    }.getOrDefault(false)
}
