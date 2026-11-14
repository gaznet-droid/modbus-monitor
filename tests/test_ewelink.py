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
    def test_powr316_day_counter_is_kept_and_powr2_has_none(self):
        self.assertEqual(e.metrics({'power':120,'dayKwh':25,'monthKwh':1282},190),{'power_w':1.2,'day_kwh':0.25})
        self.assertEqual(e.metrics({'power':'5','dayKwh':25,'hundredDaysKwh':'get'},32),{'power_w':5.0})
    def test_energy_from_counter_counts_rises_and_midnight_restart(self):
        m=60000
        # 0.10 kWh before the period, rising to 0.30, the plug's midnight restart to 0.02, then 0.05.
        rows=[(0,50,0.12),(10*m,50,0.30),(20*m,0,0.02),(30*m,0,0.05)]
        self.assertEqual(e.period_energy(rows,0,40*m,True,baseline=0.10),{'wh':250.0,'method':'counter'})
        # Without a reading just before, counting starts at the first reading in the period.
        self.assertEqual(e.period_energy(rows,0,40*m,True),{'wh':230.0,'method':'counter'})
        # A restart between the reading before the period and the first one in it counts all of the first.
        self.assertEqual(e.period_energy([(0,1,0.04),(m,1,0.06)],0,2*m,True,baseline=0.9),{'wh':60.0,'method':'counter'})
    def test_estimate_holds_readings_with_a_gap_cap(self):
        m=60000
        rows=[(0,60,None),(5*m,30,None),(60*m,None,None),(65*m,12,None)]
        # 60 W for 5 min, 30 W for the 10-minute cap only, offline counts nothing, 12 W to the end (5 min).
        self.assertEqual(e.period_energy(rows,0,70*m,True),{'wh':11.0,'method':'estimate'})
        self.assertEqual(e.period_energy([],0,70*m,True),{'wh':None,'method':None})
        # Counter readings only from part way: estimate before, counter after.
        mixed=[(0,120,None),(10*m,120,0.50),(20*m,120,0.52)]
        self.assertEqual(e.period_energy(mixed,0,30*m,True),{'wh':40.0,'method':'mixed'})
        # Short periods integrate the readings even when a counter exists (it moves in 10 Wh steps).
        self.assertEqual(e.period_energy(mixed,0,30*m,False)['method'],'estimate')
    def test_history_reports_counter_energy(self):
        now=int(time.time()*1000);start=now-86400000
        with self.a.database() as db,db:
            for ts,day in [(start-600000,0.10),(start+600000,0.20),(now-600000,0.45)]:
                db.execute('INSERT INTO samples VALUES(?,?,?)',('fridge',ts,json.dumps({'online':True,'power_w':40,'day_kwh':day})))
        self.assertEqual(self.a.history('fridge','24h')['energy'],{'wh':350.0,'method':'counter'})
        self.assertEqual(self.a.history('fridge','live')['energy']['method'],'estimate')
        self.assertFalse(self.calls)
    def test_home_metrics_scale_hundredths_and_door_lock(self):
        self.assertEqual(e.home_metrics({'temperature':'1712','humidity':'9377','battery':50},1770,'temp'),
                         {'temperature_c':17.12,'humidity_pct':93.77,'battery_pct':50.0})
        self.assertEqual(e.home_metrics({'temperature':-1579,'humidity':9854},7052,'temp'),{'temperature_c':-15.79,'humidity_pct':98.54})
        self.assertEqual(e.home_metrics({'lock':1,'battery':100},7003,'door'),{'door':'open','battery_pct':100.0})
        self.assertEqual(e.home_metrics({'lock':0},3026,'door'),{'door':'closed'})
        self.assertEqual(e.home_metrics({'lock':True},3026,'door'),{})
        self.assertEqual(e.home_metrics({'switch':'off','power':21,'voltage':23341,'current':0},277,'switch'),
                         {'switch':'off','power_w':0.21,'voltage_v':233.41,'current_a':0.0})
        self.assertEqual(e.home_metrics({'temperature':'nan','humidity':'20000'},1770,'temp'),{})
    def home_setup(self):
        e.atomic_json(self.a.home_path,{'devices':{
            'lounge-temp':{'id':'temp1','kind':'temp','place':'room:lounge','name':'Lounge'},
            'garage-door':{'id':'door1','kind':'door','place':'section:garage','name':'Garage door'}}})
        def transport(region,method,path,body,headers):
            self.calls.append((region,method,path,json.loads(body) if body else None,headers))
            item=lambda i,p,u:{'itemType':1,'itemData':{'deviceid':i,'online':True,'extra':{'uiid':u},'params':p}}
            return {'thingList':[item('one',{'power':'42.5','switch':'on'},32),item('temp1',{'temperature':'1925','humidity':'7240'},1770),
                                 item('door1',{'lock':1},7003),item('unmapped',{'temperature':'999'},1770)]}
        self.a.transport=transport
    def test_home_uses_one_list_request_and_hides_ids(self):
        self.home_setup();self.a.collect();self.a.collect()
        self.assertEqual([(c[1],c[2]) for c in self.calls],[('GET','/v2/device/thing?num=30')])
        self.assertEqual(self.a.latest()['devices'][-1]['power_w'],42.5)
        home=self.a.home_latest();text=json.dumps(home)
        self.assertTrue(home['configured']);self.assertEqual(len(home['devices']),2)
        lounge,door=home['devices']
        self.assertEqual((lounge['temperature_c'],lounge['humidity_pct'],lounge['place']),(19.25,72.4,'room:lounge'))
        self.assertEqual(door['door'],'open');self.assertFalse(door['stale'])
        for secret in ('temp1','door1','unmapped','access'):self.assertNotIn(secret,text)
        with self.a.database(readonly=True) as db:self.assertNotIn('home:unmapped',[r[0] for r in db.execute('SELECT device FROM latest')])
    def test_bad_home_mapping_keeps_existing_batch(self):
        e.atomic_json(self.a.home_path,{'devices':{'Bad Slug':{'id':'x','kind':'temp','place':'room:x','name':'X'}}})
        self.a.collect()
        self.assertEqual(self.calls[0][1:3],('POST','/v2/device/thing'))
        self.assertFalse(self.a.home_latest()['configured'])
    def test_home_history_returns_day_of_temperatures_only(self):
        self.home_setup();self.a.collect();self.calls.clear()
        now=int(time.time()*1000)
        with self.a.database() as db,db:
            for device,ts,value in [('home:lounge-temp',now-25*3600000,{'online':True,'temperature_c':5.0}),
                                    ('home:lounge-temp',now-3600000,{'online':True,'temperature_c':18.5,'humidity_pct':60.0}),
                                    ('home:lounge-temp',now-1800000,{'online':False,'temperature_c':99.0}),
                                    ('home:garage-door',now-600000,{'online':True,'door':'open'})]:
                db.execute('INSERT OR REPLACE INTO samples VALUES(?,?,?)',(device,ts,json.dumps(value)))
        self.a.history_cache.clear()
        data=self.a.home_history('lounge-temp');text=json.dumps(data)
        self.assertEqual(data['end']-data['start'],86400000);self.assertEqual(data['name'],'Lounge')
        self.assertEqual([p[1:] for p in data['points']],[[18.5,60.0],[19.25,72.4]])
        self.assertEqual((data['min_c'],data['max_c']),(18.5,19.25))
        for secret in ('temp1','door1','unmapped','access'):self.assertNotIn(secret,text)
        self.assertIs(self.a.home_history('lounge-temp'),data)
        self.assertFalse(self.calls)
    def test_home_history_rejects_doors_and_unknown_sensors(self):
        with self.assertRaises(ValueError):self.a.home_history('lounge-temp')
        self.home_setup()
        for slug in ('garage-door','unknown','',None):
            with self.assertRaises(ValueError):self.a.home_history(slug)
        self.assertFalse(self.calls)
    def test_home_latest_without_mapping_makes_no_calls(self):
        self.assertEqual(self.a.home_latest()['devices'],[]);self.assertFalse(self.calls)
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
        for window in ['live','2h','4h','8h','today','yesterday','previous24h','24h','7d','30d']:
            data=self.a.history('fridge',window)
            self.assertLessEqual(len(data['bins']),25)
            self.assertLessEqual(len(data.get('daily_bins',[])),32)
            self.assertLessEqual(len(data['points']),501)
        for window,hours in [('live',1),('2h',2),('4h',4),('8h',8)]:
            data=self.a.history('fridge',window);self.assertEqual(data['end']-data['start'],hours*3600000)
        self.assertFalse(self.calls)

if __name__=='__main__':unittest.main()
