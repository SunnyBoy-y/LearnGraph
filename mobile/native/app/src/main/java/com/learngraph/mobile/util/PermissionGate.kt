package com.learngraph.mobile.util

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import android.widget.Toast
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import java.util.WeakHashMap

/**
 * 运行时权限统一闸门（v0.16.0）。
 *
 * 存在的理由 —— 三条都是实测踩出来的坑：
 *
 *  1. **同一时刻只能有一个在途申请。**
 *     `Activity.requestPermissions` 在框架里是 final 方法，且带「已有在途申请」守卫：
 *     第二次调用会被系统直接丢弃，并立刻回调一个空结果（系统日志
 *     "Can request only one set of permissions at a time"）。旧代码在 onCreate 里
 *     连着发「通知权限」和「麦克风权限」两个申请，后发的麦克风就是被丢掉的那个
 *     —— 于是网页里 `getUserMedia` 永远拿不到系统录音权，永远 "Permission denied"。
 *     → 本闸门把所有申请串成一条队列，任何时刻只有一个在途。
 *
 *  2. **Android 11+（API 30）起系统会停止弹窗。**
 *     同一权限被拒两次后，`requestPermissions` 静默返回 DENIED，不再显示任何界面。
 *     若不做兜底，用户随手一拒 = 该功能永久失效，App 里没有任何自救入口。
 *     → 本闸门把「系统已不再询问」判定出来，弹原生说明并跳应用详情页。
 *
 *  3. **必须能反复申请。**
 *     不缓存「拒绝过」结论、不写死状态；每次调用都真实走一遍
 *     `checkSelfPermission → 系统申请 → 判定`。用户中途去设置里开了权限，
 *     回来再点一次就生效（无需重启 App）。
 *
 * 支持 Android 11+ 的「仅本次允许」：一次性授权在进程结束后自动失效，
 * 下一次调用会重新走完整申请流程，与本闸门天然一致。
 *
 * 线程约定：`request` 与所有 `onResult` 回调都在主线程。
 */
object PermissionGate {

    /** 一次权限申请的结果。 */
    data class Result(
        /** 最终是否已授权。 */
        val granted: Boolean,
        /**
         * 系统已不再弹窗询问（用户拒绝过且勾了不再询问 / Android 11+ 连拒两次），
         * 只能由用户去系统设置页手动开启。此时 App 已弹出引导。
         */
        val needsSystemSettings: Boolean,
    )

    private const val PREFS = "lg_permissions"
    private const val TAG = "PermissionGate"

    private class Pending(
        val permission: String,
        /** 本次申请**之前**是否已经问过（用于区分「首次拒绝」与「系统不再询问」）。 */
        val hadAskedBefore: Boolean,
        val onResult: (Result) -> Unit,
    )

    /** 每个 Activity 各注册一份 launcher（Activity 销毁后随弱引用一起回收）。 */
    private val owners = WeakHashMap<FragmentActivity, ActivityResultLauncher<String>>()

    /** 当前承接申请的 Activity（后注册/后 resume 的胜出）。 */
    private var current: Pair<FragmentActivity, ActivityResultLauncher<String>>? = null

    private val queue = ArrayDeque<Pending>()
    private var inFlight: Pending? = null
    private var guidanceDialog: AlertDialog? = null

    /**
     * 在 Activity.onCreate 里调用（**必须早于 onStart**，否则 registerForActivityResult
     * 会抛 IllegalStateException）。重复调用是幂等的。
     */
    fun install(activity: FragmentActivity) {
        if (!owners.containsKey(activity)) {
            owners[activity] = activity.registerForActivityResult(
                ActivityResultContracts.RequestPermission(),
            ) { granted -> onSystemResult(granted) }
        }
        activate(activity)
    }

    /** 在 Activity.onResume 里调用：把申请路由切到当前可见的 Activity。 */
    fun activate(activity: FragmentActivity) {
        val launcher = owners[activity] ?: return
        current = activity to launcher
    }

    /** 只读检查，不发起申请。 */
    fun isGranted(context: Context, permission: String): Boolean =
        ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED

