import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import java.security.MessageDigest
import org.gradle.api.artifacts.Configuration
import org.gradle.api.artifacts.component.ModuleComponentIdentifier
import org.gradle.api.attributes.Category
import org.gradle.api.attributes.Usage
import org.jetbrains.kotlin.gradle.dsl.KotlinMultiplatformExtension
import org.gradle.api.artifacts.result.ResolvedDependencyResult
import org.gradle.api.artifacts.result.UnresolvedDependencyResult
import org.gradle.api.publish.maven.MavenPublication
import org.gradle.api.publish.maven.tasks.AbstractPublishToMaven
import org.gradle.jvm.tasks.Jar
import org.jetbrains.kotlin.gradle.tasks.KotlinNativeCompile

plugins {
    kotlin("multiplatform")
    `maven-publish`
}
fun readMap(file: File) = (JsonSlurper().parse(file) as Map<*, *>).entries.associate { it.key.toString() to it.value }
fun asMap(value: Any?) = (value as Map<*, *>).entries.associate { it.key.toString() to requireNotNull(it.value) }
fun sha(file: File) = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
fun leaves(file: File): List<File> = if (file.isDirectory) file.walkTopDown().filter(File::isFile).toList() else if (file.isFile) listOf(file) else emptyList()
fun records(files: Iterable<File>) = files.flatMap(::leaves).distinct().sortedBy { it.path }.map {
    mapOf("path" to it.absolutePath, "size" to it.length(), "sha256" to sha(it))
}
val config = readMap(file("port-config.json"))
val sources = readMap(file("source-inputs.json"))
val tools = file(providers.gradleProperty("colormathTooling").get()).resolve("toolchains/kotlin-native/2.4.20-0.1.0-08")
val metadata = readMap(tools.resolve("metadata-toolchain.json"))
val metadataTools = metadata
val launcherPins = asMap(metadata.getValue("launcherPins"))
val launcherArtifacts = asMap(metadata.getValue("launcherArtifacts"))
val sourceInputs = sources
val commonizer = readMap(tools.resolve("commonizer-toolchain.json"))
val host = readMap(tools.resolve("native-compiler-toolchain.json"))
val commonizerPins = asMap(metadata.getValue("launcherPins")) + asMap(commonizer.getValue("pins"))
val commonizerArtifacts = asMap(metadata.getValue("launcherArtifacts")) + asMap(commonizer.getValue("artifacts"))
val hostPins = asMap(host.getValue("pins"))
val hostArtifacts = asMap(host.getValue("artifacts"))
val work = file(providers.gradleProperty("colormathWork").get()).canonicalFile
val fingerprint = providers.gradleProperty("colormathFingerprint").get()
val sourceRoot = file(sources.getValue("sourceRoot") as String).canonicalFile
fun upstreamSource(member: String) = sourceRoot.resolve(member.removePrefix("commonMain/"))
val manifestFile = work.resolve("source-manifest.json")
val expectedSources = (sources.getValue("sourceMembers") as List<*>).map(::asMap).associate {
    sourceRoot.resolve(it.getValue("member").toString().removePrefix("commonMain/")).canonicalFile to it.getValue("sha256").toString()
}
check(expectedSources.size == 45 && fingerprint.matches(Regex("[0-9a-f]{64}")))
check(metadata["compilerVersion"] == config["kotlin"] && commonizer["compilerVersion"] == config["kotlin"] && host["compilerVersion"] == config["kotlin"])
group = config.getValue("group").toString()
version = providers.gradleProperty("colormathVersion").get()
layout.buildDirectory.set(work.resolve("build"))
kotlin {
    ohosX64()
    ohosArm64()
    sourceSets {
        val commonMain by getting { kotlin.setSrcDirs(listOf(sourceRoot)) }
        matching { it.name.endsWith("Test") }.configureEach { kotlin.setSrcDirs(emptyList<File>()) }
    }
}
configurations.configureEach {
    if (name == host["configuration"] || name == commonizer["configuration"]) {
        val fixedPins = if (name == host["configuration"]) hostPins else commonizerPins
        resolutionStrategy.eachDependency {
            val ga = "${requested.group}:${requested.name}"
            val fixed = fixedPins[ga]?.toString() ?: error("Unpinned compiler host dependency: $ga")
            if (requested.version != fixed) useVersion(fixed)
        }
    }
    val metadataConfiguration = name.endsWith("ResolvableDependenciesMetadata") || name == "allSourceSetsCompileDependenciesMetadata"
    val library = metadataConfiguration || name.endsWith("CompileKlibraries") || name.endsWith("CompilationDependenciesMetadata")
    if (library && !name.contains("Test", ignoreCase = true)) resolutionStrategy.eachDependency {
        val ga = "${requested.group}:${requested.name}"
        check(ga == "org.jetbrains.kotlin:kotlin-stdlib") { "Unexpected Colormath library dependency: $requested" }
        if (requested.version != config["stdlib"]) useVersion(config.getValue("stdlib").toString())
    }
}
fun evidence(name: String, data: Any) {
    val target = work.resolve("evidence/$name.json")
    target.parentFile.mkdirs()
    target.writeText(JsonOutput.prettyPrint(JsonOutput.toJson(data)) + "\n")
}
fun edges(configuration: Configuration) = configuration.incoming.resolutionResult.allDependencies.map { edge ->
    if (edge !is ResolvedDependencyResult) throw GradleException("Unresolved Colormath dependency: $edge", (edge as? UnresolvedDependencyResult)?.failure)
    mapOf("from" to edge.from.id.displayName, "requested" to edge.requested.displayName, "selected" to edge.selected.id.displayName)
}
fun checkedTools(name: String, fixed: Map<String, Any>, expected: Map<String, Any>): Map<String, Any> {
    val configuration = configurations.getByName(name)
    val artifacts = configuration.resolvedConfiguration.resolvedArtifacts.map { artifact ->
        val id = artifact.moduleVersion.id
        val ga = "${id.group}:${id.name}"
        check(fixed[ga] == id.version) { "Host tool version differs: $id" }
        val item = asMap(expected.getValue(ga))
        check(sha(artifact.file) == item["sha256"] && artifact.file.length() == (item.getValue("size") as Number).toLong()) { "Host tool bytes differ: $id" }
        mapOf("gav" to id.toString(), "files" to records(listOf(artifact.file)))
    }
    check(artifacts.map { it.getValue("gav") }.toSet() == fixed.map { "${it.key}:${it.value}" }.toSet()) { "Host tool set differs: $name" }
    return mapOf("configuration" to name, "edges" to edges(configuration), "artifacts" to artifacts)
}
fun toolRecords() = mapOf("commonizer" to checkedTools(commonizer.getValue("configuration").toString(), commonizerPins, commonizerArtifacts),
    "nativeCompiler" to checkedTools(host.getValue("configuration").toString(), hostPins, hostArtifacts))
