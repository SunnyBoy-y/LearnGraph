package com.learngraph.mobile

import android.app.Application
import com.learngraph.mobile.data.ApiClient
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.data.NativeRepository
import com.learngraph.mobile.data.NativeAppearanceStore
import kotlinx.serialization.json.Json

/**
 * 应用级单例容器：AuthStore（DataStore 持久化）+ ApiClient（REST/SSE）。
 */
class LearnGraphApp : Application() {
    lateinit var authStore: AuthStore
        private set
    lateinit var api: ApiClient
        private set
    lateinit var repository: NativeRepository
        private set
    lateinit var appearanceStore: NativeAppearanceStore
        private set

    override fun onCreate() {
        super.onCreate()
        authStore = AuthStore(this)
        appearanceStore = NativeAppearanceStore(this)
        api = ApiClient(
            Json {
                ignoreUnknownKeys = true
                explicitNulls = false
            },
        )
        repository = NativeRepository(this, api)
        // 提前开启 Cookie 接受（任何 WebView 创建前），配合 flush() 实现登录态持久化
        android.webkit.CookieManager.getInstance().setAcceptCookie(true)
    }
}
