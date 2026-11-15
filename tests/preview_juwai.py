import json,time
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
root=Path(__file__).resolve().parents[1]/'router/web'
now=int(time.time()*1000)
live={'server_time':now,'latest':{'ts':now,'value':150},'stale':False,'meter':{'ts':now,'kwh':9000,'stale':False},'points':[],'reload':False,'period':{'start':now-900000,'end':now},'energy':{'import_wh':10,'export_wh':2,'coverage_s':300,'resolution_s':1}}
import sys
sys.path.insert(0,str(root.parent))
from tuya_cached import generation_bins,period_bounds

def fixture_energy(device,window):
 start,end=period_bounds(window,now)
 unit='week' if window=='30d' else 'day' if window=='7d' else 'hour'
 value=1.25 if device=='shed' else 2.5
 def bins(unit):
  result=generation_bins(start,end,unit)
  for i,b in enumerate(result):
   if i < 2 and len(result)>3: continue
   b['coverage_s']=b['period_s']
   b['estimated_kwh']=0 if i==2 and len(result)>3 else value/max(1,len(result)-(3 if len(result)>3 else 0))
  return result
 return dict(estimated_kwh=value,coverage_s=3600,period_s=(end-start)/1000,bucket_unit=unit,buckets=bins(unit),daily_buckets=bins('day'))

def fixture_points(device,window):
 # One reading every two minutes (every hour beyond a day) over the window, with a daylight curve so the lines and bars show.
 import math
 start,end=period_bounds(window,now);step=120000 if end-start<=86400000 else 3600000;peak={'shed':180,'garage':240,'p275':120}.get(device,100);rows=[]
 for ts in range(start+step,end,step):
  hour=(ts/3600000)%24;sun=max(0.0,math.sin((hour-6)/12*math.pi));power=round(peak*sun,2);volts=round(20+8*sun,2)
  rows.append([ts,1,volts,round(power/volts,3),power])
 return dict(start=start,end=end,step_ms=step,points=rows)

instrument="""<script>window.__requests=[];window.__errors=[];window.onerror=(m)=>__errors.push(m);const originalFetch=window.fetch.bind(window);window.fetch=(...a)=>{__requests.push(String(a[0]));return originalFetch(...a)};Object.defineProperty(document,'hidden',{get:()=>window.__testHidden===true});</script>"""
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  u=urlsplit(self.path);q=parse_qs(u.query);kind='application/json'
  if u.path=='/api/history':b=json.dumps({'start':now-900000,'end':now,'step_ms':3000,'points':[[now-3000,150,150,150,1],[now,150,150,150,1]],'live':live,'energy':live['energy']}).encode()
  elif u.path=='/api/live':b=json.dumps(live).encode()
  elif u.path=='/api/voltage':b=json.dumps({'ts':int(time.time()*1000),'volts':239.4}).encode()
  elif u.path=='/api/tuya/latest':b=json.dumps({'server_time':now,'collection_mode':'manual','devices':[{'id':'shed','observed_at':now,'stale':False,'values':{'power_w':1.02,'voltage_v':26.47,'current_a':.038}},{'id':'garage','observed_at':now,'stale':False,'values':{'power_w':18.37,'voltage_v':21.13,'current_a':.869}}]}).encode()
  elif u.path=='/api/tuya/history':b=json.dumps({'energy':fixture_energy(q['device'][0],q['window'][0]),'device':q['device'][0],**fixture_points(q['device'][0],q['window'][0]),'columns':['ts','samples','voltage_v','current_a','power_w']}).encode()
  elif u.path in ('/chart.umd.min.js','/ewelink.js'):b=(root/u.path[1:]).read_bytes();kind='text/javascript'
  elif u.path=='/':b=(root/'index.html').read_text().replace('<script src=',instrument+'<script src=',1).encode();kind='text/html'
  else:self.send_error(404);return
  self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def log_message(self,*args):pass
HTTPServer(('127.0.0.1',8766),Handler).serve_forever()
