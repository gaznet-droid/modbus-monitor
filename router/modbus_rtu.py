#!/usr/bin/env python3
"""Read-only Modbus RTU over a serial port (the Pi's RS485 HAT). Standard library only.

Only functions 03 and 04 (read registers) exist here; nothing can write to the meter.
The Pi must be the only master on the RS485 pair: unplug the Elfin before using this.
"""
import fcntl
import math
import os
import select
import struct
import sys
import termios
import threading
import time

BAUDS = {1200: termios.B1200, 2400: termios.B2400, 4800: termios.B4800, 9600: termios.B9600,
         19200: termios.B19200, 38400: termios.B38400}


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


class SerialBus:
    """One serial line, 8 data bits, no parity, 1 stop bit. One request at a time."""

    def __init__(self, path="/dev/serial0", baud=9600, timeout=1.0):
        if baud not in BAUDS:
            raise ValueError("Unsupported baud rate")
        self.path, self.baud, self.timeout = path, baud, timeout
        self.gap = max(0.005, 38.5 / baud)  # 3.5 character times of silence between frames
        self.lock = threading.Lock()
        self.fd = None
        self.quiet_since = 0.0

    def _open(self):
        fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # one reader of this port on the Pi
            attrs = termios.tcgetattr(fd)
            attrs[0] = attrs[1] = attrs[3] = 0                          # raw: no input, output or line processing
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL     # 8N1, ignore modem lines
            attrs[4] = attrs[5] = BAUDS[self.baud]
            attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 0, 0
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd

    def close(self):
        with self.lock:
            self._close()

    def _close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def _receive(self, count, deadline):
        data = bytearray()
        while len(data) < count:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                raise TimeoutError("No complete answer from the meter")
            chunk = os.read(self.fd, count - len(data))
            if not chunk:
                raise OSError("Serial port closed")
            data.extend(chunk)
        return bytes(data)

    def read_input_float(self, unit=1, register=146, function=4):
        """Read two registers as one big-endian float. Raises OSError or ValueError; never guesses."""
        if function not in (3, 4):
            raise ValueError("Only read functions are allowed")
        if not 1 <= unit <= 247:
            raise ValueError("Invalid Modbus unit")
        request = struct.pack(">BBHH", unit, function, register, 2)
        request += crc16(request)
        with self.lock:
            try:
                if self.fd is None:
                    self._open()
                wait = self.quiet_since + self.gap - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                termios.tcflush(self.fd, termios.TCIOFLUSH)  # drop anything left from an earlier exchange
                os.write(self.fd, request)
                termios.tcdrain(self.fd)
                deadline = time.monotonic() + self.timeout
                head = self._receive(5, deadline)
                if head == request[:5]:
                    # Some RS485 adapters hand our own request back first; skip it.
                    self._receive(len(request) - 5, deadline)
                    head = self._receive(5, deadline)
                if head[1] == function | 0x80:
                    if head[:1] == bytes((unit,)) and crc16(head[:3]) == head[3:]:
                        raise ValueError("Meter refused the read (Modbus exception %d)" % head[2])
                    raise ValueError("Invalid Modbus response")
                reply = head + self._receive(4, deadline)
            except OSError:
                self._close()  # reopen cleanly next time
                raise
            finally:
                self.quiet_since = time.monotonic()
        if crc16(reply[:7]) != reply[7:]:
            raise ValueError("Modbus checksum mismatch")
        if reply[:3] != bytes((unit, function, 4)):
            raise ValueError("Invalid Modbus register response")
        value = struct.unpack(">f", reply[3:7])[0]
        if not math.isfinite(value):
            raise ValueError("Non-finite reading")
        return value


if __name__ == "__main__":
    # Supervised check on fit day (stop modpi first; they cannot share the port):
    #   sudo -u modpi python3 /opt/modpi/router/modbus_rtu.py /dev/serial0 9600
    bus = SerialBus(sys.argv[1] if len(sys.argv) > 1 else "/dev/serial0",
                    int(sys.argv[2]) if len(sys.argv) > 2 else 9600)
    failed = 0
    for label, register, function in (("power W", 146, 4), ("voltage V", 16, 3), ("meter kWh", 1792, 4)):
        try:
            print(label, round(bus.read_input_float(1, register, function), 3))
        except (OSError, ValueError) as error:
            failed += 1
            print(label, "unavailable:", error)
        time.sleep(0.1)
    sys.exit(1 if failed else 0)
