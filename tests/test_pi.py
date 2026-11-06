"""Pi configuration, storage faults and disabled-collector startup."""
import contextlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

PI = Path(__file__).parents[1] / "pi"
sys.path.insert(0, str(PI))
spec = importlib.util.spec_from_file_location("pi_runner", PI / "run.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

class PiTests(unittest.TestCase):
    def load(self, config):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps(config))
            return r.load_config(path)

    def test_default_config_disables_collectors(self):
        config = r.load_config(PI / "monitor.example.json")
        self.assertIs(config["meter_enabled"], False)
        self.assertIs(config["ewelink_enabled"], False)

    def test_activation_requires_a_meter_host(self):
        with self.assertRaises(ValueError):
            self.load({"meter_enabled": True})
        self.assertTrue(self.load({"meter_enabled": True, "meter_host": "192.0.2.1"})["meter_enabled"])

    def test_rejects_invalid_flags_ports_and_public_bind(self):
        for config in ({"meter_enabled": "false"}, {"port": 0},
                       {"port": True}, {"meter_port": 65536}, {"bind": "0.0.0.0"}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.load(config)

    def test_storage_refuses_missing_and_symlink_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder) / "data"
            data.mkdir()
            storage = r.Storage(data)
            data.rmdir()
            with self.assertRaises(RuntimeError):
                storage.check()
            data.symlink_to(folder, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                storage.check()

    def test_storage_refuses_volatile_readonly_and_full_filesystems(self):
        with tempfile.TemporaryDirectory() as folder:
            storage = r.Storage(folder)
            # Make this independent of the development host's root filesystem.
            real_stat = r.os.stat if hasattr(r, "os") else None
            from storage import os as storage_os
            root = storage_os.stat("/")
            storage.device = root.st_dev
            with patch("storage.Path.stat", return_value=root), patch("storage.Path.is_symlink", return_value=False):
                for mounts in ("tmpfs / tmpfs rw 0 0", "/dev/root / ext4 ro 0 0"):
                    with patch("storage.Path.read_text", return_value=mounts), self.assertRaises(RuntimeError):
                        storage.check()
                with patch("storage.Path.read_text", return_value="/dev/root / ext4 rw 0 0"):
                    with patch("storage.shutil.disk_usage", return_value=Mock(free=0)), self.assertRaises(RuntimeError):
                        storage.check()
                # systemd ProtectSystem=strict: read-only root, writable data bind mount
                sandboxed = f"/dev/root / ext4 ro 0 0\n/dev/root {folder} ext4 rw 0 0"
                with patch("storage.Path.read_text", return_value=sandboxed):
                    storage.check()
                readonly_data = f"/dev/root / ext4 rw 0 0\n/dev/root {folder} ext4 ro 0 0"
                with patch("storage.Path.read_text", return_value=readonly_data), self.assertRaises(RuntimeError):
                    storage.check()

    def test_tuya_collector_uses_injected_pi_storage(self):
        sys.path.insert(0, str(PI.parent / "router"))
        import tuya_collector as t
        calls = []
        @contextlib.contextmanager
        def directory():
            calls.append("directory")
            yield 7
        guard = Mock()
        conf = {"client_id": "id", "client_secret": "secret", "devices": {"shed": "x"}}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(sys, "argv", ["tuya_run", "--once", "--lock-file", folder + "/tuya.lock"]), \
             patch.object(t, "load_config", return_value=conf), \
             patch.object(t, "TuyaCloud"), \
             patch.object(t, "collect_once", return_value=1), \
             patch.object(t, "USBStore") as store:
            with self.assertRaises(SystemExit) as stopped:
                t.main(directory, guard)
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(calls, ["directory"])
        store.assert_called_once_with("/proc/self/fd/7/tuya.sqlite3", guard)

    def test_startup_with_disabled_collectors_starts_no_polling_threads(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text('{"meter_enabled":false,"ewelink_enabled":false}')
            lock = Path(folder) / "process.lock"
            real_open = open
            def opening(path, *args, **kwargs):
                if path == "/var/lib/modpi/private/process.lock":
                    return real_open(lock, *args, **kwargs)
                return real_open(path, *args, **kwargs)
            storage = Mock()
            storage.directory.side_effect = lambda: contextlib.nullcontext(123)
            with patch.object(sys, "argv", ["run.py", "--config", str(config)]), \
                 patch("builtins.open", side_effect=opening), \
                 patch.object(r, "Storage", return_value=storage), \
                 patch.object(r.monitor, "Store"), patch.object(r.monitor, "WebServer"), \
                 patch.object(r, "CachedTuya"), patch.object(r, "Ewelink") as ewelink, \
                 patch.object(r.threading, "Thread") as thread, patch.object(r.signal, "signal"):
                r.main()
                thread.assert_not_called()
                ewelink.return_value.poll.assert_not_called()

if __name__ == "__main__":
    unittest.main()