    /**
     * 申请权限。已授权时**同步**回调 `granted=true`；否则排队串行申请。
     *
     * @param onResult 结果回调（主线程）。缺省为空实现，用于「我只想确保它被问过」的场景。
     */
    fun request(permission: String, onResult: (Result) -> Unit = {}) {
        val activity = current?.first
        if (activity == null) {
            // 没有可承接申请的 Activity（理论上只在进程伪存活期出现）：明确失败，不静默挂起。
            Log.w(TAG, "没有可用的 Activity，权限申请放弃：$permission")
            onResult(Result(granted = false, needsSystemSettings = false))
            return
        }
        if (isGranted(activity, permission)) {
            onResult(Result(granted = true, needsSystemSettings = false))
            return
        }
        queue.addLast(
            Pending(
                permission = permission,
                hadAskedBefore = wasAsked(activity, permission),
                onResult = onResult,
            ),
        )
        drain()
    }

    // ------------------------------------------------------------------ //
    // 队列
    // ------------------------------------------------------------------ //

    private fun drain() {
        if (inFlight != null) return
        val (activity, launcher) = current ?: return
        val pending = queue.removeFirstOrNull() ?: return

        if (isGranted(activity, pending.permission)) {
            pending.onResult(Result(granted = true, needsSystemSettings = false))
            drain()
            return
        }

        markAsked(activity, pending.permission)
        inFlight = pending
        val launched = try {
            launcher.launch(pending.permission)
            true
        } catch (error: Exception) {
            Log.w(TAG, "权限申请启动失败：${pending.permission}", error)
            false
        }
        if (!launched) {
            inFlight = null
            pending.onResult(Result(granted = false, needsSystemSettings = false))
            drain()
        }
    }

    private fun onSystemResult(granted: Boolean) {
        val pending = inFlight ?: return
        inFlight = null
        val activity = current?.first

        // 「系统不再询问」的判据：拒绝 + 系统明确说不再展示理由。
        // 再叠加 hadAskedBefore，避免把某些 ROM 上的「首次拒绝」误判成永久拒绝。
        val needsSettings = !granted &&
            pending.hadAskedBefore &&
            activity != null &&
            !activity.shouldShowRequestPermissionRationale(pending.permission)

        if (needsSettings && activity != null) {
            showSettingsGuidance(activity, pending.permission)
        }
        pending.onResult(Result(granted = granted, needsSystemSettings = needsSettings))
        drain()
    }

    // ------------------------------------------------------------------ //
    // 「系统不再弹窗」的兜底
    // ------------------------------------------------------------------ //

    private fun showSettingsGuidance(activity: FragmentActivity, permission: String) {
        if (activity.isFinishing) return
        if (Build.VERSION.SDK_INT >= 17 && activity.isDestroyed) return
        val existing = guidanceDialog
        if (existing != null && existing.isShowing) return

        val label = labelOf(permission)
        // 平台 AlertDialog：本模块主题是 android:Theme.Material.*（非 AppCompat 主题），
        // 用 androidx 版本在部分 Activity 上会因主题校验抛异常。
        guidanceDialog = AlertDialog.Builder(activity)
            .setTitle("系统已不再弹出「$label」授权")
            .setMessage(
                "你之前拒绝过「$label」权限，系统不会再弹窗询问。\n\n" +
                    "请到「设置 → 应用 → LearnGraph → 权限」把「$label」改为允许，" +
                    "然后回到 App 再点一次即可，无需重启。",
            )
            .setPositiveButton("去设置") { _, _ -> openAppSettings(activity) }
            .setNegativeButton("取消", null)
            .setOnDismissListener { guidanceDialog = null }
            .show()
    }

    private fun openAppSettings(activity: Activity) {
        try {
            activity.startActivity(
                Intent(
                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:${activity.packageName}"),
                ),
            )
        } catch (error: Exception) {
            Log.w(TAG, "无法打开应用详情页", error)
            Toast.makeText(activity, "请手动到系统设置里开启该权限", Toast.LENGTH_LONG).show()
        }
    }

    private fun labelOf(permission: String): String = when (permission) {
        Manifest.permission.RECORD_AUDIO -> "麦克风"
        Manifest.permission.POST_NOTIFICATIONS -> "通知"
        Manifest.permission.CAMERA -> "相机"
        else -> "该权限"
    }

    // ------------------------------------------------------------------ //
    // 「是否问过」持久化：只用于区分首次拒绝与永久拒绝，绝不用于跳过申请
    // ------------------------------------------------------------------ //

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun wasAsked(context: Context, permission: String): Boolean =
        prefs(context).getBoolean(permission, false)

    private fun markAsked(context: Context, permission: String) {
        prefs(context).edit().putBoolean(permission, true).apply()
    }
}
