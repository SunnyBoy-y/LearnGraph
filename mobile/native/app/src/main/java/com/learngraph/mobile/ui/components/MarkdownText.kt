package com.learngraph.mobile.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle

/**
 * 轻量 Markdown 渲染器（原生版刚需优先）：
 *  - 代码块（```，等宽 + 底色）
 *  - 标题 #、引用 >、列表 -、表格行 |
 *  - 行内：**粗体**、`行内代码`、[链接](url)（可点击）
 * 流式增量更新友好：纯 AnnotatedString 渲染，无 WebView/HTML 开销。
 */
@Composable
fun MarkdownText(
    markdown: String,
    onLinkClick: (String) -> Unit,
    modifier: Modifier = Modifier,
    baseColor: androidx.compose.ui.graphics.Color? = null,
) {
    val blocks = remember(markdown) { splitBlocks(markdown) }
    val codeBg = MaterialTheme.colorScheme.surfaceVariant
    val codeColor = MaterialTheme.colorScheme.onSurfaceVariant
    val titleColor = baseColor ?: MaterialTheme.colorScheme.onSurface

    Column(modifier = modifier) {
        blocks.forEachIndexed { index, block ->
            if (index > 0) {
                val previous = blocks[index - 1]
                val gap = when {
                    block is Block.Line && block.kind in listOf(LineKind.H1, LineKind.H2, LineKind.H3) -> 24.dp
                    previous is Block.Line && previous.kind in listOf(LineKind.H1, LineKind.H2, LineKind.H3) -> 10.dp
                    block is Block.Line && previous is Block.Line && block.kind == LineKind.LIST && previous.kind == LineKind.LIST -> 6.dp
                    else -> 14.4.dp
                }
                Spacer(Modifier.height(gap))
            }
            when (block) {
                is Block.Code -> {
                    Text(
                        text = block.code.trimEnd('\n'),
                        fontFamily = FontFamily.Monospace,
                        fontSize = 13.sp,
                        color = codeColor,
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(10.dp))
                            .background(codeBg)
                            .horizontalScroll(rememberScrollState())
                            .padding(horizontal = 10.dp, vertical = 8.dp),
                    )
                }
                is Block.Line -> renderLine(block, onLinkClick, titleColor, baseColor)
            }
        }
    }
}

private sealed interface Block {
    data class Code(val code: String) : Block
    data class Line(val text: String, val kind: LineKind) : Block
}

private enum class LineKind { NORMAL, H1, H2, H3, QUOTE, LIST, TABLE }

private fun splitBlocks(markdown: String): List<Block> {
    val out = mutableListOf<Block>()
    val lines = markdown.split("\n")
    var i = 0
    while (i < lines.size) {
        val line = lines[i]
        if (line.trimStart().startsWith("```")) {
            val buf = StringBuilder()
            i++
            while (i < lines.size && !lines[i].trimStart().startsWith("```")) {
                buf.append(lines[i]).append('\n')
                i++
            }
            i++ // 跳过闭合 ```
            out.add(Block.Code(buf.toString()))
        } else {
            if (line.isBlank()) { i++; continue }
            val trimmed = line.trimStart()
            val kind = when {
                trimmed.startsWith("### ") -> LineKind.H3
                trimmed.startsWith("## ") -> LineKind.H2
                trimmed.startsWith("# ") -> LineKind.H1
                trimmed.startsWith("> ") -> LineKind.QUOTE
                trimmed.startsWith("- ") || trimmed.startsWith("* ") || trimmed.matches(Regex("^\\d+[.)]\\s+.*")) -> LineKind.LIST
                trimmed.startsWith("|") -> LineKind.TABLE
                else -> LineKind.NORMAL
            }
            var text = when (kind) {
                LineKind.H1 -> trimmed.removePrefix("# ")
                LineKind.H2 -> trimmed.removePrefix("## ")
                LineKind.H3 -> trimmed.removePrefix("### ")
                LineKind.QUOTE -> trimmed.removePrefix("> ")
                LineKind.LIST -> if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) "• ${trimmed.drop(2)}" else trimmed
                else -> trimmed
            }
            i++
            // One Text per Markdown paragraph: source soft breaks must not
            // become unrelated Compose rows with artificial gaps.
            if (kind == LineKind.NORMAL || kind == LineKind.TABLE || kind == LineKind.QUOTE) {
                while (i < lines.size && lines[i].isNotBlank()) {
                    val next = lines[i].trimStart()
                    val belongs = when (kind) {
                        LineKind.TABLE -> next.startsWith("|")
                        LineKind.QUOTE -> next.startsWith("> ")
                        else -> !next.startsWith("```") && !next.startsWith("# ") && !next.startsWith("## ") && !next.startsWith("### ") && !next.startsWith("> ") && !next.startsWith("- ") && !next.startsWith("* ") && !next.startsWith("|") && !next.matches(Regex("^\\d+[.)]\\s+.*"))
                    }
                    if (!belongs) break
                    val addition = if (kind == LineKind.QUOTE) next.removePrefix("> ") else lines[i]
                    val separator = if (kind != LineKind.NORMAL || text.endsWith("  ")) "\n" else " "
                    text = text.trimEnd() + separator + addition
                    i++
                }
            }
            out.add(Block.Line(text, kind))
        }
    }
    return out
}

