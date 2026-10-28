package com.learngraph.mobile.ui.components

import java.io.File
import org.junit.Assert.*
import org.junit.Test

class WebMotionContractTest {
    @Test fun cssTransitionReversalUsesCurrentEasedOutputAndComposesRepeatedReversals() {
        val transition = WebCssTransitionTracker()
        assertEquals(320, transition.begin(0f, 1f, 320, false))
        assertEquals(192, transition.begin(.6f, 0f, 320, true))
        // Reverse again at .4: remaining travel to 1 is .6, rather than full320.
        assertEquals(192, transition.begin(.4f, 1f, 320, true))
    }

    @Test fun aNonReversingSliderStopGetsTheFullCssDuration() {
        val transition = WebCssTransitionTracker()
        assertEquals(120, transition.begin(0f, .25f, 120, false))
        assertEquals(120, transition.begin(.1f, .5f, 120, true))
    }

    @Test fun fingerFollowDisablesTransitionsAndReleaseRestoresFullDuration() {
        val transition = WebCssTransitionTracker()
        transition.begin(0f, 1f, 340, false)
        transition.reset()
        assertEquals(340, transition.begin(.7f, 0f, 340, true))
    }

    @Test fun reducedMotionSnapsOrdinaryKeyframesButRetainsTheDrawerFadeContract() {
        assertEquals(0, WebMotionContract.duration(WebMotionContract.MenuMillis, true))
        assertEquals(0, WebMotionContract.duration(WebMotionContract.DialogMillis, true))
        assertEquals(140, WebMotionContract.ReducedDrawerMillis)
        assertEquals(100, WebMotionContract.duration(WebMotionContract.MenuMillis, false))
    }

    @Test fun popupOriginMatchesMeasuredRadixEndTopEntryAndKeepsEachAlignmentDistinct() {
        val progress = .132906f
        // Same-device Web: width320, height201.78125; origin160/100.890625
        // interpolates toward320/0 rather than the trigger center (~66px).
        assertEquals(181.26495f, 320f * WebMotionContract.popupOriginFraction(1f, progress, false), .001f)
        assertEquals(87.48164f, 201.78125f * WebMotionContract.popupOriginFraction(0f, progress, false), .001f)
        assertEquals(.5f, WebMotionContract.popupOriginFraction(0f, 0f, false), 0f)
        assertEquals(0f, WebMotionContract.popupOriginFraction(0f, 1f, false), 0f)
        assertEquals(1f, WebMotionContract.popupOriginFraction(1f, 1f, false), 0f)
        assertEquals(1f, WebMotionContract.popupOriginFraction(1f, .2f, true), 0f)
        assertEquals(0f, WebMotionContract.popupOriginFraction(0f, .2f, true), 0f)
    }

    @Test fun nativeTokensRemainBoundToTheActualWebSourceRatherThanMaterialDefaults() {
        val root = generateSequence(File(System.getProperty("user.dir")).absoluteFile) { it.parentFile }
            .first { File(it, "frontend/src/index.css").isFile }
        val css = File(root, "frontend/src/index.css").readText()
        val menu = File(root, "frontend/src/components/ui/dropdown-menu.tsx").readText()
        val dialog = File(root, "frontend/src/components/ui/dialog.tsx").readText()
        assertTrue(menu.contains("duration-${WebMotionContract.MenuMillis}"))
        assertTrue(menu.contains("slide-in-from-top-2"))
        assertFalse(menu.contains("slide-out-to-"))
        assertTrue(dialog.contains("duration-${WebMotionContract.DialogMillis}"))
        assertTrue(dialog.contains("zoom-out-95"))
        assertTrue(dialog.contains("bg-black/12"))
        assertTrue(css.contains("transform ${WebMotionContract.LeftDrawerMillis}ms"))
        assertTrue(css.contains("transform ${WebMotionContract.RightDrawerMillis}ms"))
        assertTrue(css.contains("transition-duration: ${WebMotionContract.ReducedDrawerMillis}ms !important"))
        assertTrue(css.contains("transition: width .12s ease"))
        assertEquals(120, WebMotionContract.SliderMillis)
    }
}
