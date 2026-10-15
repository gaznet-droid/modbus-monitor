import importlib.util
import socket
import struct
import tempfile
import threading
import unittest
import json
import urllib.request
import urllib.error
import time
from pathlib import Path

spec=importlib.util.spec_from_file_location('monitor',Path(__file__).parents[1]/'router/monitor.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class MonitorTests(unittest.TestCase):
    def test_public_http_is_read_only_and_does_not_serve_files(self):
        with tempfile.TemporaryDirectory() as folder:
            s=m.Store(str(Path(folder)/'test.db'))
            s.add(int(time.time()*1000),100)
            server=m.WebServer(('127.0.0.1',0),m.handler(s,Path(__file__).parents[1]/'router/web'))
            thread=threading.Thread(target=server.serve_forever);thread.start()
            base='http://127.0.0.1:'+str(server.server_address[1])
            try:
                with urllib.request.urlopen(base+'/health') as response:
                    self.assertTrue(json.load(response)['ok'])
                for path,code in [('/etc/modbus-monitor.json',404),('/monitor.sqlite3',404),('/api/history?window=invalid',400)]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base+path)
                    self.assertEqual(error.exception.code,code)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(base+'/api/live',data=b'{}',method='POST'))
                self.assertEqual(error.exception.code,501)
            finally:
                server.shutdown();thread.join();server.server_close();s.db.close()

    def test_zero_crossing_and_outage_energy(self):
        imp,exp,coverage=m.split_energy(100,-100,3)
        self.assertAlmostEqual(imp,75/3600)
        self.assertAlmostEqual(exp,75/3600)
        self.assertEqual(coverage,3)
        self.assertEqual(m.split_energy(1000,1000,60),(0,0,0))

    def test_database_persistence_rollups_gaps_and_storage_loss(self):
        ready=[True]
        def guard():
            if not ready[0]:raise RuntimeError('USB missing')
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'test.db')
            s=m.Store(path,guard)
            base=1700000000000
            for i in range(10):s.add(base+i*3000,3600)
            s.add(base+60000,3600)
            self.assertFalse(s.add(base+60000,3600))
            live=s.live(now=base+60000)
            self.assertAlmostEqual(live['latest']['import_wh'],27)
            self.assertTrue(s.live(now=base+80000)['stale'])
            for window in m.WINDOWS:
                result=s.history(window,now=base+60000)
                self.assertGreater(len(result['points']),0)
                self.assertLessEqual(len(result['points']),1001)
            s.db.close()
            s=m.Store(path,guard)
            self.assertAlmostEqual(s.live(now=base+60000)['latest']['import_wh'],27)
            ready[0]=False
            with self.assertRaises(RuntimeError):s.add(base+63000,100)
            with self.assertRaises(RuntimeError):s.history('1d')
            s.db.close()

    def test_modbus_read_only_protocol_and_fragmented_response(self):
        listener=socket.socket()
        listener.bind(('127.0.0.1',0));listener.listen()
        observed=[]
        def meter():
            with listener:
                conn,_=listener.accept()
                with conn:
                    request=m.receive_exact(conn,12);observed.append(request)
                    tid=struct.unpack('>H',request[:2])[0]
                    reply=struct.pack('>HHHBBBf',tid,0,7,1,4,4,-123.5)
                    conn.sendall(reply[:4]);conn.sendall(reply[4:])
        thread=threading.Thread(target=meter);thread.start()
        value=m.read_power('127.0.0.1',listener.getsockname()[1])
        thread.join()
        self.assertEqual(value,-123.5)
        self.assertEqual(observed[0][7:],struct.pack('>BHH',4,146,2))

if __name__=='__main__':unittest.main()
