import json
import os
from pathlib import Path
import tempfile
import unittest

import network
import ewelink_local as L
from test_ewelink_local import announcement, plain_txt, Clock

TCP = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000:3A99 00000000:0000 0A 00000000:00000000 00:00000000 00000000   999        0 1 1 x
   1: F207A8C0:3A99 CF07A8C0:C001 01 00000000:00000000 02:000A 00000000   999        0 2 1 x
   2: 0100007F:1FA0 0100007F:D431 01 00000000:00000000 00:00000000 00000000   999        0 3 1 x
"""


class FakeEwelink:
    def config(self):
        return {"devices": {"fridge": {"id": "1000aaaaaa", "item_type": 1}, "inverter-600": "1000bbbbbb"}}
    def home_mapping(self):
        return {"lounge-temp": {"id": "a480000001"}, "tv": {"id": "1000cccccc"}, "socket": {"id": "1000dddddd"}}


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.table = self.root / "tcp"; self.table.write_text(TCP)
    def tearDown(self):
        self.tmp.cleanup()
    def private(self, name, value):
        path = self.root / name; path.write_text(json.dumps(value)); os.chmod(path, 0o600); return str(path)

    def test_connected_peer_is_the_established_lan_connection(self):
        self.assertEqual(network.connected_peer(15001, self.table), "192.168.7.207")
        self.assertIsNone(network.connected_peer(8096, self.table))    # loopback is not a device
        self.assertIsNone(network.connected_peer(15002, self.table))
        self.assertIsNone(network.connected_peer(15001, self.root / "missing"))

    def test_addresses_never_carry_device_ids_or_public_addresses(self):
        juwai = self.private("juwai.json", {"devices": {"shed": {"host": "192.168.7.108", "version": "3.4"},
                                                         "garage": {"host": "2.98.153.184", "version": "3.4"}}})
        epever = self.private("epever.json", {"name": "Epever", "port": 15001})
        seen = {"1000aaaaaa": ("192.168.4.23", "plug"), "1000cccccc": ("192.168.4.49", "strip"),
                "10027da5ce": ("192.168.7.243", "zigbee_gateway")}
        out = network.addresses(FakeEwelink(), seen, juwai, epever, self.table)
        self.assertEqual(out, {"solar": {"shed": "192.168.7.108", "garage": None}, "epever": "192.168.7.207",
            "plugs": {"fridge": "192.168.4.23", "inverter-600": None}, "bridge": "192.168.7.243",
            "home": {"lounge-temp": {"ip": None, "zigbee": True}, "tv": {"ip": "192.168.4.49", "zigbee": False},
                     "socket": {"ip": None, "zigbee": False}}})
        text = json.dumps(out)
        for secret in ("1000aaaaaa", "a480000001", "10027da5ce"):
            self.assertNotIn(secret, text)

    def test_missing_or_loose_files_give_empty_answers(self):
        loose = self.root / "loose.json"; loose.write_text('{"devices":{"shed":{"host":"192.168.7.108"}}}'); os.chmod(loose, 0o644)
        out = network.addresses(None, None, str(loose), str(self.root / "none.json"), self.table)
        self.assertEqual(out, {"solar": {}, "epever": None, "plugs": {}, "home": {}, "bridge": None})

    def test_listener_remembers_where_each_announcement_came_from(self):
        clock = Clock()
        local = L.EwelinkLocal(None, "unused", sock=object(), clock=clock)
        local.handle(announcement("1000eeeeee", dict(plain_txt({"switch": "on"}), type="plug"), ip="192.168.7.231"))
        self.assertEqual(local.addresses(), {"1000eeeeee": ("192.168.7.231", "plug")})
        clock.t += 901
        self.assertEqual(local.addresses(), {})                         # not heard for 15 minutes


if __name__ == "__main__":
    unittest.main()
