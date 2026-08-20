package com.learngraph.mobile

import android.app.Application
import com.learngraph.mobile.data.ApiClient
import com.learngraph.mobile.data.AuthStore
import kotlinx.serialization.json.Json

/**
 * 应用级单例容器：AuthStore（DataStore 持久化）+ ApiClient（REST/SSE）。
 */
class LearnGraphApp : Application() {
    lateinit var authStore: AuthStore
        private set
    lateinit var api: ApiClient
        private set

    override fun onCreate() {
        super.onCreate()
        authStore = AuthStore(this)
        api = ApiClient(
            Json {
                ignoreUnknownKeys = true
                explicitNulls = false
            },
        )
    }
}
