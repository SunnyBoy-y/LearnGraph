# Library consumer rules preserve serialization, ML Kit and Android entry points.
-keepattributes Signature,InnerClasses,EnclosingMethod
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
