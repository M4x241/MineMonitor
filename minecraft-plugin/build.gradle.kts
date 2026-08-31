plugins {
    `java-library`
    id("com.gradleup.shadow") version "9.0.0"
}

group = "dev.mineexplorer"
version = "0.1.0"
description = "Exploración autónoma de minas subterráneas (Minecraft como simulador)"

repositories {
    mavenCentral()
    maven("https://repo.papermc.io/repository/maven-public/")
}

dependencies {
    compileOnly("io.papermc.paper:paper-api:26.2.build.+")
    implementation("org.java-websocket:Java-WebSocket:1.6.0")
}

java {
    sourceCompatibility = JavaVersion.VERSION_25
    targetCompatibility = JavaVersion.VERSION_25
}

tasks.withType<JavaCompile> {
    options.release.set(25)
}

tasks.jar {
    archiveBaseName.set("MineExplorer")
}

tasks.shadowJar {
    archiveBaseName.set("MineExplorer")
}

tasks.build {
    dependsOn(tasks.shadowJar)
}

tasks.processResources {
    filesMatching("plugin.yml") {
        expand("version" to project.version)
    }
}
