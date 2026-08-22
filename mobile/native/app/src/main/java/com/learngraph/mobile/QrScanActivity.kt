package com.learngraph.mobile

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Size
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.common.InputImage

/**
 * 二维码扫码页：扫描 LearnGraph 服务器地址二维码，把结果带回连接页。
 *
 * 使用 CameraX（Preview + ImageAnalysis）+ ML Kit 条码识别；
 * 自动识别 URL（http/https）后立即返回，无需手动操作。
 * 顶部工具条：关闭 ✕ | 提示文字 | 手电筒 💡
 */
class QrScanActivity : ComponentActivity() {

    companion object {
        const val EXTRA_RESULT = "qr_result"
    }

    private var previewView: PreviewView? = null
    private var flashlightOn = false
    private var handled = false
    private var boundCamera: androidx.camera.core.Camera? = null
    private val barcodeScanner = BarcodeScanning.getClient()

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            startCamera()
        } else {
            Toast.makeText(this, "需要相机权限才能扫码", Toast.LENGTH_SHORT).show()
            finish()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        buildUi()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            startCamera()
        } else {
            permissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    @SuppressLint("SetTextI18n")
    private fun buildUi() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(android.graphics.Color.BLACK)
        }

        // 顶部工具条
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
            setPadding(dp(4), dp(6), dp(4), dp(6))
        }
        fun toolButton(symbol: String, desc: String, onClick: () -> Unit) = TextView(this).apply {
            text = symbol
            textSize = 18f
            gravity = android.view.Gravity.CENTER
            setTextColor(android.graphics.Color.WHITE)
            contentDescription = desc
            setPadding(dp(14), dp(8), dp(14), dp(8))
            setOnClickListener { onClick() }
        }
        bar.addView(toolButton("✕", "关闭", { finish() }))
        bar.addView(
            TextView(this).apply {
                text = "扫描 LearnGraph 服务器二维码"
                textSize = 14f
                setTextColor(0xFFB8BDC7.toInt())
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            },
        )
        bar.addView(toolButton("💡", "手电筒", {
            flashlightOn = !flashlightOn
            runCatching {
                boundCamera?.cameraControl?.enableTorch(flashlightOn)
            }
        }))
        root.addView(bar)

        // 预览
        val preview = PreviewView(this)
        previewView = preview
        root.addView(
            preview,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )

        setContentView(root)
    }

    // ------------------------------------------------------------------ //
    // CameraX
    // ------------------------------------------------------------------ //

    private var cameraProvider: ProcessCameraProvider? = null
    private lateinit var imageAnalysis: ImageAnalysis

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener(
            {
                try {
                    val provider = future.get()
                    cameraProvider = provider
                    val preview: Preview = Preview.Builder().build()
                    previewView?.let { preview.setSurfaceProvider(it.surfaceProvider) }
                    imageAnalysis = ImageAnalysis.Builder()
                        .setTargetResolution(Size(1280, 720))
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build()
                    imageAnalysis.setAnalyzer(ContextCompat.getMainExecutor(this)) { proxy ->
                        analyzeFrame(proxy)
                    }
                    provider.unbindAll()
                    boundCamera = provider.bindToLifecycle(
                        this,
                        CameraSelector.DEFAULT_BACK_CAMERA,
                        preview,
                        imageAnalysis,
                    )
                } catch (_: Exception) {
                    Toast.makeText(this, "相机启动失败", Toast.LENGTH_SHORT).show()
                    finish()
                }
            },
            ContextCompat.getMainExecutor(this),
        )
    }

    private fun analyzeFrame(proxy: ImageProxy) {
        if (handled) {
            proxy.close()
            return
        }
        val mediaImage = proxy.image
        if (mediaImage == null) {
            proxy.close()
            return
        }
        val inputImage = InputImage.fromMediaImage(mediaImage, proxy.imageInfo.rotationDegrees)
        barcodeScanner.process(inputImage)
            .addOnSuccessListener { barcodes ->
                for (barcode in barcodes) {
                    val raw = barcode.rawValue ?: continue
                    val url = raw.trim()
                    if (url.startsWith("http://") || url.startsWith("https://")) {
                        handled = true
                        val result = Intent().putExtra(EXTRA_RESULT, url)
                        setResult(RESULT_OK, result)
                        finish()
                        break
                    }
                }
            }
            .addOnCompleteListener { proxy.close() }
    }

    override fun onDestroy() {
        super.onDestroy()
        handled = true
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}
