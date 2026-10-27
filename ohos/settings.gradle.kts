import groovy.json.JsonSlurper

pluginManagement {
    repositories {
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-develop/")
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-public/")
        gradlePluginPortal()
        mavenCentral()
    }
    plugins { id("org.jetbrains.kotlin.multiplatform") version "2.4.20-0.1.0-08" }
}
val tools = file(providers.gradleProperty("colormathTooling").get())
val host = tools.resolve("toolchains/kotlin-native/2.4.20-0.1.0-08")
fun tool(name: String) = JsonSlurper().parse(host.resolve(name)) as Map<*, *>
val allowed = (tool("metadata-toolchain.json")["launcherPins"] as Map<*, *>).entries +
    (tool("commonizer-toolchain.json")["pins"] as Map<*, *>).entries +
    (tool("native-compiler-toolchain.json")["pins"] as Map<*, *>).entries

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-develop/") {
            content { allowed.forEach { (ga, version) ->
                val parts = ga.toString().split(":")
                includeVersion(parts[0], parts[1], version.toString())
            } }
        }
        maven("https://maven.eazytec-cloud.com/nexus/repository/maven-public/") {
            content { allowed.forEach { (ga, version) ->
                val parts = ga.toString().split(":")
                includeVersion(parts[0], parts[1], version.toString())
            } }
        }
        mavenCentral()
    }
}
rootProject.name = "colormath"
