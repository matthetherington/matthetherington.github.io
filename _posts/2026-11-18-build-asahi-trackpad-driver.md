---
title: "Build a patched MacBook trackpad driver on Fedora Asahi"
description: "A copy-and-paste walkthrough for building the magicmouse palm-rejection patch without rebuilding the whole kernel."
categories: [Linux, Fedora Asahi]
tags: [asahi, trackpad, kernel]
---

This builds a standalone MacBook trackpad driver with
[Asahi Linux's palm-rejection and hover-filtering patch](https://github.com/AsahiLinux/linux/commit/d4f767712c8b0097e0996f951d3c9cea2648ee97).
It uses the source and development headers for your **currently running kernel**,
so you don't need to rebuild the whole kernel.

**Building does not install or activate the driver.** Your existing trackpad
driver stays active unless you opt into the boot setup at the end. The commands
below are for a normal Fedora Asahi installation on an Apple Silicon MacBook.

## 1. Matching kernel development headers

The development headers must match the kernel you're actually running, not just
the newest kernel available in the repositories. Open Konsole and run:

```bash
sudo dnf install -y "kernel-devel-uname-r = $(uname -r)"
```
{: .nolineno }

`$(uname -r)` supplies your running kernel's exact version automatically.
This installs its development package; it doesn't replace or reboot the running
kernel.

If DNF cannot find that exact version, **stop here rather than installing headers
for a different kernel**. Older versions may require archived packages. This
guide deliberately does not include a whole-system or kernel upgrade.

## 2. Build tools

Install the tools used to download and verify the source packages, apply the
patch, and compile the module:

```bash
sudo dnf install -y python3 gcc make git rpm cpio binutils kmod bubblewrap \
  elfutils-libelf-devel openssl-devel curl
```
{: .nolineno }

Run the check and build as your normal user, **not with `sudo`**. The separate
install and uninstall commands below need `sudo`.

## 3. Get the script

[Download the build script]({{ "/downloads/build-magicmouse-palm.py" | relative_url }})
or open that link to review it first.
The script is self-contained: you don't need a ZIP or a cloned repository.

To download it into its own working directory and check its SHA-256, copy this
whole block:

```bash
mkdir -p ~/asahi-trackpad-build
cd ~/asahi-trackpad-build
curl --fail --location --proto '=https' --tlsv1.2 \
  --output build-magicmouse-palm.py \
  {{ "/downloads/build-magicmouse-palm.py" | absolute_url }}
printf '%s  %s\n' '{{ site.data.downloads.magicmouse.sha256 }}' build-magicmouse-palm.py \
  | sha256sum --check -
```
{: .nolineno }

The last command should print `build-magicmouse-palm.py: OK`. Don't continue if
the download or checksum check fails.

The script is [licensed under the GPL]({{ "/downloads/LICENSE" | relative_url }}).

## 4. Check your system

From that same terminal and directory, run:

```bash
python3 build-magicmouse-palm.py --check
```
{: .nolineno }

This checks the kernel package, matching headers, compiler, and trackpad without
downloading sources or compiling anything. Continue only if it prints
`Preflight passed`.

## 5. Build the driver

```bash
python3 build-magicmouse-palm.py --output ./build
```
{: .nolineno }

Allow a few hundred megabytes of downloads and about 1 GB of working space. The
script verifies the source RPM signatures, checks the original driver source
against Fedora's packaged SHA-256, applies the requested patch, and builds a
separately named module. It stops rather than forcing an incompatible patch.

**If it says your current driver already contains the patch, you're done.** It
won't build an unnecessary replacement, and you can skip the next step.

## 6. Check the result

After a successful build, verify the new module's checksum:

```bash
cd ~/asahi-trackpad-build/build/dist
sha256sum --check SHA256SUMS
```
{: .nolineno }

It should print `hid_magicmouse_palm_test.ko: OK`. The built module is at:

```text
~/asahi-trackpad-build/build/dist/hid_magicmouse_palm_test.ko
```
{: .nolineno }

Build details and logs are in `~/asahi-trackpad-build/build/`.
The `build` directory must not already exist: if you're repeating the process,
use a new output name rather than overwriting a previous build.

## What happens next?

**Nothing has been activated.** If a packaged Fedora Asahi kernel already
includes the patch, use that instead; there is nothing to install here.

To use the module you just built across reboots, run the following after the
checksum check. Use only the verified script and your own build: this command
runs as root and installs code that will run in the kernel.

```bash
cd ~/asahi-trackpad-build
sudo python3 -I build-magicmouse-palm.py --install ./build
```
{: .nolineno }

This enables `magicmouse-palm.service` but leaves the current session alone.
Before rebooting, have an external mouse handy and make sure you can edit the
boot entry. The module is unsigned and can taint, crash, or hang the kernel.
Don't disable module-signature enforcement or blacklist the stock driver to
make it work.

On the next boot into the same kernel, the service looks for the matching
internal trackpad already bound to the stock driver, loads the patched module,
and switches only that HID device. If loading fails, it leaves the stock driver
alone. If switching fails, it attempts to restore the stock binding. The
service skips other kernel versions rather than trying to load an incompatible
module.

After rebooting, check that the journal reports `Patched driver bound to`:

```bash
journalctl -b -u magicmouse-palm.service
```
{: .nolineno }

If the trackpad stops responding or the machine hangs, reboot and append
`systemd.mask=magicmouse-palm.service` to the kernel command line in the boot
menu for that boot. Then remove the boot setup and reboot to use the stock driver:

```bash
cd ~/asahi-trackpad-build
sudo python3 -I build-magicmouse-palm.py --uninstall
```
{: .nolineno }

After a kernel update, keep the stock driver if the patch is included. If it
isn't and you still want the local patch, boot into the new kernel, build into a
new directory, uninstall the old boot setup, and install the new build.

The full download-and-build process has been tested on Fedora Asahi with kernel
`7.2.12-402.asahi.fc45.aarch64+16k`. Other kernel versions are detected
automatically, but may be rejected if their sources, toolchain, or driver APIs
are incompatible. The boot-time installer has not been tested on MacBook hardware.
