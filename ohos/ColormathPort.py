"""Colormath 3.6.1 的独立 OHOS producer；不部署、不读取凭据。"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
from urllib.request import ProxyHandler, build_opener
import xml.etree.ElementTree as ET
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TOOL_REVISION = "d0cff88fd1ea2e4f08bb4409f1c802cca0c7f285"
TOOL_DIR = "toolchains/kotlin-native/2.4.20-0.1.0-08"
GROUP = "io.github.qsxs.cpf.colormath"
MODULE = "colormath"
TASKS = {"Tasks": ["tasks", "--all"], "Resolve": ["resolveColormath"], "Metadata": ["recordColormathMetadata"],
         "NativeX64": ["recordColormathX64"], "NativeArm64": ["recordColormathArm64"], "Publish": ["publishColormath"]}
COMPILES = (("Metadata", "metadata", None), ("NativeX64", "native-x64", "ohos_x64"),
            ("NativeArm64", "native-arm64", "ohos_arm64"))
REQUIRED = {"resolveColormath", "verifyColormathHost", "compileCommonMainWithMetadataCli", "compileCommonMainKotlinMetadata", "compileKotlinOhosX64",
            "compileKotlinOhosArm64", "recordColormathMetadata", "recordColormathX64", "recordColormathArm64", "publishColormath"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def record(path):
    path = Path(path)
    data = path.read_bytes()
    return {"path": str(path.resolve()), "size": len(data), "sha256": digest(data)}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_environment():
    return {key: os.environ[key] for key in ("PATH", "HOME", "JAVA_HOME", "LANG", "LC_ALL", "SSL_CERT_FILE",
                                              "SSL_CERT_DIR", "LD_LIBRARY_PATH", "GRADLE_USER_HOME", "KONAN_DATA_DIR",
                                              "TMPDIR", "TEMP", "TMP") if key in os.environ}


def checked_tools(work, explicit):
    lock = load(HERE / "tooling-lock.json")
    if lock["revision"] != TOOL_REVISION or lock["repository"] != "https://github.com/qsxs/cpf-port-tooling":
        raise RuntimeError("Immutable shared tooling revision differs")
    if lock["archiveUrl"] != lock["repository"] + "/archive/" + TOOL_REVISION + ".tar.gz":
        raise RuntimeError("Shared tooling archive URL differs")
    if len(lock["files"]) != 20 or len({row["path"] for row in lock["files"]}) != 20:
        raise RuntimeError("Shared tooling file lock differs")
    directory = Path(explicit).resolve() if explicit else (work / "tooling" / TOOL_REVISION).resolve()
    for row in lock["files"]:
        relative = PurePosixPath(row["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in row["path"] or not row["path"].startswith(TOOL_DIR + "/"):
            raise RuntimeError("Unsafe shared tooling file path")
    if not explicit and not all((directory / row["path"]).is_file() for row in lock["files"]):
        # 仅读取锁定文件；归档压缩容器本身不作为内容身份。
        with build_opener(ProxyHandler({})).open(lock["archiveUrl"], timeout=60) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise RuntimeError("Shared tooling archive is too large")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            for row in lock["files"]:
                matches = [member for member in members if "/" in member.name and member.name.split("/", 1)[1] == row["path"]]
                if len(matches) != 1 or not matches[0].isfile():
                    raise RuntimeError("Shared tooling archive member differs: " + row["path"])
                stream = archive.extractfile(matches[0])
                if stream is None:
                    raise RuntimeError("Shared tooling archive member is unreadable")
                content = stream.read(row["size"] + 1)
                if len(content) != row["size"] or digest(content) != row["sha256"]:
                    raise RuntimeError("Shared tooling archive bytes differ: " + row["path"])
                target = directory / row["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if target.read_bytes() != content:
                        raise RuntimeError("Existing shared tooling bytes differ")
                else:
                    with target.open("xb") as out:
                        out.write(content)
    for row in lock["files"]:
        actual = record(directory / row["path"])
        if (actual["size"], actual["sha256"]) != (row["size"], row["sha256"]):
            raise RuntimeError("Shared tooling identity differs: " + row["path"])
    return directory


def source_facts():
    model = load(HERE / "source-inputs.json")
    source_root = (HERE / model["sourceRoot"]).resolve()
    expected = {row["member"]: row for row in model["sourceMembers"]}
    if len(expected) != 45:
        raise RuntimeError("Colormath source manifest must contain forty-five original Kotlin files")
    actual_paths = {"commonMain/" + path.relative_to(source_root).as_posix() for path in source_root.rglob("*.kt")}
    if actual_paths != set(expected):
        raise RuntimeError("Colormath upstream Kotlin source inventory differs")
    for key in ("sources", "pom", "module", "license"):
        item = model[key]
        found = record(HERE / item["path"])
        if (found["size"], found["sha256"]) != (item["size"], item["sha256"]):
            raise RuntimeError("Original upstream evidence differs: " + key)
    rows = []
    with zipfile.ZipFile(HERE / model["sources"]["path"]) as archive:
        if {name for name in archive.namelist() if name.endswith(".kt")} != set(expected):
            raise RuntimeError("Official sources archive inventory differs")
        for member, item in sorted(expected.items()):
            path = source_root / member.removeprefix("commonMain/")
            data = path.read_bytes()
            if len(data) != item["size"] or digest(data) != item["sha256"] or archive.read(member) != data:
                raise RuntimeError("Original Colormath source bytes differ: " + member)
            rows.append({"member": member, **record(path)})
    return model, rows


def identity(args, tools):
    config = load(HERE / "port-config.json")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_sha) or not re.fullmatch(r"[1-9][0-9]*", args.run_id) or not re.fullmatch(r"[1-9][0-9]*", args.run_attempt):
        raise RuntimeError("Immutable source SHA and positive run identifiers are required")
    actual = subprocess.run(["git", "-C", str(HERE.parent), "rev-parse", "HEAD"], env=safe_environment(), check=True, capture_output=True, text=True).stdout.strip()
    if actual != args.source_sha:
        raise RuntimeError("Colormath checkout differs from requested source SHA")
    if config["group"] != GROUP or config["artifact"] != MODULE or config["kotlin"] != "2.4.20-0.1.0-08":
        raise RuntimeError("Colormath port identity differs")
    model, rows = source_facts()
    recipe = []
    for path in sorted(HERE.rglob("*")):
        relative = path.relative_to(HERE)
        if path.is_file() and not any(part in (".local", ".gradle", ".kotlin", "build", "__pycache__") for part in relative.parts):
            data = path.read_bytes()
            recipe.append({"path": relative.as_posix(), "size": len(data), "sha256": digest(data)})
    value = {"sourceCommit": args.source_sha, "sourceTagCommit": model["upstream"]["commit"], "sources": rows,
             "recipe": recipe, "toolingCommit": TOOL_REVISION, "toolingFiles": load(HERE / "tooling-lock.json")["files"],
             "group": GROUP, "version": "3.6.1-cpf11-preview-" + args.source_sha[:12] + "-" + args.run_id + "-" + args.run_attempt}
    # 指纹不含机器路径；源码使用稳定的成员名、大小和哈希。
    fingerprint_value = {**value, "sources": [{key: row[key] for key in ("member", "size", "sha256")} for row in rows]}
    value["fingerprint"] = digest(json.dumps(fingerprint_value, sort_keys=True, separators=(",", ":")).encode())
    value["toolingHome"] = str(tools)
    value["work"] = str(Path(args.work).resolve())
    value["repository"] = str(Path(args.repository).resolve())
    return value


def check_native(home):
    if not home:
        raise RuntimeError("Explicit Kotlin/Native home is required")
    props = dict(line.split("=", 1) for line in (Path(home) / "konan/konan.properties").read_text(encoding="utf-8").splitlines() if "=" in line)
    if props.get("compilerVersion", "").strip() != "2.4.20-0.1.0-08":
        raise RuntimeError("Kotlin/Native compilerVersion differs")


def verify_stage(value, stage, target):
    row = load(Path(value["work"]) / "evidence" / (stage + ".json"))
    if row["fingerprint"] != value["fingerprint"] or row["stage"] != stage or row["sourceCount"] != 45:
        raise RuntimeError("Colormath compile evidence identity differs: " + stage)
    expected = {(source["size"], source["sha256"], source["path"]) for source in value["sources"]}
    actual = {(source["size"], source["sha256"], source["path"]) for source in row["sources"]}
    if actual != expected or row["plugins"]:
        raise RuntimeError("Colormath source or producer compiler plugin set differs")
    if target is None:
        cli = row.get("commonMetadataCli") or {}
        if (cli.get("fingerprint") != value["fingerprint"] or cli.get("exitCode") != 0 or
                cli.get("compilerVersion") != "2.4.20-0.1.0-08" or
                cli.get("mainClass") != "org.jetbrains.kotlin.cli.metadata.KotlinMetadataCompiler" or
                cli.get("retiredTask", {}).get("enabled") is not False or cli.get("plugins")):
            raise RuntimeError("Public metadata CLI receipt differs")
    metadata = ir = False
    targets = set()
    for item in row["outputs"] + row["metadataJarOutputs"]:
        path = Path(item["path"])
        if record(path) != item:
            raise RuntimeError("Colormath compile output changed: " + str(path))
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                metadata |= any(("/linkdata/" in entry.filename or entry.filename.endswith(".kotlin_metadata")) and entry.file_size > 0 for entry in entries)
                ir |= any("/ir/" in entry.filename and entry.file_size > 0 for entry in entries)
                if "default/manifest" in archive.namelist():
                    props = dict(line.split("=", 1) for line in archive.read("default/manifest").decode().splitlines() if "=" in line)
                    targets.update(props.get("native_targets", "").split())
        else:
            metadata |= "/linkdata/" in path.as_posix() and item["size"] > 0
            ir |= "/ir/" in path.as_posix() and item["size"] > 0
            if path.as_posix().endswith("/default/manifest"):
                props = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
                targets.update(props.get("native_targets", "").split())
    if not metadata or (target is not None and (not ir or targets != {target})):
        raise RuntimeError("Colormath output metadata, IR or native target differs: " + stage)
    return {"stage": stage, "sourceCount": 45, "metadata": metadata, "ir": ir, "nativeTargets": sorted(targets)}


def verify_publication(value):
    model = load(HERE / "source-inputs.json")
    repository = Path(value["repository"]) / GROUP.replace(".", "/")
    version = value["version"]
    root_file = repository / MODULE / version / (MODULE + "-" + version + ".module")
    root = load(root_file)
    expected_dep = "org.jetbrains.kotlin:kotlin-stdlib"
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    files, scopes = [], []
    for suffix, target in (("", None), ("-ohosx64", "ohos_x64"), ("-ohosarm64", "ohos_arm64")):
        name = MODULE + suffix
        directory = repository / name / version
        module_file = directory / (name + "-" + version + ".module")
        gmm = load(module_file)
        if any(gmm["component"].get(key) != expected for key, expected in (("group", GROUP), ("module", MODULE), ("version", version))):
            raise RuntimeError("Colormath GMM owner differs: " + name)
        if target:
            if (directory / gmm["component"]["url"]).resolve() != root_file.resolve():
                raise RuntimeError("Colormath Native leaf owner differs")
            links = [variant["available-at"] for variant in root["variants"] if variant.get("attributes", {}).get("org.jetbrains.kotlin.native.target") == target and "available-at" in variant]
            if not links or any(link["group"] != GROUP or link["module"] != name or link["version"] != version or
                                (root_file.parent / link["url"]).resolve() != module_file.resolve() for link in links):
                raise RuntimeError("Colormath root available-at differs: " + target)
        api = []
        for variant in gmm["variants"]:
            attrs = variant.get("attributes", {})
            if (not target and attrs.get("org.gradle.usage") == "kotlin-metadata" and attrs.get("org.jetbrains.kotlin.platform.type") == "common") or (target and attrs.get("org.gradle.usage") == "kotlin-api" and attrs.get("org.jetbrains.kotlin.native.target") == target):
                api.append(variant)
            for dep in variant.get("dependencies", []):
                if dep["group"] + ":" + dep["module"] != expected_dep or dep.get("version", {}).get("requires") != "2.4.20-0.1.0-08":
                    raise RuntimeError("Colormath publication contains an unexpected dependency")
            for item in variant.get("files", []):
                path = (directory / item["url"]).resolve()
                if not path.is_relative_to(directory.resolve()):
                    raise RuntimeError("Colormath publication artifact URL escapes version directory")
                actual = record(path)
                if actual["size"] <= 0 or actual["size"] != item["size"] or actual["sha256"] != item["sha256"]:
                    raise RuntimeError("Colormath publication artifact bytes differ")
        if not api or not all(v.get("files") for v in api) or any({d["group"] + ":" + d["module"] for d in v.get("dependencies", [])} != {expected_dep} for v in api):
            raise RuntimeError("Colormath publication API metadata or dependencies differ")
        for variant in api:
            for item in variant["files"]:
                archive_path = (directory / item["url"]).resolve()
                with zipfile.ZipFile(archive_path) as archive:
                    entries = archive.infolist()
                    if not any(("/linkdata/" in entry.filename or entry.filename.endswith(".kotlin_metadata")) and entry.file_size > 0 for entry in entries):
                        raise RuntimeError("Published Colormath API artifact has no metadata")
                    if target:
                        if not any("/ir/" in entry.filename and entry.file_size > 0 for entry in entries) or "default/manifest" not in archive.namelist():
                            raise RuntimeError("Published Colormath Native API artifact has no IR or manifest")
                        props = dict(line.split("=", 1) for line in archive.read("default/manifest").decode().splitlines() if "=" in line)
                        if set(props.get("native_targets", "").split()) != {target}:
                            raise RuntimeError("Published Colormath Native API target differs")
        pom = ET.fromstring((directory / (name + "-" + version + ".pom")).read_bytes())
        if any(pom.findtext("m:" + key, namespaces=ns) != expected for key, expected in (("groupId", GROUP), ("artifactId", name), ("version", version))):
            raise RuntimeError("Colormath POM coordinates differ")
        dependencies = pom.findall("m:dependencies/m:dependency", ns)
        for dep in dependencies:
            ga = dep.findtext("m:groupId", namespaces=ns) + ":" + dep.findtext("m:artifactId", namespaces=ns)
            if ga != expected_dep or dep.findtext("m:version", namespaces=ns) != "2.4.20-0.1.0-08" or dep.findtext("m:scope", namespaces=ns) != ("compile" if target else "runtime"):
                raise RuntimeError("Colormath POM dependency or scope differs")
        if len(dependencies) != 1:
            raise RuntimeError("Colormath POM dependency set differs")
        license_file = directory / (name + "-" + version + "-license.txt")
        if digest(license_file.read_bytes()) != model["license"]["sha256"]:
            raise RuntimeError("Colormath published license differs")
        provenance_file = directory / (name + "-" + version + "-provenance.json")
        if provenance_file.read_bytes() != (Path(value["work"]) / "provenance.json").read_bytes():
            raise RuntimeError("Published provenance differs")
        source_jar = directory / (name + "-" + version + "-sources.jar")
        with zipfile.ZipFile(source_jar) as archive:
            expected_sources = {row["member"]: row["sha256"] for row in model["sourceMembers"]}
            present = {entry: digest(archive.read(entry)) for entry in archive.namelist() if entry.endswith(".kt")}
            licenses = [entry for entry in archive.namelist() if entry.endswith("META-INF/licenses/colormath/LICENSE")]
            provenance_entries = [entry for entry in archive.namelist() if entry.endswith("META-INF/upstream/provenance.json")]
            if len(provenance_entries) != 1 or archive.read(provenance_entries[0]) != provenance_file.read_bytes():
                raise RuntimeError("Published source archive provenance differs")
            if present != expected_sources or len(licenses) != 1 or digest(archive.read(licenses[0])) != model["license"]["sha256"]:
                raise RuntimeError("Colormath published source archive or license differs")
        if not target:
            for key, classifier in (("sources", "upstream-sources.jar"), ("pom", "upstream-pom.xml"), ("module", "upstream-module.json")):
                if digest((directory / (name + "-" + version + "-" + classifier)).read_bytes()) != model[key]["sha256"]:
                    raise RuntimeError("Colormath original source evidence differs in publication: " + key)
        entries = [path for path in sorted(directory.iterdir()) if path.is_file()]
        if not entries or any(path.stat().st_size <= 0 for path in entries):
            raise RuntimeError("Publication directory is empty or contains empty files")
        for path in entries:
            if path.suffix in (".md5", ".sha1", ".sha256", ".sha512"):
                continue
            for algorithm in ("md5", "sha1", "sha256", "sha512"):
                checksum = path.with_name(path.name + "." + algorithm)
                if not checksum.is_file() or checksum.read_text().strip().lower() != hashlib.new(algorithm, path.read_bytes()).hexdigest():
                    raise RuntimeError("Publication checksum differs: " + str(checksum))
        files.extend(record(path) for path in entries)
        scopes.append({"module": name, "scope": "compile" if target else "runtime", "dependency": expected_dep})
    result = {"status": "VERIFIED_STAGING_NOT_PUBLISHED", "fingerprint": value["fingerprint"], "sourceCommit": value["sourceCommit"],
              "toolingCommit": TOOL_REVISION, "group": GROUP, "version": version, "files": files, "pomScopes": scopes,
              "compilations": [verify_stage(value, stage, target) for _, stage, target in COMPILES], "link": "NOT_RUN", "runtime": "NOT_RUN"}
    write(Path(value["work"]) / "publication.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("Prepare", *TASKS, "Verify"))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--tooling", type=Path)
    parser.add_argument("--native-home", type=Path)
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    tools = checked_tools(work, args.tooling)
    value = identity(args, tools)
    prepared = work / "prepared.json"
    model = load(HERE / "source-inputs.json")
    provenance = {"fingerprint": value["fingerprint"], "sourceCommit": value["sourceCommit"],
                  "upstream": model["upstream"], "group": GROUP, "module": MODULE, "version": value["version"],
                  "toolingCommit": TOOL_REVISION, "sourceMembers": model["sourceMembers"],
                  "upstreamFiles": {key: model[key] for key in ("sources", "pom", "module", "license")}}
    if args.stage == "Prepare":
        if prepared.exists() and load(prepared) != value:
            raise RuntimeError("Existing work identity differs; use a fresh work directory")
        write(work / "source-manifest.json", {"fingerprint": value["fingerprint"], "members": value["sources"], "sourceCount": 45})
        write(prepared, value)
        write(work / "provenance.json", provenance)
        print(json.dumps({"work": str(work), "toolingHome": str(tools), "fingerprint": value["fingerprint"], "version": value["version"]}))
        return
    if not prepared.is_file() or load(prepared) != value:
        raise RuntimeError("Colormath work was not prepared with the current source and recipe")
    if load(work / "provenance.json") != provenance:
        raise RuntimeError("Publication provenance differs from the prepared identity")
    if args.stage == "Verify":
        print(json.dumps(verify_publication(value)))
        return
    check_native(args.native_home)
    if args.stage != "Tasks":
        discovery = load(work / "task-discovery.json")
        if discovery["fingerprint"] != value["fingerprint"] or record(discovery["stdout"]["path"]) != discovery["stdout"]:
            raise RuntimeError("Colormath task discovery differs")
    if args.stage not in ("Tasks", "Resolve"):
        if load(work / "evidence/resolve.json")["fingerprint"] != value["fingerprint"]:
            raise RuntimeError("Colormath Resolve belongs to another recipe")
        for label, stage, target in COMPILES:
            if label == args.stage:
                break
            verify_stage(value, stage, target)
    if args.stage == "Publish":
        root = Path(value["repository"]) / GROUP.replace(".", "/")
        for module in (MODULE, MODULE + "-ohosx64", MODULE + "-ohosarm64"):
            if (root / module / value["version"]).exists():
                raise RuntimeError("Publication version already exists: " + module)
    argv = ["sh", str(HERE / "gradlew"), "-p", str(HERE), "--project-cache-dir", str(work / "project-cache"),
            "-PcolormathWork=" + str(work), "-PcolormathTooling=" + str(tools), "-PcolormathFingerprint=" + value["fingerprint"],
            "-PcolormathVersion=" + value["version"], "-PcolormathRepository=" + value["repository"],
            "-Pkotlin.project.persistent.dir=" + str(work / "kotlin-cache"), "-Pkotlin.native.home=" + str(args.native_home.resolve()),
            "--console=plain", "--stacktrace", *TASKS[args.stage]]
    logs = work / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout, stderr = logs / (args.stage + ".stdout.log"), logs / (args.stage + ".stderr.log")
    with stdout.open("wb") as out, stderr.open("wb") as err:
        run = subprocess.run(argv, env=safe_environment(), stdout=out, stderr=err)
    if run.returncode:
        print(stderr.read_text(encoding="utf-8", errors="replace")[-16384:], file=sys.stderr)
        raise RuntimeError("Gradle " + args.stage + " failed with exit " + str(run.returncode))
    if args.stage == "Tasks":
        text = stdout.read_text(encoding="utf-8", errors="replace")
        names = {name.lstrip(":") for name in re.findall(r"^\s*([\w:-]+)(?:\s+-|\s*$)", text, re.MULTILINE)}
        if not REQUIRED.issubset(names):
            raise RuntimeError("Required Colormath tasks absent from actual task discovery")
        write(work / "task-discovery.json", {"fingerprint": value["fingerprint"], "required": sorted(REQUIRED), "stdout": record(stdout)})
    for label, stage, target in COMPILES:
        if label == args.stage:
            verify_stage(value, stage, target)
    print(json.dumps({"stage": args.stage, "exitCode": run.returncode, "fingerprint": value["fingerprint"], "stdout": record(stdout)}))


if __name__ == "__main__":
    main()