fun metadataLauncherRecords(configuration: Configuration): List<Map<String, Any>> {
    val artifacts = configuration.resolvedConfiguration.resolvedArtifacts.map { artifact ->
        val id = artifact.moduleVersion.id
        val ga = "${id.group}:${id.name}"
        check(launcherPins[ga] == id.version) { "Unpinned metadata launcher: $id" }
        val expected = asMap(launcherArtifacts.getValue(ga))
        val hash = sha(artifact.file)
        check(hash == expected.getValue("sha256") && artifact.file.length() == (expected.getValue("size") as Number).toLong()) {
            "Metadata launcher bytes differ: $id"
        }
        mapOf("gav" to id.toString(), "path" to artifact.file.absolutePath, "sha256" to hash, "size" to artifact.file.length())
    }
    check(artifacts.map { it.getValue("gav") }.toSet() == launcherPins.map { "${it.key}:${it.value}" }.toSet()) {
        "Metadata launcher artifact set differs"
    }
    return artifacts.sortedBy { it.getValue("gav").toString() }
}
gradle.projectsEvaluated {
    check(metadataTools["compilerVersion"] == config["kotlin"])
    val portProject = project
    val kmp = portProject.extensions.getByType<KotlinMultiplatformExtension>()
    val compilation = kmp.targets.getByName("metadata").compilations.getByName("commonMain")
    val original = portProject.tasks.named<KotlinNativeCompile>("compileCommonMainKotlinMetadata").get()
    portProject.tasks.getByName("allMetadataJar")
    val originalDependencies = portProject.provider { original.taskDependencies.getDependencies(original).toList() }
    val launcher = portProject.configurations.create("commonMetadataCliLauncher") {
        isCanBeConsumed = false
        isCanBeResolved = true
        isVisible = false
        attributes {
            attribute(Usage.USAGE_ATTRIBUTE, portProject.objects.named(Usage.JAVA_RUNTIME))
            attribute(Category.CATEGORY_ATTRIBUTE, portProject.objects.named(Category.LIBRARY))
        }
        resolutionStrategy.eachDependency {
            val ga = "${requested.group}:${requested.name}"
            val fixed = launcherPins[ga]?.toString() ?: error("Unreviewed metadata launcher dependency: $ga")
            if (requested.version != fixed) useVersion(fixed)
        }
    }
    portProject.dependencies.add(launcher.name, "org.jetbrains.kotlin:kotlin-compiler-embeddable:${metadataTools.getValue("compilerVersion")}")
    val output = portProject.layout.buildDirectory.dir("metadata-cli/commonMain/klib")
    val argumentsFile = portProject.layout.buildDirectory.file("metadata-cli/commonMain/arguments.txt")
    val producerFile = portProject.layout.buildDirectory.file("metadata-cli/commonMain/producer.json")
    val libraries = portProject.files(portProject.provider { original.libraries })
    val plugins = portProject.files(portProject.provider { original.compilerPluginClasspath })
    val sourceFiles = portProject.files(portProject.provider { original.sources })
    val compilerArguments = portProject.provider {
        val options = original.compilerOptions
        buildList {
            addAll(listOf("-classpath", libraries.files.sortedBy { it.path }.joinToString(File.pathSeparator) { it.invariantSeparatorsPath },
                "-d", output.get().asFile.invariantSeparatorsPath, "-module-name", "${portProject.group}:${portProject.name}_commonMain",
                "-Xtarget-platform=${metadataTools.getValue("targetPlatform")}", "-Xmulti-platform"))
            options.languageVersion.orNull?.let { addAll(listOf("-language-version", it.version)) }
            options.apiVersion.orNull?.let { addAll(listOf("-api-version", it.version)) }
            options.optIn.get().sorted().forEach { add("-opt-in=$it") }
            val freeArguments = options.freeCompilerArgs.get()
            addAll(freeArguments)
            if (!plugins.isEmpty) add("-Xplugin=" + plugins.files.sortedBy { it.path }.joinToString(",") { it.invariantSeparatorsPath })
            original.compilerPluginCommandLine.forEach { addAll(listOf("-P", it)) }
            addAll(sourceFiles.files.sortedBy { it.path }.map { it.invariantSeparatorsPath })
        }
    }
    val cli = portProject.tasks.register<JavaExec>("compileCommonMainWithMetadataCli") {
        group = "build"
        description = "使用同版本公共 metadata 编译器编译完整四十五份 commonMain 原源。"
        dependsOn(originalDependencies)
        dependsOn("transformCommonMainDependenciesMetadata")
        dependsOn("verifyColormathHost")
        classpath = launcher
        mainClass.set(metadataTools.getValue("mainClass").toString())
        setExecutable(File(System.getProperty("java.home"), "bin/" + if (System.getProperty("os.name").startsWith("Windows")) "java.exe" else "java").absolutePath)
        inputs.files(sourceFiles, libraries, plugins, launcher)
        inputs.file(manifestFile)
        inputs.file(rootProject.file("source-inputs.json"))
        inputs.file(tools.resolve("metadata-toolchain.json"))
        inputs.property("compilerArguments", compilerArguments)
        outputs.dir(output)
        outputs.file(argumentsFile)
        outputs.file(producerFile)
        args("@" + argumentsFile.get().asFile.absolutePath)
        var actualLauncher = emptyList<Map<String, Any>>()
        doFirst {
            val binding = compilation.output.classesDirs
            check(binding.files == setOf(output.get().asFile) &&
                binding.buildDependencies.getDependencies(this).map { it.path }.toSet() == setOf(path) && !original.enabled) {
                "Common metadata output/builtBy was not replaced exclusively"
            }
            val expected = (sourceInputs.getValue("sourceMembers") as List<*>).map(::asMap)
                .associate { upstreamSource(it.getValue("member").toString()).canonicalFile to it.getValue("sha256").toString() }
            check(expected.size == 45 && sourceFiles.files.map { it.canonicalFile }.toSet() == expected.keys &&
                expected.all { (source, hash) -> sha(source) == hash }) { "Complete original Colormath common compiler sources differ" }
            actualLauncher = metadataLauncherRecords(launcher)
            val argFile = argumentsFile.get().asFile
            argFile.parentFile.mkdirs()
            argFile.writeText(compilerArguments.get().joinToString("\n", postfix = "\n") {
                "\"" + it.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
            })
        }
        doLast {
            check(executionResult.get().exitValue == 0)
            val root = output.get().asFile
            check(root.resolve("default/manifest").isFile && root.resolve("default/linkdata/module").length() > 0) {
                "Public metadata compiler did not produce a Klib"
            }
            val data = mapOf("fingerprint" to fingerprint, "kind" to metadataTools.getValue("producer"), "module" to portProject.name,
                "task" to path, "exitCode" to executionResult.get().exitValue, "compilerVersion" to metadataTools.getValue("compilerVersion"),
                "targetPlatform" to metadataTools.getValue("targetPlatform"), "mainClass" to mainClass.get(), "javaExecutable" to executable,
                "launcherArguments" to args, "arguments" to compilerArguments.get(), "argumentsFile" to records(listOf(argumentsFile.get().asFile)).single(),
                "launcher" to actualLauncher, "sources" to records(sourceFiles.files), "sourceManifestSha256" to sha(manifestFile),
                "sourceSetSha256" to sourceInputs.getValue("sourceSetSha256"), "plugins" to records(plugins.files),
                "pluginOptions" to original.compilerPluginCommandLine,
                "languageOptions" to mapOf("languageVersion" to original.compilerOptions.languageVersion.orNull?.version,
                    "apiVersion" to original.compilerOptions.apiVersion.orNull?.version, "optIns" to original.compilerOptions.optIn.get().sorted(),
                    "freeCompilerArgs" to original.compilerOptions.freeCompilerArgs.get(), "explicitApi" to kmp.explicitApi?.name),
                "libraries" to libraries.files.sortedBy { it.path }.map { mapOf("path" to it.absolutePath, "files" to records(leaves(it))) },
                "libraryBuiltBy" to libraries.buildDependencies.getDependencies(this).map { it.path }.sorted(),
                "output" to root.absolutePath, "outputs" to records(leaves(root)),
                "outputBinding" to mapOf("roots" to compilation.output.classesDirs.files.map { it.absolutePath },
                    "builtBy" to compilation.output.classesDirs.buildDependencies.getDependencies(this).map { it.path }.sorted()),
                "retiredTask" to mapOf("path" to original.path, "enabled" to original.enabled))
            producerFile.get().asFile.writeText(JsonOutput.prettyPrint(JsonOutput.toJson(data)) + "\n")
        }
    }
    original.enabled = false
    compilation.output.classesDirs.setFrom(output)
    compilation.output.classesDirs.setBuiltBy(listOf(cli))
}
fun resolution() = listOf("commonMainResolvableDependenciesMetadata", "ohosX64CompileKlibraries", "ohosArm64CompileKlibraries").map { name ->
    val configuration = configurations.getByName(name)
    val artifacts = configuration.incoming.artifacts.artifacts.map { artifact ->
        val id = artifact.id.componentIdentifier
        check(id is ModuleComponentIdentifier && id.group == "org.jetbrains.kotlin" && id.module == "kotlin-stdlib" && id.version == config["stdlib"]) {
            "Unexpected Colormath library artifact: $id"
        }
        mapOf("component" to id.displayName, "attributes" to artifact.variant.attributes.keySet().associate {
            it.name to artifact.variant.attributes.getAttribute(it).toString()
        }, "files" to records(listOf(artifact.file)))
    }
    mapOf("configuration" to name, "edges" to edges(configuration), "artifacts" to artifacts)
}
val verifyHost = tasks.register("verifyColormathHost") {
    outputs.upToDateWhen { false }
    doLast { evidence("host", mapOf("fingerprint" to fingerprint, "tools" to toolRecords())) }
}
tasks.withType<KotlinNativeCompile>().configureEach {
    if (name in listOf("compileCommonMainKotlinMetadata", "compileKotlinOhosX64", "compileKotlinOhosArm64")) dependsOn(verifyHost)
}
tasks.register("resolveColormath") {
    doLast {
        check(gradle.gradleVersion == config["gradle"])
        evidence("resolve", mapOf("fingerprint" to fingerprint, "tools" to toolRecords(), "metadataLauncher" to metadataLauncherRecords(configurations.getByName("commonMetadataCliLauncher")), "configurations" to resolution()))
    }
}
fun recordCompilation(name: String, compilerName: String, stage: String) {
    tasks.register(name) {
        val producerName = if (stage == "metadata") "compileCommonMainWithMetadataCli" else compilerName
        dependsOn(producerName)
        if (stage == "metadata") dependsOn("allMetadataJar")
        outputs.upToDateWhen { false }
        doLast {
            val compiler = tasks.getByName(compilerName) as KotlinNativeCompile
            val actualSources = compiler.sources.files.filter { it.isFile && it.extension == "kt" }.map { it.canonicalFile }.toSet()
            check(actualSources == expectedSources.keys && expectedSources.all { (path, hash) -> sha(path) == hash }) { "Original Colormath source set differs" }
            val producer = tasks.getByName(producerName)
            check(producer.enabled && producer.state.executed && !producer.state.noSource && producer.state.failure == null &&
                producer.state.skipMessage in listOf(null, "UP-TO-DATE", "FROM-CACHE")) { "Expected compiler did not execute" }
            val outputs = records(producer.outputs.files.files)
            check(outputs.any { (it.getValue("size") as Long) > 0 }) { "Compiler output is empty" }
            val metadataJar = if (stage == "metadata") tasks.getByName("allMetadataJar") else null
            if (metadataJar != null) check(metadataJar.state.executed && !metadataJar.state.noSource)
            evidence(stage, mapOf("fingerprint" to fingerprint, "stage" to stage, "task" to producer.path,
                "sourceCount" to actualSources.size, "sources" to records(actualSources), "outputs" to outputs,
                "metadataJarOutputs" to if (metadataJar == null) emptyList<Any>() else records(metadataJar.outputs.files.files),
                "commonMetadataCli" to if (stage == "metadata") readMap(layout.buildDirectory.file("metadata-cli/commonMain/producer.json").get().asFile) else null,
                "plugins" to records(compiler.compilerPluginClasspath?.files.orEmpty()), "pluginOptions" to compiler.compilerPluginCommandLine,
                "libraries" to compiler.libraries.files.sortedBy { it.path }.map { mapOf("path" to it.absolutePath, "files" to records(listOf(it))) },
                "runtime" to "NOT_RUN"))
        }
    }
}
recordCompilation("recordColormathMetadata", "compileCommonMainKotlinMetadata", "metadata")
recordCompilation("recordColormathX64", "compileKotlinOhosX64", "native-x64")
recordCompilation("recordColormathArm64", "compileKotlinOhosArm64", "native-arm64")
publishing {
    repositories { maven { name = "Port"; url = uri(providers.gradleProperty("colormathRepository").get()) } }
    publications.withType<MavenPublication>().configureEach {
        artifact(file("legal/LICENSE")) { classifier = "license"; extension = "txt" }
        artifact(work.resolve("provenance.json")) { classifier = "provenance"; extension = "json" }
        if (name == "kotlinMultiplatform") {
            listOf("sources" to ("upstream-sources" to "jar"), "pom" to ("upstream-pom" to "xml"),
                "module" to ("upstream-module" to "json")).forEach { (key, format) ->
                artifact(file(asMap(sources.getValue(key)).getValue("path"))) { classifier = format.first; extension = format.second }
            }
        }
        pom {
            name.set("Colormath")
            description.set("Complete unmodified Colormath 3.6.1 sources for CPF OHOS")
            licenses { license { name.set("MIT License"); url.set("https://opensource.org/license/mit") } }
            scm { url.set("https://github.com/ajalt/colormath") }
        }
    }
}
tasks.withType<AbstractPublishToMaven>().configureEach {
    doFirst {
        check(readMap(work.resolve("provenance.json"))["fingerprint"] == fingerprint) { "Provenance identity differs" }
        listOf("sources", "pom", "module", "license").forEach { key ->
            val item = asMap(sources.getValue(key))
            check(sha(file(item.getValue("path"))) == item["sha256"]) { "Upstream provenance differs: $key" }
        }
        listOf("metadata", "native-x64", "native-arm64").forEach {
            check(readMap(work.resolve("evidence/$it.json"))["fingerprint"] == fingerprint) { "Compilation receipt differs: $it" }
        }
    }
}
tasks.withType<Jar>().configureEach {
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
    from(file("legal/LICENSE")) { into("META-INF/licenses/colormath") }
    from(work.resolve("provenance.json")) { into("META-INF/upstream") }
}
tasks.register("publishColormath") { dependsOn("publishAllPublicationsToPortRepository") }
