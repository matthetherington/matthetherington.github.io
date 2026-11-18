"""Exercise install and boot decisions without changing real drivers or services."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


spec = importlib.util.spec_from_file_location(
    "trackpad_builder", Path(__file__).resolve().parents[1] / "downloads/build-magicmouse-palm.py"
)
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)

RELEASE = "7.2.12-402.asahi.fc45.aarch64+16k"
IDENTITY = {"hid_id": "001C:000005AC:00000320", "name": "Apple Trackpad"}


class TrackpadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "config"
        self.directory = self.root / "extra" / "magicmouse-palm"
        self.module = self.directory / (builder.MODULE + ".ko")
        self.unit = self.root / builder.SERVICE
        self.commands = Mock()
        for name, replacement in (("STATE", self.state), ("UNIT", self.unit),
                                  ("module_dir", lambda _: self.directory)):
            self.enterContext(patch.object(builder, name, replacement))
        self.enterContext(patch.object(builder.os, "geteuid", return_value=0))
        self.uname = self.enterContext(patch.object(
            builder.os, "uname", return_value=SimpleNamespace(machine="aarch64", release=RELEASE)
        ))

    def fake_devices(self):
        self.sysroot = self.root / "sys"
        self.hid = self.sysroot / "bus/hid"
        self.stock = self.hid / "drivers/magicmouse"
        self.patched = self.hid / "drivers" / builder.DRIVER
        self.stock.mkdir(parents=True)
        self.patched.mkdir()
        self.device = self.add_device("001C:05AC:0320.0042")

    def add_device(self, instance):
        device = self.hid / "devices" / instance
        device.mkdir(parents=True)
        (device / "driver").symlink_to(self.stock)
        (device / "uevent").write_text(f"HID_ID={IDENTITY['hid_id']}\nHID_NAME={IDENTITY['name']}\n")
        return device

    def stage_config(self):
        self.state.mkdir()
        self.directory.mkdir(parents=True)
        self.module.write_bytes(b"module")
        (self.state / "config.json").write_text(json.dumps({
            "kernel": RELEASE, "device": IDENTITY, "sha256": builder.sha256(self.module),
        }))

    def test_install_enables_without_starting_and_uninstall_removes(self):
        build = self.root / "build"
        (build / "dist").mkdir(parents=True)
        data = b"module"
        digest = hashlib.sha256(data).hexdigest()
        (build / "dist" / self.module.name).write_bytes(data)
        (build / "result.json").write_text(json.dumps({
            "built": True, "status": "built-not-loaded", "patch_commit": builder.COMMIT,
            "running_kernel": RELEASE, "device": IDENTITY, "sha256": digest,
        }))
        with patch.object(Path, "is_dir", return_value=True), \
                patch.object(builder, "select_device", return_value=IDENTITY), \
                patch.object(builder, "verify_module", return_value={"sha256": digest}), \
                patch.object(builder.shutil, "which", return_value=None):
            builder.install(build, self.commands)
        self.assertEqual(self.module.read_bytes(), data)
        self.assertIn("--boot-apply", self.unit.read_text())
        self.assertEqual(self.commands.run.call_args_list, [
            call(["systemctl", "daemon-reload"]), call(["systemctl", "enable", builder.SERVICE]),
        ])
        self.commands.reset_mock()
        builder.uninstall(self.commands)
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.module.exists())
        self.assertFalse(self.state.exists())
        self.assertEqual(self.commands.run.call_args_list, [
            call(["systemctl", "disable", builder.SERVICE]), call(["systemctl", "daemon-reload"]),
        ])

    def test_boot_uses_current_instance_and_skips_other_kernel(self):
        self.fake_devices()
        self.stage_config()
        with patch.object(builder, "switch_trackpad") as switch:
            builder.boot_apply(self.commands, self.sysroot)
            switch.assert_called_once_with(self.commands, self.sysroot, self.device, self.module)
            switch.reset_mock()
            self.uname.return_value.release = "7.3.0"
            builder.boot_apply(self.commands, self.sysroot)
            switch.assert_not_called()

    def test_boot_refuses_ambiguous_device(self):
        self.fake_devices()
        self.add_device("001C:05AC:0320.0043")
        self.stage_config()
        with self.assertRaisesRegex(builder.Refused, "Ambiguous"):
            builder.boot_apply(self.commands, self.sysroot)
        self.commands.run.assert_not_called()

    def test_module_load_failure_leaves_stock_alone(self):
        self.fake_devices()
        self.commands.run.side_effect = builder.Refused("insmod failed")
        with patch.object(builder, "sysfs_action") as action, self.assertRaisesRegex(builder.Refused, "insmod"):
            builder.switch_trackpad(self.commands, self.sysroot, self.device, self.module)
        action.assert_not_called()

    def test_failed_bind_restores_stock(self):
        self.fake_devices()
        link = self.device / "driver"

        def action(driver, operation, instance):
            self.assertEqual(instance, self.device.name)
            if operation == "unbind":
                link.unlink()
            elif driver == self.patched:
                raise OSError("bind failed")
            else:
                link.symlink_to(driver)

        with patch.object(builder, "sysfs_action", side_effect=action), self.assertRaisesRegex(OSError, "bind failed"):
            builder.switch_trackpad(self.commands, self.sysroot, self.device, self.module)
        self.assertTrue(builder.bound_to(self.device, self.stock))
        self.commands.run.assert_called_with(["rmmod", builder.MODULE], check=False, timeout=15)
