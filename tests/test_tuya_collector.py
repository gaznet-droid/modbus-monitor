"""Tuya integration unit tests; never calls Tuya or the router."""
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1] / "router"))
import tuya_collector as tuya

class TuyaCollectorTest(unittest.TestCase):
    def test_scale_and_keep_original_codes(self):
        output,raw=tuya.decode_status([{"code":"cur_voltage","value":2639},
                                       {"code":"cur_current","value":611},
                                       {"code":"cur_power","value":1613}])
        self.assertEqual(output,{"voltage_v":26.39,"current_a":0.611,"power_w":16.13})
        self.assertEqual(raw["cur_voltage"],2639)

    def test_no_fabricated_missing_values(self):
        output,_=tuya.decode_status([{"code":"cur_voltage","value":3430}])
        self.assertEqual(output,{"voltage_v":34.3,"current_a":None,"power_w":None})

    def test_reject_malformed_and_empty(self):
        for sample in ([], [{"code":"switch","value":True}],
                       [{"code":"cur_voltage","value":float('nan')}],
                       [{"code":"cur_voltage","value":34}, {"code":"cur_voltage","value":35}]):
            with self.assertRaises(ValueError): tuya.decode_status(sample)

    def test_api_forbids_writes(self):
        cloud=tuya.TuyaCloud("id","secret",transport=lambda *a,**kw:None)
        for path in ("/v1.0/devices/demo/commands", "/v1.0/devices/demo/status?abc=1", "/v1.0/space/1"):
            with self.assertRaises(ValueError): cloud.get(path)

    def test_storage_guard_and_roundup(self):
        checks=[]
        with tempfile.TemporaryDirectory() as path:
            db=pathlib.Path(path)/"test.sqlite3"
            store=tuya.USBStore(db,lambda: checks.append(True))
            output={"voltage_v":26.39,"current_a":0.611,"power_w":16.13}
            try:
                store.add("shed",1730000000000,output,{"cur_voltage":2639})
                store.add("shed",1730000000000,output,{"cur_voltage":2639})
                with store.db:
                    self.assertEqual(store.db.execute("SELECT count(*) FROM samples").fetchone()[0],1)
                    self.assertEqual(store.db.execute("SELECT count(*) FROM rollups").fetchone()[0],2)
                self.assertGreaterEqual(len(checks),3)
            finally:store.close()

    def test_disallows_fast_cloud_polling(self):
        with tempfile.TemporaryDirectory() as path:
            cfg=pathlib.Path(path)/"private.json"
            cfg.write_text(json.dumps({"client_id":"id","client_secret":"secret","interval_seconds":60,"devices":{"shed":"aaaaaaaaaaaaaaaaaaaaaaaa"}}))
            cfg.chmod(0o600)
            with self.assertRaises(ValueError):tuya.load_config(cfg)
            cfg.chmod(0o644)
            with self.assertRaises(RuntimeError):tuya.load_config(cfg)

if __name__=="__main__":unittest.main()
