pluginManagement {
    repositories {
        // 国内镜像优先（本机直连 google/mavenCentral 超时）
        maven { url = uri("https://maven.aliyun.com/repository/google") }
        maven { url = uri("https://maven.aliyun.com/repository/central") }
        maven { url = uri("https://maven.aliyun.com/repository/gradle-plugin") }
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        maven { url = uri("https://maven.aliyun.com/repository/google") }
        maven { url = uri("https://maven.aliyun.com/repository/central") }
        // GeckoView（内嵌浏览器内核）官方 maven 仓库
        maven { url = uri("https://maven.mozilla.org/maven2") }
        google()
        mavenCentral()
    }
}

rootProject.name = "LearnGraphNative"
include(":app")
