#!/usr/local/usr/bin/python3
"""Mount the dedicated Modbus USB; refuse internal-storage fallback.
Future collectors must use storage_directory() and relative dir_fd file operations.
"""
import contextlib
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys

TARGET = Path("/usr/local/mnt/modbus-usb")
SERIAL = "19419302FB103BE4"
DATA = "modbus-data"

def device():
    found = []
    for part in Path("/sys/class/block").glob("*"):
        if not (part / "partition").exists():
            continue
        if (part / "partition").read_text().strip() != "1":
            continue
        for parent in part.resolve().parents:
            serial = parent / "serial"
            if serial.is_file() and serial.read_text().strip() == SERIAL:
                dev = Path("/dev") / part.name
                with dev.open("rb", buffering=0) as stream:
                    header = stream.read(128)
                if header[3:11] != b"EXFAT   ":
                    raise RuntimeError("Expected USB no longer has an exFAT filesystem")
                found.append(dev)
                break
    if len(found) != 1:
        raise RuntimeError("Dedicated Modbus USB absent or ambiguous; storage refused")
    return found[0]

def check():
    dev = device()
    rows = [line.split() for line in Path("/proc/mounts").read_text().splitlines()]
    rows = [r for r in rows if r[1] == str(TARGET)]
    if len(rows) != 1 or rows[0][2] != "exfat" or "rw" not in rows[0][3].split(","):
        raise RuntimeError("Dedicated USB is not mounted read-write; storage refused")
    if TARGET.stat().st_dev != dev.stat().st_rdev:
        raise RuntimeError("Mount does not match dedicated USB; storage refused")
    return dev

@contextlib.contextmanager
def storage_directory():
    """Yield a pinned USB directory fd. Use open(..., dir_fd=fd), never absolute writes."""
    dev = check()
    fd = os.open(TARGET / DATA, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(fd).st_dev != dev.stat().st_rdev:
            raise RuntimeError("Data directory is not on USB; storage refused")
        yield fd
    finally:
        os.close(fd)

def mount_usb():
    with open("/var/lock/modbus-usb.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        dev = device()
        TARGET.mkdir(parents=True, exist_ok=True)
        rows = [line.split() for line in Path("/proc/mounts").read_text().splitlines()]
        if not any(r[1] == str(TARGET) for r in rows):
            subprocess.run(["/bin/mount", "-t", "exfat", "-o",
                            "rw,noatime,nodev,nosuid,noexec", str(dev), str(TARGET)], check=True)
        check()
        root_fd = os.open(TARGET, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.fstat(root_fd).st_dev != dev.stat().st_rdev:
                raise RuntimeError("USB disappeared; refusing data directory creation")
            try:
                os.mkdir(DATA, dir_fd=root_fd)
            except FileExistsError:
                pass
            data_fd = os.open(DATA, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=root_fd)
            os.close(data_fd)
        finally:
            os.close(root_fd)
        check()
        print("Modbus USB mounted read-write; storage guard ready")

def selftest():
    import secrets
    name = "usb-test-" + secrets.token_hex(8)
    payload = b"Modbus USB verified\n"
    with storage_directory() as directory:
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            os.write(fd, payload)
            os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            if os.read(fd, len(payload)) != payload:
                raise RuntimeError("USB readback mismatch")
        finally:
            os.close(fd)
            os.unlink(name, dir_fd=directory)
    print("USB create/write/sync/read/delete test passed")

if __name__ == "__main__":
    try:
        action = sys.argv[1] if len(sys.argv) == 2 else "check"
        if action == "mount":
            mount_usb()
        elif action == "check":
            check()
            with storage_directory():
                pass
            print("USB storage ready")
        elif action == "selftest":
            selftest()
        else:
            raise RuntimeError("Usage: modbus_usb.py [mount|check|selftest]")
    except Exception as error:
        print("Modbus USB: " + str(error), file=sys.stderr)
        sys.exit(1)

