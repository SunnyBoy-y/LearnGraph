package com.learngraph.mobile.util

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.MediaStore
import android.util.Base64
import androidx.activity.result.ActivityResultLauncher
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.fragment.app.FragmentActivity
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.UUID

/**
 * 拍照即问（系统相机通道）。
 *
 * 网页版「现场拍照」按钮 → LearnGraphNative.takePhoto() → 本工具：
 *  1. 先检查/申请 CAMERA 权限
 *  2. 权限就绪后启动系统相机 App（MediaStore.ACTION_IMAGE_CAPTURE + FileProvider）
 *  3. 拍完压缩为 JPEG data URL，经 window.__lgPhotoCallback 回调网页版
 *
 * 保存 MainActivity 引用（contextRef），launch 不依赖调用方传入的 context 类型。
 */
object PhotoCapture {

    @Volatile
    var webView: android.webkit.WebView? = null
        private set

    @Volatile
    private var contextRef: FragmentActivity? = null
    private var launcher: ActivityResultLauncher<Intent>? = null
    private var permissionLauncher: ActivityResultLauncher<String>? = null
    private var pendingCaptureUri: Uri? = null

    /** 由 MainActivity 在 onCreate 注册一次 */
    fun init(activity: FragmentActivity) {
        contextRef = activity
        if (launcher != null && permissionLauncher != null) return
        launcher = activity.registerForActivityResult(
            androidx.activity.result.contract.ActivityResultContracts.StartActivityForResult(),
        ) { result ->
            val payloadUri = pendingCaptureUri
            if (result.resultCode == Activity.RESULT_OK) {
                deliverPhoto(activity, payloadUri)
            } else {
                deliverPhoto(activity, null)
            }
            pendingCaptureUri = null
        }
        permissionLauncher = activity.registerForActivityResult(
            androidx.activity.result.contract.ActivityResultContracts.RequestPermission(),
        ) { granted ->
            if (granted) startCameraIntent(activity) else deliverPhoto(activity, null)
        }
    }

    fun setWebView(wv: android.webkit.WebView?) {
        webView = wv
    }

    /** 现场拍照入口：先申请相机权限，再跳转系统相机控件 */
    fun launch(context: Context) {
        val activity = contextRef ?: run {
            deliverPhoto(context, null)
            return
        }
        if (launcher == null || permissionLauncher == null) {
            deliverPhoto(context, null)
            return
        }
        val hasCamera =
            ContextCompat.checkSelfPermission(activity, Manifest.permission.CAMERA) ==
                PackageManager.PERMISSION_GRANTED
        if (hasCamera) {
            startCameraIntent(activity)
        } else {
            permissionLauncher?.launch(Manifest.permission.CAMERA)
        }
    }

    /** 直接启动系统相机 App（需已有 CAMERA 权限） */
    private fun startCameraIntent(context: Context) {
        val l = launcher ?: return
        val dir = File(context.cacheDir, "photos").apply { mkdirs() }
        val file = File(dir, "capture_${UUID.randomUUID()}.jpg")
        val uri = FileProvider.getUriForFile(
            context,
            "com.learngraph.mobile.fileprovider",
            file,
        )
        pendingCaptureUri = uri
        try {
            val intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE).apply {
                putExtra(MediaStore.EXTRA_OUTPUT, uri)
                addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            l.launch(intent)
        } catch (_: Exception) {
            deliverPhoto(context, null)
        }
    }

    private fun deliverPhoto(context: Context, uri: Uri?) {
        val wv = webView ?: return
        if (uri == null) {
            wv.evaluateJavascript("try{window.__lgPhotoCallback&&window.__lgPhotoCallback(null)}catch(e){}", null)
            return
        }
        try {
            val bitmap = decodeSampled(context, uri) ?: run {
                wv.evaluateJavascript("try{window.__lgPhotoCallback&&window.__lgPhotoCallback(null)}catch(e){}", null)
                return
            }
            val dataUrl = bitmapToJpegDataUrl(bitmap)
            val escaped = dataUrl.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
            wv.evaluateJavascript(
                "try{window.__lgPhotoCallback&&window.__lgPhotoCallback('$escaped')}catch(e){}",
                null,
            )
        } catch (_: Exception) {
            wv.evaluateJavascript("try{window.__lgPhotoCallback&&window.__lgPhotoCallback(null)}catch(e){}", null)
        }
    }

    private fun decodeSampled(context: Context, uri: Uri): Bitmap? {
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            context.contentResolver.openInputStream(uri)?.use {
                BitmapFactory.decodeStream(it, null, bounds)
            }
            var sample = 1
            val maxDim = 1600
            while (Math.max(bounds.outWidth, bounds.outHeight) / sample > maxDim) {
                sample *= 2
            }
            val opts = BitmapFactory.Options().apply { inSampleSize = sample }
            val bmp = context.contentResolver.openInputStream(uri)?.use {
                BitmapFactory.decodeStream(it, null, opts)
            } ?: return null
            val orientation = try {
                val exif = android.media.ExifInterface(context.contentResolver.openInputStream(uri)!!)
                exif.getAttributeInt(
                    android.media.ExifInterface.TAG_ORIENTATION,
                    android.media.ExifInterface.ORIENTATION_NORMAL,
                )
            } catch (_: Exception) {
                android.media.ExifInterface.ORIENTATION_NORMAL
            }
            val matrix = android.graphics.Matrix()
            when (orientation) {
                android.media.ExifInterface.ORIENTATION_ROTATE_90 -> matrix.postRotate(90f)
                android.media.ExifInterface.ORIENTATION_ROTATE_180 -> matrix.postRotate(180f)
                android.media.ExifInterface.ORIENTATION_ROTATE_270 -> matrix.postRotate(270f)
            }
            val rotated = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, matrix, true)
            if (rotated !== bmp) bmp.recycle()
            rotated
        } catch (_: Exception) {
            null
        }
    }

    private fun bitmapToJpegDataUrl(bitmap: Bitmap): String {
        val bytes = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 82, bytes)
        val base64 = Base64.encodeToString(bytes.toByteArray(), Base64.NO_WRAP)
        return "data:image/jpeg;base64,$base64"
    }
}
