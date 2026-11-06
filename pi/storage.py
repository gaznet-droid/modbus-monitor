"""Pi microSD storage. Refuse volatile storage or filesystem replacement."""
import contextlib
import os
from pathlib import Path
import shutil

class Storage:
    def __init__(self, path="/var/lib/modpi/data"):
        self.path = Path(path)
        self.device = self.path.stat().st_dev

    def check(self):
        if self.path.is_symlink() or not self.path.is_dir():
            raise RuntimeError("ModPi data directory unavailable")
        if self.path.stat().st_dev != self.device or self.device != os.stat("/").st_dev:
            raise RuntimeError("Expected unchanged Pi root filesystem")
        # Check the mount holding the data directory: under ProtectSystem=strict "/" is
        # read-only and only the ReadWritePaths bind mount is writable.
        rows = [line.split() for line in Path("/proc/mounts").read_text().splitlines()]
        data = os.path.abspath(self.path)
        covering = [row for row in rows if data == row[1] or data.startswith(row[1].rstrip("/") + "/")]
        mount = sorted(covering, key=lambda row: len(row[1]))[-1] if covering else None
        if mount is None or mount[2] != "ext4" or "rw" not in mount[3].split(","):
            raise RuntimeError("Pi data filesystem is not writable ext4")
        if shutil.disk_usage(self.path).free < 64 * 1024 * 1024:
            raise RuntimeError("Less than 64 MiB free; collection paused")

    @contextlib.contextmanager
    def directory(self):
        self.check()
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.fstat(fd).st_dev != self.device:
                raise RuntimeError("Data filesystem changed")
            yield fd
        finally:
            os.close(fd)
