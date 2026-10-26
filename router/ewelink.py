#!/usr/bin/env python3
"""Dad's Modbus eWeLink adapter. Standard library; no device-control API.

Private config/tokens/state live on the router's root filesystem (0600), never
on exFAT or in the web root. Only cached, selected telemetry is public.
OAuth initiation is a local administrator CLI action, never a public endpoint.
"""
import argparse
from bisect import bisect_right
import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CALLBACK = 'https://modbus.clockmillbarn.uk/api/integrations/ewelink/callback'
DEVICES = [('inverter-1000', '1000 watt inverter'), ('garage-inverter', 'Garage inverter'),
           ('inverter-600', '600 watt inverter'), ('charger-inverter', 'Charger inverter'),
           ('fridge', 'Fridge')]
HOSTS = {'eu':'https://eu-apia.coolkit.cc', 'us':'https://us-apia.coolkit.cc',
         'as':'https://as-apia.coolkit.cc', 'cn':'https://cn-apia.coolkit.cn'}
INTERVAL = 300
SCHEMA = '''CREATE TABLE IF NOT EXISTS samples(
 device TEXT NOT NULL, ts INTEGER NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(device,ts));
 CREATE TABLE IF NOT EXISTS latest(device TEXT PRIMARY KEY, ts INTEGER NOT NULL, payload TEXT NOT NULL);'''

