plugins {
    id("com.android.application")
}

android {
    namespace = "com.team23.aplabmc"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.team23.aplabmc"
        minSdk = 26
        targetSdk = 34 // 35 forces edge-to-edge drawing under the status bar; not needed for a sideloaded demo
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/web"))
}

// The web app in ../../app is the single source; copy it into the APK's assets on every build.
val syncWeb by tasks.registering(Sync::class) {
    from(rootProject.file("../app")) {
        include("index.html", "styles.css", "i18n.js", "app.js", "apla_data.js")
    }
    into(layout.buildDirectory.dir("generated/web/www"))
}
tasks.named("preBuild") { dependsOn(syncWeb) }
