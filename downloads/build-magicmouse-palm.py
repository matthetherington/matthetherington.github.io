#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build only: a separately named palm/hover-patched driver for running Fedora Asahi.

Never installs packages, loads/unloads drivers, writes sysfs, or changes boot files.
Run --help or --check first. See the adjacent README for prerequisites/limitations.
"""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request

COMMIT = "d4f767712c8b0097e0996f951d3c9cea2648ee97"
PATCH_SHA256 = "3e4d0654eb497ed0680429b665519429a17880fb31039310e979eb61fdb1d661"
PATCH_URL = f"https://github.com/AsahiLinux/linux/commit/{COMMIT}.patch"
MODULE = "hid_magicmouse_palm_test"
DRIVER = "magicmouse_palm_test"
MODULE_VERSION = "d4f767712c-portable1"
DRIVER_SUFFIX = "/drivers/hid/hid-magicmouse.c"
PACKAGE_FORMAT = "%{NAME}\\n%{VERSION}\\n%{RELEASE}\\n%{ARCH}\\n%{SOURCERPM}\\n%{VENDOR}\\n%{SIGPGP:pgpsig}\\n%{SOURCEPACKAGE}\\n"
TOOLS = ("rpm", "rpm2cpio", "cpio", "git", "make", "gcc", "ld", "nm", "readelf", "modinfo", "bwrap")


class Refused(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Commands:
    def __init__(self):
        self.logdir = None
        self.number = 0

    def run(self, args, *, cwd=None, check=True, timeout=300):
        args = list(map(str, args))
        # Do not inherit overrides that can alter the kernel config/compiler or
        # downgrade MODPOST failures to warnings.
        overrides = {
            "ARCH", "SUBARCH", "CROSS_COMPILE", "CC", "HOSTCC", "LD", "HOSTLD",
            "AR", "NM", "OBJCOPY", "OBJDUMP", "READELF", "STRIP", "KCFLAGS", "KAFLAGS",
            "CFLAGS", "CPPFLAGS", "LDFLAGS", "CFLAGS_MODULE", "AFLAGS_MODULE",
            "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "BASH_ENV", "ENV",
        }
        environment = {key: value for key, value in os.environ.items()
                       if key not in overrides and not key.startswith(("KBUILD_", "KCONFIG_", "LLVM", "GIT_"))}
        environment.update({"LC_ALL": "C", "LANG": "C", "GIT_CONFIG_NOSYSTEM": "1",
                            "GIT_CONFIG_GLOBAL": "/dev/null"})
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            env=environment,
        )
        if self.logdir:
            self.number += 1
            (self.logdir / f"{self.number:02d}-{Path(args[0]).name}.log").write_text(
                "$ " + shlex.join(args) + "\n" + result.stdout + result.stderr
            )
        if check and result.returncode:
            tail = "\n".join((result.stdout + result.stderr).splitlines()[-35:])
            raise Refused(f"Command failed ({result.returncode}): {shlex.join(args)}\n{tail}")
        return result


@dataclass
class Package:
    name: str
    version: str
    release: str
    arch: str
    source_rpm: str
    vendor: str
    signature: str
    source_package: str = "0"

    @property
    def nevra(self):
        return f"{self.name}-{self.version}-{self.release}.{self.arch}"

    @property
    def fedora(self):
        match = re.search(r"\.fc([0-9]+)(?:\.|$)", self.release)
        require(match, f"Cannot identify Fedora release from RPM release {self.release!r}")
        return match.group(1)

    @property
    def signing_key(self):
        match = re.search(r"Key ID ([a-f0-9]{8,40})", self.signature, re.I)
        require(match, "Installed kernel has no recognizable RPM signing-key identity")
        return match.group(1).lower()


def package_info(commands, target, *, installed=False, file_owner=False):
    selector = "-qf" if file_owner else ("-q" if installed else "-qp")
    text = commands.run(["rpm", selector, "--qf", PACKAGE_FORMAT, target]).stdout
    fields = text.splitlines()
    require(len(fields) == 8, "RPM query did not return exactly one package")
    return Package(*fields)


def check_signature(text, installed_key):
    require(not re.search(r"NOKEY|NOT\s+OK|NOTTRUSTED|BAD|UNTRUSTED", text, re.I),
            "RPM signature/digest validation failed or its signing key is untrusted")
    # Accommodate both rpm 4's short key IDs and newer rpm's full fingerprints.
    keys = re.findall(r"signature[^\n]*?(?:fingerprint:|key ID)\s*([a-f0-9]{8,40}):\s*OK", text, re.I)
    require(keys, "A verified OpenPGP signature is required; digests alone are not enough")
    require(any(k.lower().endswith(installed_key) or installed_key.endswith(k.lower()) for k in keys),
            "RPM signer does not match the installed kernel's signer")


def verify_rpm(commands, path, kernel, *, source):
    text = commands.run(["rpm", "-Kv", path]).stdout
    check_signature(text, kernel.signing_key)
    package = package_info(commands, path)
    require(package.version == kernel.version and package.release == kernel.release,
            f"RPM version mismatch: {package.nevra}; expected {kernel.version}-{kernel.release}")
    if source:
        # Source RPMs can store the target architecture, not the literal "src".
        require(package.name == "kernel" and package.source_package == "1" and
                package.arch in (kernel.arch, "src", "nosrc"), "Expected a kernel source RPM")
        require(path.name == kernel.source_rpm,
                "Source RPM filename must equal the installed package's SOURCERPM")
    else:
        require(package.arch == kernel.arch and package.source_rpm == kernel.source_rpm,
                "Debug-source RPM does not belong to the installed kernel build")
        require(package.name in (f"kernel-debuginfo-common-{kernel.arch}", "kernel-debugsource"),
                "Expected Fedora's matching kernel debug-source package")
    return package


def file_digests(commands, target, *, installed=False):
    text = commands.run([
        "rpm", "-q" if installed else "-qp", "--qf", "[%{FILENAMES}\\t%{FILEDIGESTS}\\n]", target,
    ]).stdout
    result = {}
    for line in text.splitlines():
        name, _, digest = line.partition("\t")
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest) and int(digest, 16):
            result[name] = digest.lower()
    return result


def properties(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def select_device(sysroot=Path("/sys"), requested=None):
    matches = []
    for path in (sysroot / "bus/hid/devices").iterdir():
        if requested and path.name != requested:
            continue
        driver = path / "driver"
        if not driver.is_symlink():
            continue
        bound_driver = driver.resolve().name
        if bound_driver == DRIVER:
            version = sysroot / "module" / MODULE / "version"
            require(version.is_file() and version.read_text().strip() in
                    ("d4f767712c-local1", MODULE_VERSION),
                    "An unrecognized trial-driver version is already active; refusing to guess")
        elif bound_driver != "magicmouse":
            continue
        props = properties(path / "uevent")
        match = re.fullmatch(r"(001C|0019):([0-9A-Fa-f]{8}):([0-9A-Fa-f]{8})", props.get("HID_ID", ""))
        if match and int(match.group(2), 16) == 0x05AC:
            matches.append({
                "instance": path.name, "name": props.get("HID_NAME", ""),
                "hid_id": props["HID_ID"], "bus": int(match.group(1), 16),
                "vendor": int(match.group(2), 16), "product": int(match.group(3), 16),
                "bound_driver": bound_driver,
            })
    require(len(matches) == 1,
            f"Expected one internal Apple trackpad bound to magicmouse or the known trial driver, found {len(matches)}. "
            "Use --device with its exact HID instance if there is more than one.")
    # Keyboard devices can share all numeric IDs; their different driver excludes them.
    return matches[0]


def preflight(commands, requested_device=None):
    require(os.geteuid() != 0, "Run this builder as a normal user, never with sudo")
    require(sys.version_info >= (3, 11), "Python 3.11 or later is required")
    require(os.uname().machine == "aarch64", "This builder supports Apple Silicon/aarch64 only")
    missing = [name for name in TOOLS if not shutil.which(name)]
    require(not missing, "Missing build tools: " + ", ".join(missing) +
            ". Install prerequisites yourself; this script will not install packages.")
    release = os.uname().release
    require(re.fullmatch(r"[0-9][A-Za-z0-9_.+~-]+", release), "Unexpected kernel release string")
    candidates = [Path("/lib/modules") / release / "vmlinuz", Path("/boot") / ("vmlinuz-" + release)]
    image = next((p for p in candidates if p.is_file()), None)
    require(image, "Cannot find the running kernel release's installed vmlinuz")
    kernel = package_info(commands, image, file_owner=True)
    require(kernel.arch == "aarch64" and ".asahi." in kernel.release,
            "The running release is not an identifiable Fedora Asahi kernel package")
    require(kernel.source_rpm == f"kernel-{kernel.version}-{kernel.release}.src.rpm",
            "Unexpected source-RPM naming; refusing to guess a different kernel source")
    kernel.signing_key
    kernel.fedora
    digests = file_digests(commands, kernel.nevra, installed=True)
    wanted = {d for p, d in digests.items() if p.endswith(f"/modules/{release}/vmlinuz")}
    require(len(wanted) == 1 and sha256(image) in wanted,
            "Installed kernel image does not match its RPM's SHA-256")
    headers = (Path("/lib/modules") / release / "build").resolve()
    require((headers / "Makefile").is_file() and (headers / "Module.symvers").is_file(),
            f"Matching kernel-devel is missing. Install the exact capability: "
            f"kernel-devel-uname-r = {release} (not simply the newest kernel-devel).")
    header_package = package_info(commands, headers / "Makefile", file_owner=True)
    require(header_package.source_rpm == kernel.source_rpm, "Kernel headers are from a different source build")
    commands.run(["rpm", "-V", header_package.nevra])
    config = properties(headers / "include/config/auto.conf")
    require(config.get("CONFIG_MODULES") == "y" and config.get("CONFIG_HID_MAGICMOUSE") in ("y", "m"),
            "Running configuration does not support this external module")
    require(config.get("CONFIG_CC_IS_GCC") == "y", "Only GCC-built Fedora kernels are supported here")
    original_gcc = int(config.get("CONFIG_GCC_VERSION", "0"))
    gcc = commands.run(["gcc", "-dumpfullversion"]).stdout.strip()
    require(re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", gcc), "Cannot determine GCC version")
    numbers = list(map(int, gcc.split(".")))
    require(numbers[:2] == [original_gcc // 10000, original_gcc // 100 % 100],
            f"GCC major/minor differs from this kernel ({gcc} versus config {original_gcc}). "
            "Use matching build tools; this script will not replace your compiler.")
    return release, kernel, headers, image, select_device(requested=requested_device)


def download(url, destination):
    print(f"Downloading {destination.name}", flush=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    require(not destination.exists() and not partial.exists(), "Download destination already exists")
    try:
        with urllib.request.urlopen(url, timeout=120) as response, partial.open("xb") as stream:
            require(response.geturl().startswith("https://"), "Refusing a non-HTTPS redirect")
            shutil.copyfileobj(response, stream)
        partial.rename(destination)
    except urllib.error.URLError as error:
        raise Refused(f"Download failed: {url}\n{error}\n"
                      "Use --source-rpm / --debugsource-rpm with matching signed archived RPMs "
                      "if this old release is no longer hosted.") from error
    return destination


def acquire_rpm(supplied, filename, kernel, work):
    if supplied:
        path = Path(supplied).resolve()
        require(path.is_file(), f"RPM not found: {path}")
        return path
    base = (f"https://packages.redhat.com/api/pulp-content/public-copr/@asahi/kernel/"
            f"fedora-{kernel.fedora}-{kernel.arch}/Packages/k/")
    return download(base + filename, work / "inputs" / filename)


def source_files(commands, source_rpm):
    names = commands.run(["rpm", "-qpl", source_rpm]).stdout.splitlines()
    # Support Fedora's known monolithic downstream-patch layout, not arbitrary specs.
    archives = [n for n in names if re.fullmatch(r"linux-[A-Za-z0-9_.+-]+\.tar\.(xz|gz|bz2)", n)]
    downstream = [n for n in names if re.fullmatch(r"patch-[A-Za-z0-9_.+-]+-redhat\.patch", n)]
    require(len(archives) == 1 and len(downstream) == 1 and "kernel.spec" in names,
            "Unsupported source-RPM layout; refusing to reconstruct a guessed source tree")
    selected = [archives[0], downstream[0], "kernel.spec"]
    if "linux-kernel-test.patch" in names:
        selected.append("linux-kernel-test.patch")
    return selected


def extract_source_rpm(source_rpm, names, destination):
    # Extract only flat, whitelisted members; never run a downloaded RPM spec.
    require(all(re.fullmatch(r"[A-Za-z0-9_.+-]+", n) for n in names), "Unsafe source member name")
    log = destination.parent / "logs/extract.log"
    with log.open("w") as stream:
        producer = subprocess.Popen(["rpm2cpio", str(source_rpm)], stdout=subprocess.PIPE, stderr=stream)
        try:
            result = subprocess.run(
                ["cpio", "-id", "--no-absolute-filenames", "--no-preserve-owner", "--",
                 *names, *("./" + name for name in names)],
                stdin=producer.stdout, stdout=stream, stderr=stream, cwd=destination, timeout=300,
            )
        finally:
            producer.stdout.close()
            if producer.poll() is None:
                # After cpio finishes it has consumed the entire archive.
                try:
                    producer.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    producer.terminate()
                    producer.wait(timeout=10)
        require(result.returncode == 0 and producer.returncode == 0, "Source-RPM extraction failed; see logs/extract.log")
    require(all((destination / name).is_file() and not (destination / name).is_symlink() for name in names),
            "The source RPM did not supply the requested regular files")


def reconstruct(commands, source_dir, names, tree, expected_digest):
    spec = (source_dir / "kernel.spec").read_text()
    for name in re.findall(r"^Patch[0-9]*:\s*(\S+)", spec, re.M):
        require((name.startswith("patch-") and name.endswith("-redhat.patch")) or name == "linux-kernel-test.patch",
                f"Unsupported additional distro patch: {name}")
    archive = next(n for n in names if ".tar." in n)
    target = tree / "drivers/hid"
    target.mkdir(parents=True)
    # Make source the repository root even if output is inside somebody else's
    # checkout; git apply must never discover or modify a parent repository.
    commands.run(["git", "-c", "init.templateDir=", "init", "--quiet", tree])
    with tarfile.open(source_dir / archive, "r:*") as tar:
        for name in ("hid-magicmouse.c", "hid-ids.h"):
            members = [m for m in tar.getmembers() if m.name.endswith("/drivers/hid/" + name)]
            require(len(members) == 1 and members[0].isfile(), f"Missing or ambiguous source member: {name}")
            member = members[0]
            require(not PurePosixPath(member.name).is_absolute() and ".." not in PurePosixPath(member.name).parts,
                    "Unsafe tar member")
            with tar.extractfile(member) as src, (target / name).open("xb") as dest:
                shutil.copyfileobj(src, dest)
    for name in names:
        if name.endswith(".patch") and (source_dir / name).stat().st_size:
            commands.run([
                "git", "apply", "--include=drivers/hid/hid-magicmouse.c",
                "--include=drivers/hid/hid-ids.h", source_dir / name,
            ], cwd=tree)
    require(sha256(target / "hid-magicmouse.c") == expected_digest,
            "Reconstructed driver SHA-256 does not match the signed debug-source package. "
            "The distro preparation layout may have changed; refusing to build.")
    return target / "hid-magicmouse.c"


def apply_patch(commands, tree, patch):
    result = commands.run(["git", "apply", "--check", patch], cwd=tree, check=False)
    if result.returncode:
        reverse = commands.run(["git", "apply", "--reverse", "--check", patch], cwd=tree, check=False)
        if reverse.returncode == 0:
            return False
        raise Refused("The requested patch does not apply cleanly to this kernel. "
                      "No fuzz, forced application, unrelated upgrade or automatic backport is permitted.")
    commands.run(["git", "apply", patch], cwd=tree)
    return True


def replace_once(text, old, new):
    require(text.count(old) == 1, f"Unsupported driver layout: expected one {old!r}")
    return text.replace(old, new, 1)


def adapt_driver(text, device):
    require("HID_TYPE_SPI_MOUSE" in text, "Driver lacks the expected internal-trackpad HID type")
    text = replace_once(text, '#include "hid-ids.h"\n', '#include "hid-ids.h"\n\n'
                        '/* Manual trial only: no instance is selected by default. */\n'
                        'static char *target_hid = "";\n'
                        'module_param(target_hid, charp, 0400);\n'
                        'MODULE_PARM_DESC(target_hid, "Exact internal trackpad HID instance selected manually");\n')
    probe = re.search(r"static int magicmouse_probe\([^)]*\)\s*\{", text, re.S)
    require(probe, "Cannot identify magicmouse_probe")
    probe_end = text.find("\n}", probe.end())
    require(probe_end != -1, "Cannot identify the end of magicmouse_probe")
    first_if = re.search(r"^\tif \(", text[probe.end():probe_end], re.M)
    require(first_if, "Cannot identify a safe probe guard insertion point")
    position = probe.end() + first_if.start()
    text = (text[:position] +
            '\tif (!target_hid[0] || strcmp(dev_name(&hdev->dev), target_hid) ||\n'
            '\t    hdev->type != HID_TYPE_SPI_MOUSE)\n'
            '\t\treturn -ENODEV;\n\n' + text[position:])
    table = re.compile(r"^static const struct hid_device_id magic_mice\[\] = \{.*?^\};\n"
                       r"MODULE_DEVICE_TABLE\(hid, magic_mice\);", re.M | re.S)
    require(len(table.findall(text)) == 1, "Unsupported HID device-table layout")
    replacement = (
        "/* Only this model, with a separate exact-instance guard; no autoload aliases. */\n"
        "static const struct hid_device_id magic_mice[] = {\n"
        f"\t{{ HID_DEVICE(0x{device['bus']:04x}, HID_GROUP_ANY, "
        f"0x{device['vendor']:08x}, 0x{device['product']:08x}), .driver_data = 0 }},\n"
        "\t{ }\n};"
    )
    text = table.sub(lambda _: replacement, text, count=1)
    text = replace_once(text, '.name = "magicmouse",', f'.name = "{DRIVER}",')
    require("MODULE_VERSION(" not in text, "Unexpected pre-existing module version")
    return text + f'\nMODULE_VERSION("{MODULE_VERSION}");\n'


def verify_module(commands, module, headers, release):
    text = commands.run(["modinfo", module]).stdout
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        fields.setdefault(key, []).append(value.strip())
    require(fields.get("name") == [MODULE], "Wrong compiled module identity")
    require(fields.get("version") == [MODULE_VERSION], "Wrong compiled module version")
    require(fields.get("vermagic", [""])[0].split()[0] == release, "Module targets a different kernel")
    require("alias" not in fields, "Manual-only module unexpectedly has autoload aliases")
    header = commands.run(["readelf", "-h", module]).stdout
    require("AArch64" in header and "REL (Relocatable file)" in header, "Not an AArch64 kernel module")
    exported = {line.split()[1] for line in (headers / "Module.symvers").read_text().splitlines() if line.strip()}
    undefined = commands.run(["nm", "-u", module]).stdout
    missing = [p[-1] for line in undefined.splitlines() if (p := line.split()) and p[0] == "U" and p[-1] not in exported]
    require(not missing, "Unexported kernel symbols: " + ", ".join(missing))
    return {
        "sha256": sha256(module), "vermagic": fields["vermagic"][0],
        "srcversion": fields.get("srcversion", [""])[0], "dependencies": fields.get("depends", [""])[0],
        "unsigned": not bool(fields.get("signer")), "hardware_tested": False,
    }


def build(args, commands):
    release, kernel, headers, image, device = preflight(commands, args.device)
    summary = {"running_kernel": release, "kernel_package": asdict(kernel),
               "headers": str(headers), "installed_image_sha256": sha256(image), "device": device}
    print(json.dumps(summary, indent=2), flush=True)
    if args.check:
        print("Preflight passed. No sources downloaded, no compilation or system changes performed.")
        return
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work = Path(args.output or f"magicmouse-palm-{release}-{timestamp}").resolve()
    require(not re.search(r"\s", str(work)), "Kbuild requires an output path without whitespace")
    require(not work.exists(), f"Output must be a new directory: {work}")
    work.mkdir(parents=True)
    for name in ("inputs", "srpm", "module", "dist", "logs", "tmp"):
        (work / name).mkdir()
    commands.logdir = work / "logs"
    (work / "preflight.json").write_text(json.dumps(summary, indent=2) + "\n")
    source = acquire_rpm(args.source_rpm, kernel.source_rpm, kernel, work)
    debugname = f"kernel-debuginfo-common-{kernel.arch}-{kernel.version}-{kernel.release}.{kernel.arch}.rpm"
    debug = acquire_rpm(args.debugsource_rpm, debugname, kernel, work)
    print("Verifying matching source/debug RPM signatures and digests...", flush=True)
    verify_rpm(commands, source, kernel, source=True)
    verify_rpm(commands, debug, kernel, source=False)
    expected = [d for p, d in file_digests(commands, debug).items() if p.endswith(DRIVER_SUFFIX)]
    require(len(expected) == 1, "Debug-source RPM lacks a unique driver SHA-256")
    names = source_files(commands, source)
    extract_source_rpm(source, names, work / "srpm")
    tree = work / "source"
    driver = reconstruct(commands, work / "srpm", names, tree, expected[0])
    baseline = driver.read_text()
    (work / "inputs/baseline-hid-magicmouse.c").write_text(baseline)
    patch = work / "inputs/d4f767712c.patch"
    if args.patch_file:
        shutil.copyfile(Path(args.patch_file).resolve(), patch)
    else:
        download(PATCH_URL, patch)
    require(sha256(patch) == PATCH_SHA256, "Patch SHA-256 mismatch; refusing different patch content")
    if not apply_patch(commands, tree, patch):
        (work / "result.json").write_text(json.dumps({
            **summary, "status": "patch-already-present", "built": False,
        }, indent=2) + "\n")
        print("Your current driver already contains this exact patch. No replacement module was built.")
        return
    patched = driver.read_text()
    local = adapt_driver(patched, device)
    (work / "inputs/upstream-patched-hid-magicmouse.c").write_text(patched)
    (work / "inputs/local-adaptation.patch").write_text("".join(difflib.unified_diff(
        patched.splitlines(keepends=True), local.splitlines(keepends=True),
        fromfile="upstream-patched/hid-magicmouse.c", tofile=f"module/{MODULE}.c",
    )))
    (work / "module" / (MODULE + ".c")).write_text(local)
    shutil.copyfile(driver.parent / "hid-ids.h", work / "module/hid-ids.h")
    (work / "module/Makefile").write_text("# SPDX-License-Identifier: GPL-2.0-or-later\nobj-m += " + MODULE + ".o\n")
    print("Building the single module in a read-only-host, network-isolated sandbox...", flush=True)
    commands.run([
        "bwrap", "--die-with-parent", "--unshare-net", "--ro-bind", "/", "/", "--dev", "/dev",
        "--bind", work, work, "--chdir", work, "--setenv", "TMPDIR", work / "tmp",
        shutil.which("make"), "-C", headers, f"M={work / 'module'}", f"-j{args.jobs}", "modules",
    ], timeout=1800)
    module = work / "dist" / (MODULE + ".ko")
    shutil.copyfile(work / "module" / module.name, module)
    artifact = verify_module(commands, module, headers, release)
    # Confirm neither the installed image nor the RPM-owned build headers changed.
    require(sha256(image) == summary["installed_image_sha256"], "Installed image changed during the build")
    header_package = package_info(commands, headers / "Makefile", file_owner=True)
    commands.run(["rpm", "-V", header_package.nevra])
    result = {
        **summary, "status": "built-not-loaded", "built": True, "patch_commit": COMMIT,
        "patch_sha256": PATCH_SHA256, "baseline_driver_sha256": expected[0],
        "source_rpm": str(source), "source_rpm_sha256": sha256(source),
        "debugsource_rpm": str(debug), "debugsource_rpm_sha256": sha256(debug),
        "driver_name": DRIVER, "module_name": MODULE, "module_version": MODULE_VERSION,
        "module": str(module), **artifact,
        "compiler": commands.run(["gcc", "--version"]).stdout.splitlines()[0],
        "original_compiler": (headers / "include/generated/compile.h").read_text(),
    }
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    (work / "dist/SHA256SUMS").write_text(f"{artifact['sha256']}  {module.name}\n")
    (work / "DO-NOT-INSTALL.txt").write_text(
        "Built only. This is NOT a new kernel image or an installed driver.\n"
        "The existing driver binding was not changed. No persistent changes were made.\n"
        "Do not use force-loading, blacklist the original driver, or unbind a parent SPI device.\n"
        "The module has no autoload aliases and refuses every HID instance unless target_hid\n"
        "is explicitly supplied at manual load time. The exact-instance guard and HID type\n"
        "check prevent it from claiming the keyboard, even where numeric IDs are shared.\n"
        f"Built for: {release}\nSelected trackpad during build: {device['instance']}\n"
        "Loading and rebinding require a separate reviewed, reversible trial procedure.\n"
        "Do not copy a loader hardcoded for somebody else's kernel/device/version.\n"
        "It is unsigned/out-of-tree, may add kernel taint and can crash/hang the system.\n"
        "Kernel taint persists until reboot even after a driver rollback.\n"
    )
    print(f"\nSUCCESS: {module}\nSHA-256: {artifact['sha256']}\n"
          f"Details and warnings: {work / 'result.json'} and {work / 'DO-NOT-INSTALL.txt'}\n"
          "Nothing was installed, loaded, rebound or made persistent.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="read-only preflight only; no download/build")
    parser.add_argument("--output", help="new output directory (no whitespace; never overwrite an existing directory)")
    parser.add_argument("--source-rpm", help="optional matching signed kernel source RPM")
    parser.add_argument("--debugsource-rpm", help="optional matching signed debug-source RPM")
    parser.add_argument("--patch-file", help="optional cached patch; still checked against the pinned SHA-256")
    parser.add_argument("--device", help="exact internal HID instance; default: discover the one bound to magicmouse")
    parser.add_argument("--jobs", type=int, default=2, help="compile jobs, default 2")
    args = parser.parse_args(argv)
    require(1 <= args.jobs <= 64, "--jobs must be between 1 and 64")
    build(args, Commands())


if __name__ == "__main__":
    try:
        main()
    except (Refused, OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as error:
        print(f"\nSTOPPED: {error}", file=sys.stderr)
        print("No installation or driver activation was attempted.", file=sys.stderr)
        raise SystemExit(1)
