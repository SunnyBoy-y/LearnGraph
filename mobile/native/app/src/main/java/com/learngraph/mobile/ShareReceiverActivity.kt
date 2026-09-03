package com.learngraph.mobile

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import com.learngraph.mobile.data.ShareInbox

/**
 * 分享投喂接收器（Share Target）：
 *
 * 任意 App 通过系统分享（ACTION_SEND / ACTION_SEND_MULTIPLE）选择 LearnGraph
 * 时进入本页：
 *  - 文本（EXTRA_TEXT）→ 存收件箱（网页版自动填对话框）
 *  - 任意文件（EXTRA_STREAM）→ 复制到应用缓存 → 存收件箱（网页版自动上传为附件）
 *  - 多文件 → 逐条复制
 *
 * 不拉起主界面（避免打断用户当前操作），仅 Toast 提示；
 * 下次打开 App 时网页版通过 LearnGraphNative.getInboxItems() 拉取自动消费。
 */
class ShareReceiverActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val action = intent?.action
        var saved = 0

        when (action) {
            Intent.ACTION_SEND -> {
                val text = intent?.getStringExtra(Intent.EXTRA_TEXT)?.trim().orEmpty()
                if (text.isNotBlank()) {
                    ShareInbox.addText(this, text)
                    saved++
                }
                val stream = intent?.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
                if (stream != null && ShareInbox.addFile(this, stream) != null) saved++
            }

            Intent.ACTION_SEND_MULTIPLE -> {
                val uris = intent?.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM) ?: emptyList()
                uris.forEach { uri ->
                    if (ShareInbox.addFile(this, uri) != null) saved++
                }
                val text = intent?.getStringExtra(Intent.EXTRA_TEXT)?.trim().orEmpty()
                if (text.isNotBlank()) {
                    ShareInbox.addText(this, text)
                    saved++
                }
            }
        }

        Toast.makeText(
            this,
            when {
                saved <= 0 -> "没有可保存的内容"
                saved == 1 -> "已接收，打开 LearnGraph 将自动处理"
                else -> "已接收 $saved 条，打开 LearnGraph 将自动处理"
            },
            Toast.LENGTH_SHORT,
        ).show()
        finish()
    }
}
