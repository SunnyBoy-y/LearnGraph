package com.learngraph.mobile.notify

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test

class QuickReplyBodyTest {
    @Test fun `notification reply is valid JSON including multiline Unicode and control characters`() {
        val text = "你好，继续解释\n\"引号\"\\路径\t\u0001"
        assertEquals(text, Json.parseToJsonElement(quickReplyBody(text)).jsonObject["content"]!!.jsonPrimitive.content)
    }
}
