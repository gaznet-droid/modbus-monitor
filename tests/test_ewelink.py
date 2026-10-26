import contextlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import sys
import sqlite3
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
import ewelink as e

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        @contextlib.contextmanager
        def directory():
            fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
            try:yield fd
            finally:os.close(fd)
        self.calls=[]
        def transport(region,method,path,body,headers):
            self.calls.append((region,method,path,json.loads(body) if body else None,headers))
            if path.endswith('token') or path.endswith('refresh'):
                return dict(accessToken='access',refreshToken='refresh',atExpiredTime=int(time.time()*1000)+2*86400000,rtExpiredTime=int(time.time()*1000)+3*86400000)
            return {'thingList':[{'itemType':2,'itemData':{'deviceid':'one','online':True,'params':{'power':'42.5','voltage':'230','current':'0.2','switch':'on','apikey':'private'}}}]}
        self.a=e.Ewelink(directory,lambda:None,self.root/'config.json',transport)
        if sys.platform=='darwin':
            # The router uses Linux /proc fd paths; Mac tests use the same schema
            # in an isolated directory. USB pinning is exercised on Linux/router.
            @contextlib.contextmanager
            def mac_database(readonly=False):
                self.a.guard()
                path=str(self.root/'ewelink.sqlite3')
                db=sqlite3.connect('file:'+path+'?mode=ro' if readonly else path,uri=readonly)
                try:
                    if not readonly:db.executescript(e.SCHEMA)
                    yield db
                finally:db.close()
            self.a.database=mac_database
        e.atomic_json(self.a.config_path,dict(appid='app',secret='secret',redirect_url=e.CALLBACK,app_expires_at=time.time()+86400,devices={'fridge':{'id':'one','item_type':2}}))
        self.a.save_tokens(transport('eu','POST','token',None,{}),'eu');self.calls.clear()
        self.a.last_request=-10
    def tearDown(self):self.temp.cleanup()
    def query(self):return parse_qs(urlsplit(self.a.authorize()).query)
    def test_signature_official_vector(self):self.assertEqual(e.sign('abc','ABC_123'),'v1+mfNY2ukxswM8sZOTg99srZsVnUVv9DGXeav1096M=')
    def test_oauth_one_use(self):
        q=self.query();self.a.callback(dict(state=q['state'],code=['code'],region=['eu']))
        with self.assertRaises(ValueError):self.a.callback(dict(state=q['state'],code=['code'],region=['eu']))
        self.assertEqual(len(self.calls),1)
    def test_state_not_stored_plain(self):
        q=self.query();self.assertNotIn(q['state'][0],self.a.pending_path.read_text())
    def test_wrong_state_does_not_consume(self):
        self.query()
        with self.assertRaises(ValueError):self.a.callback(dict(state=['bad'],code=['code'],region=['eu']))
        self.assertTrue(self.a.pending_path.exists());self.assertFalse(self.calls)
    def test_expired_state(self):
        q=self.query();p=e.private_json(self.a.pending_path);p['expires']=0;e.atomic_json(self.a.pending_path,p)
        with self.assertRaises(ValueError):self.a.callback(dict(state=q['state'],code=['code'],region=['eu']))
        self.assertFalse(self.calls)
    def test_bad_region(self):
        q=self.query()
        with self.assertRaises(ValueError):self.a.callback(dict(state=q['state'],code=['code'],region=['https://evil.test']))
        self.assertFalse(self.calls)
    def test_no_control_transport(self):
        for method,path in [('POST','/v2/device/thing/status'),('POST','/v2/device/thing/batch-status'),('DELETE','/v2/user/oauth/token')]:
            with self.assertRaises(ValueError):self.a.request('eu',method,path,{})
        self.assertFalse(self.calls)
    def test_batch_only_selected_shared_device(self):
        self.a.collect();self.assertEqual(self.calls[0][3],{'thingList':[{'itemType':2,'id':'one'}]})
        data=self.a.latest();self.assertEqual(len(data['devices']),5)
        fridge=data['devices'][-1];self.assertEqual(fridge['power_w'],42.5)
        self.assertNotIn('private',json.dumps(data));self.assertNotIn('access',json.dumps(data));self.assertNotIn('one',json.dumps(data))
    def test_restart_does_not_burst(self):
        self.a.collect();self.a.collect();self.assertEqual(len(self.calls),1)
    def test_public_read_does_not_make_network_calls(self):
        self.a.latest();self.assertFalse(self.calls);self.assertFalse((self.root/'ewelink.sqlite3').exists())
    def test_offline_not_history_power(self):
        self.a.collect()
        data=self.a.history('fridge','24h');self.assertEqual(data['points'][0][1],42.5)
        with self.a.database() as db,db:db.execute("UPDATE samples SET payload=?",(json.dumps({'online':False,'power_w':99}),))
        self.a.history_cache.clear()
        self.assertEqual(self.a.history('fridge','24h')['points'],[])
    def test_budget_prevents_transport(self):
        e.atomic_json(self.a.budget_path,dict(month=time.strftime('%Y-%m',time.gmtime()),day=time.strftime('%Y-%m-%d',time.gmtime()),daily=400,monthly=400))
        with self.assertRaises(RuntimeError):self.a.collect()
        self.assertFalse(self.calls)
    def test_expiry_prevents_transport(self):
        c=e.private_json(self.a.config_path);c['app_expires_at']=0;e.atomic_json(self.a.config_path,c)
        with self.assertRaises(RuntimeError):self.a.collect()
        self.assertFalse(self.calls)
    def test_storage_failure_prevents_transport(self):
        self.a.guard=lambda:(_ for _ in ()).throw(RuntimeError())
        with self.assertRaises(RuntimeError):self.a.collect()
        self.assertFalse(self.calls)
    def test_unknown_fields_and_invalid_numbers(self):
        self.assertEqual(e.metrics(dict(power='nan',voltage='inf',current=-1,switch='on',voltage_00=23000)),{'switch':'on'})
    def test_private_files(self):
        self.assertEqual(self.a.tokens_path.stat().st_mode&0o777,0o600)
        os.chmod(self.a.tokens_path,0o644)
        with self.assertRaises(RuntimeError):self.a.token()
    def test_cli_and_server_share_operation_lock(self):
        other=e.Ewelink(None,None,self.a.config_path,self.a.transport)
        with self.a.exclusive():
            with self.assertRaises(RuntimeError):other.authorize()
    def test_powr3_uses_hundredths_and_powr2_uses_engineering_units(self):
        self.assertEqual(e.metrics({'power':4566,'voltage':23500,'current':27},190),{'power_w':45.66,'voltage_v':235.0,'current_a':0.27})
        self.assertEqual(e.metrics({'power':'80.05','voltage':'240.28','current':'0.54'},32),{'power_w':80.05,'voltage_v':240.28,'current_a':0.54})
    def test_mobile_bins_distinguish_zero_missing_and_weight_samples(self):
        now=int(time.time()*1000)
        with self.a.database() as db,db:
            for i,power in enumerate([0,10,20]):
                db.execute('INSERT INTO samples VALUES(?,?,?)',('fridge',now-(i+1)*300000,json.dumps({'online':True,'power_w':power})))
        result=self.a.history('fridge','30d')
        self.assertEqual(result['bucket_unit'],'week')
        present=[b for b in result['bins'] if b['samples']]
        self.assertEqual(len(present),1);self.assertEqual(present[0]['mean_w'],10)
        self.assertEqual(present[0]['samples'],3)
        self.assertTrue(any(b['mean_w'] is None for b in result['daily_bins']))
        with self.a.database() as db,db:db.execute('UPDATE samples SET payload=?',(json.dumps({'online':True,'power_w':0}),))
        self.a.history_cache.clear()
        result=self.a.history('fridge','24h')
        self.assertTrue(any(b['mean_w']==0 for b in result['bins']))
        self.assertTrue(any(b['mean_w'] is None for b in result['bins']))
        self.assertFalse(self.calls)
    def test_new_periods_are_bounded_and_do_not_contact_vendor(self):
        with self.a.database():pass
        for window in ['live','today','yesterday','previous24h','24h','7d','30d']:
            data=self.a.history('fridge',window)
            self.assertLessEqual(len(data['bins']),25)
            self.assertLessEqual(len(data.get('daily_bins',[])),32)
            self.assertLessEqual(len(data['points']),501)
        self.assertFalse(self.calls)

if __name__=='__main__':unittest.main()