def atomic_json(path, value):
    path = Path(path)
    fd = os.open(str(path)+'.new', os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, separators=(',', ':'), allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(str(path)+'.new', path)
    finally:
        with contextlib.suppress(FileNotFoundError): os.unlink(str(path)+'.new')

def private_json(path):
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        st = os.fstat(stream.fileno())
        if st.st_mode & 0o077 or st.st_uid != os.geteuid():
            raise RuntimeError('Private configuration permissions are invalid')
        return json.load(stream)

def sign(secret, text):
    return base64.b64encode(hmac.new(secret.encode(), text.encode(), hashlib.sha256).digest()).decode()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

class VendorError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('eWeLink request unsuccessful')

def number(value, low=None, high=None):
    if isinstance(value, bool): return None
    try: value = float(value)
    except (TypeError, ValueError): return None
    if not math.isfinite(value) or (low is not None and value < low) or (high is not None and value > high): return None
    return value

def metrics(params, uiid=None):
    """Only documented engineering-unit scalar fields. Never guess raw scales."""
    out = {}
    for source, target, low, high in [('power','power_w',-1000000,1000000),
            ('voltage','voltage_v',0,1000), ('current','current_a',0,10000)]:
        value = number(params.get(source))
        if value is not None and uiid==190:value/=100
        value = number(value,low,high)
        if value is not None: out[target] = value
    switch = params.get('switch')
    if switch in ('on', 'off'): out['switch'] = switch
    elif isinstance(params.get('switches'), list):
        states = [p.get('switch') for p in params['switches'] if isinstance(p,dict)]
        if states and all(x in ('on','off') for x in states):
            out['switch'] = 'on' if 'on' in states else 'off'
    return out

class Ewelink:
    def __init__(self, storage_directory, guard, config='/etc/modbus-ewelink.json', transport=None):
        self.storage_directory, self.guard = storage_directory, guard
        self.config_path = Path(config)
        self.tokens_path = Path(str(config)+'.tokens')
        self.pending_path = Path(str(config)+'.pending')
        self.budget_path = Path(str(config)+'.budget')
        self.lock = threading.RLock()
        self.read_lock = threading.RLock()
        self.history_cache = {}
        self.transport = transport or self._transport
        self.last_request = 0
        self.status = 'Awaiting account authorisation'
        self.stop = threading.Event()

    def config(self):
        data = private_json(self.config_path)
        if data.get('redirect_url') != CALLBACK or not data.get('appid') or not data.get('secret'):
            raise RuntimeError('eWeLink configuration incomplete')
        # This app has a console-verified annual expiry, checked before transport.
        if time.time() >= data.get('app_expires_at',0):
            raise RuntimeError('eWeLink application expired')
        return data

    @contextlib.contextmanager
    def exclusive(self):
        """Serialise token rotation and budget updates across CLI/server processes."""
        with self.lock:
            fd=os.open(str(self.config_path)+'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            try:
                os.fchmod(fd,0o600)
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:raise RuntimeError('eWeLink operation already in progress') from None
                yield
            finally:os.close(fd)

    def _transport(self, region, method, path, body, headers):
        if region not in HOSTS: raise ValueError('Invalid region')
        request = urllib.request.Request(HOSTS[region]+path, data=body, headers=headers, method=method)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request, timeout=10) as response:
                payload = response.read(524289)
                if len(payload)>524288: raise RuntimeError('Response too large')
        except urllib.error.HTTPError as error:
            raise VendorError(error.code) from None
        data = json.loads(payload)
        if data.get('error') != 0: raise VendorError(data.get('error'))
        return data['data']

    def request(self, region, method, path, body=None, token=None):
        allowed = {('POST','/v2/user/oauth/token'), ('POST','/v2/user/refresh'),
                   ('GET','/v2/device/thing'), ('POST','/v2/device/thing')}
        if (method,path.split('?')[0]) not in allowed: raise ValueError('API not permitted')
        with self.lock:
            config = self.config()
            now = time.time()
            month, day = time.strftime('%Y-%m',time.gmtime(now)), time.strftime('%Y-%m-%d',time.gmtime(now))
            try: budget = private_json(self.budget_path)
            except FileNotFoundError: budget = {}
            if budget.get('month') != month: budget = {'month':month,'monthly':0,'day':day,'daily':0}
            if budget.get('day') != day: budget.update(day=day,daily=0)
            if budget['monthly']>=12000 or budget['daily']>=400: raise RuntimeError('eWeLink request budget reached')
            # Persist attempts before sending, so retries/restarts cannot reset caps.
            budget['monthly']+=1; budget['daily']+=1
            atomic_json(self.budget_path,budget)
            delay = 1.05-(time.monotonic()-self.last_request)
            if delay>0: time.sleep(delay)
            raw = json.dumps(body,separators=(',',':')).encode() if body is not None else None
            headers = {'X-CK-Appid':config['appid'], 'X-CK-Nonce':secrets.token_hex(4),
                       'Content-Type':'application/json', 'Authorization':('Bearer '+token) if token else 'Sign '+sign(config['secret'],raw.decode() if raw else '')}
            self.last_request = time.monotonic()
            return self.transport(region,method,path,raw,headers)

    def authorize(self):
        """Local authenticated operator creates a ten-minute, one-use flow."""
        with self.exclusive():
            config = self.config()
            state = secrets.token_urlsafe(32)
            atomic_json(self.pending_path, {'hash':hashlib.sha256(state.encode()).hexdigest(),
                                          'expires':time.time()+600})
            seq = str(int(time.time()*1000))
            query = dict(clientId=config['appid'],seq=seq,redirectUrl=CALLBACK,
                         authorization=sign(config['secret'],config['appid']+'_'+seq),
                         grantType='authorization_code',state=state,nonce=secrets.token_hex(4))
            return 'https://c2ccdn.coolkit.cc/oauth/index.html?'+urllib.parse.urlencode(query)

    def callback(self, query):
        with self.exclusive():
            if set(query)!={'state','code','region'} or any(len(x)!=1 for x in query.values()):
                raise ValueError('Invalid callback')
            state,code,region = (query[x][0] for x in ('state','code','region'))
            if len(state)>256 or not 1<=len(code)<=256 or region not in HOSTS: raise ValueError('Invalid callback')
            try: pending = private_json(self.pending_path)
            except FileNotFoundError: raise ValueError('No pending authorisation') from None
            if time.time()>pending['expires'] or not hmac.compare_digest(pending['hash'],hashlib.sha256(state.encode()).hexdigest()):
                raise ValueError('Authorisation expired or invalid')
            os.unlink(self.pending_path) # Consume before exchange; replay is refused.
            result = self.request(region,'POST','/v2/user/oauth/token',
                {'code':code,'redirectUrl':CALLBACK,'grantType':'authorization_code'})
            self.save_tokens(result,region)
            self.status = 'Connected; waiting for first collection'

    def save_tokens(self, result, region):
        now = int(time.time()*1000)
        at,rt = result.get('accessToken',result.get('at')),result.get('refreshToken',result.get('rt'))
        # Refresh contract documents 30/60 days when timestamps are absent.
        ae,re = result.get('atExpiredTime',now+30*86400000),result.get('rtExpiredTime',now+60*86400000)
        if not isinstance(at,str) or not at or not isinstance(rt,str) or not rt or number(ae,now) is None or number(re,now) is None:
            raise RuntimeError('Invalid OAuth token response')
        atomic_json(self.tokens_path,dict(accessToken=at,refreshToken=rt,atExpiredTime=ae,rtExpiredTime=re,region=region))

    def token(self):
        with self.lock:
            t = private_json(self.tokens_path)
            if t['rtExpiredTime']<=time.time()*1000: raise RuntimeError('Account authorisation expired')
            if t['atExpiredTime']<=time.time()*1000+86400000:
                result = self.request(t['region'],'POST','/v2/user/refresh',{'rt':t['refreshToken']},t['accessToken'])
                self.save_tokens(result,t['region']); t = private_json(self.tokens_path)
            return t

    @contextlib.contextmanager
    def database(self, readonly=False):
        self.guard()
        with self.storage_directory() as directory:
            path = '/proc/self/fd/'+str(directory)+'/ewelink.sqlite3'
            db = sqlite3.connect('file:'+path+'?mode=ro' if readonly else path,timeout=3,uri=readonly)
            try:
                if not readonly:
                    db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
                    db.executescript(SCHEMA)
                db.execute('PRAGMA cache_size=-128')
                yield db
            finally: db.close()

    def collect(self):
        # A single shared batch per five minutes, independent of browser traffic.
        with self.exclusive():
            self.guard() # Do not consume API quota when USB is missing.
            config = self.config(); t = self.token()
            mapping = config.get('devices',{})
            if not mapping: raise RuntimeError('Device selection required')
            selected = {slug:(value if isinstance(value,dict) else {'id':value,'item_type':1}) for slug,value in mapping.items()}
            ids = list(dict.fromkeys(x.get('id') for x in selected.values()))
            if len(ids)>5 or any(not isinstance(x,str) or not x for x in ids): raise RuntimeError('Invalid device selection')
            if any(x.get('item_type') not in (1,2) for x in selected.values()): raise RuntimeError('Invalid device type')
            schedule_path = Path(str(self.config_path)+'.schedule')
            try: next_cycle = private_json(schedule_path)['next']
            except FileNotFoundError: next_cycle = 0
            if time.time()<next_cycle:
                self.status = 'Connected';return
            atomic_json(schedule_path,{'next':time.time()+INTERVAL})
            result = self.request(t['region'],'POST','/v2/device/thing',
                                  {'thingList':[{'itemType':x['item_type'],'id':x['id']} for x in selected.values()]},t['accessToken'])
            things = {x['itemData']['deviceid']:x['itemData'] for x in result.get('thingList',[]) if x.get('itemType') in (1,2) and isinstance(x.get('itemData'),dict) and x['itemData'].get('deviceid') in ids}
            now = int(time.time()*1000)
            with self.database() as db, db:
                for slug,_ in DEVICES:
                    d = things.get(selected.get(slug,{}).get('id'))
                    if not d: continue # Preserve old sample with its original timestamp.
                    uiid=d.get('extra',{}).get('uiid',selected.get(slug,{}).get('uiid'))
                    value = dict(metrics(d.get('params',{}),uiid),online=d.get('online') is True)
                    payload = json.dumps(value,separators=(',',':'),allow_nan=False)
                    db.execute('INSERT OR REPLACE INTO samples VALUES(?,?,?)',(slug,now,payload))
                    db.execute('INSERT OR REPLACE INTO latest VALUES(?,?,?)',(slug,now,payload))
                db.execute('DELETE FROM samples WHERE rowid IN (SELECT rowid FROM samples WHERE ts<? LIMIT 1000)',(now-30*86400000,))
            self.status = 'Connected'
            with self.read_lock:self.history_cache.clear()

    def inventory(self):
        with self.exclusive():
            t = self.token()
            # Explicit operator discovery only, not recurring polling of other devices.
            data = self.request(t['region'],'GET','/v2/device/thing?num=30',token=t['accessToken'])
            return [{'id':x['itemData'].get('deviceid'),'name':x['itemData'].get('name'),
                     'item_type':x['itemType'],'uiid':x['itemData'].get('extra',{}).get('uiid'),
                     'fields':sorted(x['itemData'].get('params',{}))}
                    for x in data.get('thingList',[]) if x.get('itemType') in (1,2)]

    def latest(self):
        with self.read_lock:
            rows = {}
            try:
                with self.database(readonly=True) as db:
                    rows = {r[0]:(r[1],json.loads(r[2])) for r in db.execute('SELECT device,ts,payload FROM latest')}
            except (OSError,RuntimeError,sqlite3.Error): pass
            now = int(time.time()*1000)
            devices = []
            for slug,name in DEVICES:
                ts,value = rows.get(slug,(None,{}))
                devices.append(dict(id=slug,name=name,ts=ts,stale=ts is None or now-ts>900000,**value))
            status=self.status
            if status.startswith('Awaiting') and self.config_path.exists() and self.tokens_path.exists() and any(not x['stale'] for x in devices):status='Connected'
            return dict(status=status,server_time=now,interval_seconds=INTERVAL,devices=devices)

    def history(self, slug, window):
        allowed=('live','today','24h','previous24h','yesterday','7d','30d')
        if slug not in dict(DEVICES) or window not in allowed: raise ValueError('Invalid history query')
        from tuya_cached import period_bounds, generation_bins
        with self.read_lock:
            self.guard()
            now = int(time.time()*1000)
            cached=self.history_cache.get(slug)
            if cached and cached[0]==window and time.monotonic()<cached[1]:return cached[2]
            start,end=period_bounds(window,now)
            bucket=max(300000,(end-start)//500)
            unit='week' if window=='30d' else 'day' if window=='7d' else 'hour'
            def plan(unit):return [dict(start=b['start'],end=b['end'],mean_w=None,samples=0) for b in generation_bins(start,end,unit)]
            plans={'bins':plan(unit)}
            if window=='30d':plans['daily_bins']=plan('day')
            edges={key:[b['end'] for b in rows] for key,rows in plans.items()}
            totals={key:[0.0]*len(rows) for key,rows in plans.items()}
            points={}
            with self.database(readonly=True) as db:
                rows=db.execute('SELECT ts,payload FROM samples WHERE device=? AND ts>=? AND ts<? ORDER BY ts',(slug,start,end))
                for ts,payload in rows:
                    value=json.loads(payload)
                    power=number(value.get('power_w')) if value.get('online') else None
                    if power is None:continue
                    group=ts//bucket*bucket
                    total,count=points.get(group,(0,0));points[group]=(total+power,count+1)
                    for key,bins in plans.items():
                        index=bisect_right(edges[key],ts)
                        if index<len(bins):
                            totals[key][index]+=power;bins[index]['samples']+=1
            for key,bins in plans.items():
                for i,b in enumerate(bins):
                    if b['samples']:b['mean_w']=totals[key][i]/b['samples']
                    b['partial']=b['samples']<max(1,int((b['end']-b['start'])/(INTERVAL*1000))-1)
            result=dict(device=slug,window=window,start=start,end=end,
                points=[[ts,total/count] for ts,(total,count) in sorted(points.items())],
                bucket_ms=bucket,bucket_unit=unit,**plans)
            # One cached period per device; browser reads never call the vendor.
            self.history_cache[slug]=(window,time.monotonic()+60,result)
            return result

    def poll(self):
        delay = 0
        while not self.stop.wait(delay):
            try:
                if not self.config_path.exists():
                    self.status = 'Awaiting integration setup'
                elif not self.tokens_path.exists():
                    self.status = 'Awaiting account authorisation'
                else: self.collect()
                delay = INTERVAL
            except VendorError as error:
                self.status = 'Account authorisation required' if error.code in (401,402) else 'eWeLink unavailable; showing stored readings'
                delay = 3600 if error.code in (403,407,412,429) else 900
            except (OSError,RuntimeError,ValueError,KeyError,sqlite3.Error):
                self.status = 'Setup or collection unavailable; showing stored readings'
                delay = 900

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['authorize','inventory','once'])
    parser.add_argument('--config',default='/etc/modbus-ewelink.json')
    args = parser.parse_args()
    if os.geteuid()!=0: raise SystemExit('Run on the router as the local administrator')
    import sys
    sys.path.insert(0,'/usr/local/usr/lib/clockmill')
    import modbus_usb
    adapter = Ewelink(modbus_usb.storage_directory,modbus_usb.check,args.config)
    if args.action=='authorize': print(adapter.authorize())
    elif args.action=='inventory': print(json.dumps(adapter.inventory()))
    else: adapter.collect();print('Selected device collection complete')

if __name__=='__main__': main()
