import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pi"))
import epever_tap

TAP = os.path.join(os.path.dirname(__file__), "..", "pi", "epever_tap.py")


def reply(words, function=4):
    body = bytes([1, function, len(words) * 2]) + b"".join(w.to_bytes(2, "big") for w in words)
    return body + epever_tap.crc16(body)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FindReadReplies(unittest.TestCase):
    def test_finds_reply_inside_other_bytes(self):
        data = b"\x30\x1a\x00\x05topic" + reply([0x0E10, 0x0064]) + b"\xff"
        self.assertEqual(epever_tap.find_read_replies(data), [(4, [0x0E10, 0x0064])])

    def test_ignores_bad_crc_and_requests(self):
        bad = bytearray(reply([1, 2]))
        bad[-1] ^= 0xFF
        request = bytes([1, 4, 0x31, 0x00, 0, 4])
        request += epever_tap.crc16(request)
        self.assertEqual(epever_tap.find_read_replies(bytes(bad) + request), [])


class RelayPassesBytesUnchanged(unittest.TestCase):
    def test_relay_both_ways_and_logs(self):
        upstream = socket.socket()
        upstream.bind(("127.0.0.1", 0))
        upstream.listen(1)
        port = free_port()
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "tap.log")
            proc = subprocess.Popen([sys.executable, TAP, "relay", "--bind", "127.0.0.1", "--port", str(port),
                                     "--upstream", "127.0.0.1:%d" % upstream.getsockname()[1], "--log", log])
            try:
                for _ in range(50):
                    try:
                        dongle = socket.create_connection(("127.0.0.1", port), 1)
                        break
                    except OSError:
                        time.sleep(0.1)
                upstream.settimeout(5)
                far, _ = upstream.accept()
                far.settimeout(5)
                dongle.settimeout(5)
                dongle.sendall(reply([0x1234]))
                self.assertEqual(far.recv(64), reply([0x1234]))
                far.sendall(b"poll")
                self.assertEqual(dongle.recv(64), b"poll")
                dongle.close()
                far.close()
                time.sleep(0.3)
            finally:
                proc.terminate()
                proc.wait(5)
                upstream.close()
            text = open(log).read()
            self.assertIn("dongle> 7 ", text)
            self.assertIn("[modbus fc04 1234]", text)
            self.assertIn("server> 4 ", text)
            self.assertEqual(os.stat(log).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
