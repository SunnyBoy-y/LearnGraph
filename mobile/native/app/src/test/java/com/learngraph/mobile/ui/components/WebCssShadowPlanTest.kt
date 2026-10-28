package com.learngraph.mobile.ui.components

import java.io.File
import org.junit.Assert.*
import org.junit.Test

class WebCssShadowPlanTest {
    @Test fun cssBlur42BecomesSigma21BeforeAndroidLegacyMaskConversion() {
        val plan = WebCssShadowPlan.create(960, 606, 3f, 14f, 42f, 16f)
        assertEquals(63f, plan.sigmaPx, .0001f)
        assertEquals(63f, plan.maskRadius * .57735f + .5f, .0001f)
        assertEquals(48, plan.dyPx)
    }

    @Test fun largeMenuReusesAnAlphaTileBelowPointEightMiBAtDpr3() {
        val a = WebCssShadowPlan.create(960, 606, 3f, 14f, 42f, 16f)
        val b = WebCssShadowPlan.create(960, 1500, 3f, 14f, 42f, 16f)
        assertEquals(a, b)
        val alignedRowBytes = (a.width + 3) / 4 * 4
        assertTrue(alignedRowBytes * a.height < .8 * 1024 * 1024)
        assertTrue(a.centerX > a.pad)
        assertTrue(a.centerX + 1 < a.width)
        assertTrue(a.centerY + 1 < a.height)
    }

    @Test fun aShortNarrowMenuDoesNotStretchAnIncorrectFlatBlurPlateau() {
        val plan = WebCssShadowPlan.create(384, 120, 3f, 14f, 42f, -16f)
        assertEquals(384, plan.bodyWidth)
        assertEquals(120, plan.bodyHeight)
        assertEquals(-48, plan.dyPx)
        assertEquals(plan.bodyHeight + 2 * plan.pad + 48, plan.height)
    }

    @Test fun popupUsesTheImportantGlobalOverrideInsteadOfTheLaterModelClass() {
        val root = generateSequence(File(System.getProperty("user.dir")).absoluteFile) { it.parentFile }
            .first { File(it, "frontend/src/index.css").isFile }
        val css = File(root, "frontend/src/index.css").readText()
        val rule = Regex("\\[data-slot='dropdown-menu-content'\\]\\s*\\{([^}]+)\\}").find(css)!!.groupValues[1]
        assertTrue(rule.contains("border-radius: 14px !important"))
        assertTrue(rule.contains("padding: 6px !important"))
        assertTrue(rule.contains("0 16px 42px rgba(0,0,0,.14) !important"))
    }
}