@Composable
private fun renderLine(
    block: Block.Line,
    onLinkClick: (String) -> Unit,
    titleColor: androidx.compose.ui.graphics.Color,
    baseColor: androidx.compose.ui.graphics.Color? = null,
) {
    if (block.text.isBlank()) {
        Spacer(Modifier.height(4.dp))
        return
    }
    val style = when (block.kind) {
        LineKind.H1 -> MaterialTheme.typography.titleLarge.copy(fontSize = 22.sp, lineHeight = 30.8.sp, fontWeight = FontWeight.Bold, color = titleColor)
        LineKind.H2 -> MaterialTheme.typography.titleMedium.copy(fontSize = 18.sp, lineHeight = 26.1.sp, fontWeight = FontWeight.Bold, color = titleColor)
        LineKind.H3 -> MaterialTheme.typography.titleSmall.copy(fontSize = 16.sp, lineHeight = 24.sp, fontWeight = FontWeight.SemiBold, color = titleColor)
        LineKind.QUOTE -> MaterialTheme.typography.bodyLarge.copy(
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        LineKind.LIST -> MaterialTheme.typography.bodyLarge.copy(fontSize = 16.sp, lineHeight = 28.8.sp)
        LineKind.TABLE -> MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace, fontSize = 14.sp, lineHeight = 22.4.sp)
        LineKind.NORMAL -> MaterialTheme.typography.bodyLarge.copy(fontSize = 16.sp, lineHeight = 28.8.sp)
    }
    val textColor = baseColor ?: style.color
    val linkColor = MaterialTheme.colorScheme.primary
    val annotated = remember(block.text, style, textColor, linkColor, onLinkClick) { buildInline(block.text, style.toSpanStyle().copy(color = textColor), linkColor, onLinkClick) }
    Text(
        text = annotated,
        style = style.copy(color = textColor),
        modifier = if (block.kind == LineKind.QUOTE) Modifier.padding(start = 16.dp) else Modifier,
    )
}

/** 行内解析：`code`、**bold**、[text](url) */
private fun buildInline(
    text: String,
    base: SpanStyle,
    linkColor: androidx.compose.ui.graphics.Color,
    onLinkClick: (String) -> Unit,
): AnnotatedString = buildAnnotatedString {
    pushStyle(base)
    var i = 0
    val n = text.length
    while (i < n) {
        val c = text[i]
        // 行内代码 `...`
        if (c == '`') {
            val end = text.indexOf('`', i + 1)
            if (end > i) {
                withStyle(
                    base + SpanStyle(
                        fontFamily = FontFamily.Monospace,
                        background = androidx.compose.ui.graphics.Color(0x22000000),
                    ),
                ) {
                    append(text.substring(i + 1, end))
                }
                i = end + 1
                continue
            }
        }
        // 粗体 **...**
        if (c == '*' && i + 1 < n && text[i + 1] == '*') {
            val end = text.indexOf("**", i + 2)
            if (end > i) {
                withStyle(base + SpanStyle(fontWeight = FontWeight.Bold)) {
                    append(text.substring(i + 2, end))
                }
                i = end + 2
                continue
            }
        }
        // 链接 [text](url)
        if (c == '[') {
            val close = text.indexOf(']', i + 1)
            if (close > i && close + 1 < n && text[close + 1] == '(') {
                val urlEnd = text.indexOf(')', close + 2)
                if (urlEnd > close) {
                    val label = text.substring(i + 1, close)
                    val url = text.substring(close + 2, urlEnd)
                    withLink(
                        LinkAnnotation.Clickable(
                            tag = url,
                            styles = TextLinkStyles(
                                style = base + SpanStyle(color = linkColor, textDecoration = TextDecoration.Underline),
                            ),
                            linkInteractionListener = { annotation ->
                                onLinkClick(
                                    if (annotation is LinkAnnotation.Clickable) annotation.tag else annotation.toString(),
                                )
                            },
                        ),
                    ) {
                        append(label)
                    }
                    i = urlEnd + 1
                    continue
                }
            }
        }
        append(c)
        i++
    }
    pop()
}
