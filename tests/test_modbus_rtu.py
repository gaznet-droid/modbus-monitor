import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'router'))
sys.path.insert(0, str(Path(__file__).parents[1] / 'pi'))
import modbus_rtu as rtu
import monitor
import run


def frame(body):
    return body + rtu.crc16(body)


class FakeMeter:
    """The other end of a pseudo-terminal: answers one request with whatever the test supplies."""
    def __init__(self, test, replies):
        self.master, slave = os.openpty()
        self.path = os.ttyname(slave)
        self.requests, self.replies = [], list(replies)
        test.addCleanup(os.close, self.master); test.addCleanup(os.close, slave)
        self.thread = threading.Thread(target=self.serve, daemon=True); self.thread.start()

    def serve(self):
        try:
            while self.replies:
                request = b''
                while len(request) < 8:
                    request += os.read(self.master, 8 - len(request))
                self.requests.append(request)
                for part in self.replies.pop(0):
                    os.write(self.master, part)
        except OSError:
            pass


class ModbusRtuTests(unittest.TestCase):
    def bus(self, *replies):
        fake = FakeMeter(self, replies)
        bus = rtu.SerialBus(fake.path, 9600, timeout=0.5)
        self.addCleanup(bus.close)
        return fake, bus

    def test_checksum_matches_the_standard_example(self):
        self.assertEqual(rtu.crc16(bytes.fromhex('01030000000A')), bytes.fromhex('C5CD'))

    def test_reads_power_voltage_and_kwh_with_read_only_requests_through_the_monitor(self):
        answer = lambda fc, value: frame(struct.pack('>BBBf', 1, fc, 4, value))
        power, volts, kwh = answer(4, -123.5), answer(3, 241.25), answer(4, 5321.75)
        fake, bus = self.bus([power[:2], power[2:]], [volts], [kwh])   # first reply arrives in two pieces
        self.assertEqual(monitor.read_power(bus), -123.5)
        self.assertEqual(monitor.VoltageReader(bus).sample()['volts'], 241.25)
        self.assertEqual(monitor.read_combined_kwh(bus), 5321.75)
        self.assertEqual(fake.requests, [frame(struct.pack('>BBHH', 1, 4, 146, 2)),
                                         frame(struct.pack('>BBHH', 1, 3, 16, 2)),
                                         frame(struct.pack('>BBHH', 1, 4, 1792, 2))])

    def test_adapter_echo_of_our_own_request_is_skipped(self):
        request = frame(struct.pack('>BBHH', 1, 4, 146, 2))
        fake, bus = self.bus([request, frame(struct.pack('>BBBf', 1, 4, 4, 42.0))])
        self.assertEqual(bus.read_input_float(), 42.0)

    def test_bad_answers_are_errors_never_readings(self):
        good = frame(struct.pack('>BBBf', 1, 4, 4, 42.0))
        cases = [[good[:-1] + bytes([good[-1] ^ 1])],                    # corrupt checksum
                 [frame(struct.pack('>BBBf', 2, 4, 4, 42.0))],            # another unit answered
                 [frame(struct.pack('>BBB', 1, 0x84, 2))],                # meter's own refusal
                 [frame(struct.pack('>BBBf', 1, 4, 4, float('nan')))]]
        for reply in cases:
            _, bus = self.bus(reply)
            with self.assertRaises(ValueError):
                bus.read_input_float()
        _, bus = self.bus([good[:4]])                                     # meter goes quiet mid-answer
        with self.assertRaises(OSError):
            bus.read_input_float()
        for bad in (dict(function=6), dict(function=16), dict(unit=0)):   # no writes, no broadcast
            with self.assertRaises(ValueError):
                bus.read_input_float(**bad)

    def test_recovers_after_a_silent_meter_and_a_missing_port(self):
        good = frame(struct.pack('>BBBf', 1, 4, 4, 7.5))
        _, bus = self.bus([], [good])
        with self.assertRaises(OSError):
            bus.read_input_float()
        self.assertEqual(bus.read_input_float(), 7.5)
        with self.assertRaises(OSError):
            rtu.SerialBus('/dev/does-not-exist').read_input_float()

    def test_pi_config_accepts_serial_instead_of_a_host(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'monitor.json'
            def load(**config):
                path.write_text(json.dumps(config)); return run.load_config(path)
            self.assertEqual(load(meter_enabled=True, meter_serial='/dev/serial0')['meter_serial'], '/dev/serial0')
            for bad in (dict(meter_serial='serial0'), dict(meter_serial=5), dict(meter_baud=1234),
                        dict(meter_baud='9600'), dict(meter_enabled=True)):
                with self.assertRaises(ValueError):
                    load(**bad)


if __name__ == '__main__':
    unittest.main()
