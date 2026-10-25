import json,time
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
root=Path(__file__).resolve().parents[1]/'router/web'
now=int(time.time()*1000)
live={'server_time':now,'latest':{'ts':now,'value':150},'stale':False,'meter':{'ts':now,'kwh':9000,'stale':False},'points':[],'reload':False,'period':{'start':now-900000,'end':now},'energy':{'import_wh':10,'export_wh':2,'coverage_s':300,'resolution_s':1}}
instrument="""<script>window.__requests=[];window.__errors=[];window.onerror=(m)=>__errors.push(m);const originalFetch=window.fetch.bind(window);window.fetch=(...a)=>{__requests.push(String(a[0]));return originalFetch(...a)};Object.defineProperty(document,'hidden',{get:()=>window.__testHidden===true});</script>"""
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  u=urlsplit(self.path);q=parse_qs(u.query);kind='application/json'
  if u.path=='/api/history':b=json.dumps({'start':now-900000,'end':now,'step_ms':3000,'points':[[now-3000,150,150,150,1],[now,150,150,150,1]],'live':live,'energy':live['energy']}).encode()
  elif u.path=='/api/live':b=json.dumps(live).encode()
  elif u.path=='/api/voltage':b=json.dumps({'ts':int(time.time()*1000),'volts':239.4}).encode()
  elif u.path=='/api/tuya/latest':b=json.dumps({'server_time':now,'collection_mode':'manual','devices':[{'id':'shed','observed_at':now,'stale':False,'values':{'power_w':1.02,'voltage_v':26.47,'current_a':.038}},{'id':'garage','observed_at':now,'stale':False,'values':{'power_w':18.37,'voltage_v':21.13,'current_a':.869}}]}).encode()
  elif u.path=='/api/tuya/history':b=json.dumps({'energy':{'estimated_kwh':1.25 if q['device'][0]=='shed' else 2.5,'coverage_s':3600,'period_s':7200},'device':q['device'][0],'start':now-86400000,'end':now,'step_ms':120000,'points':[[now-60000,1,26.47,.038,1.02]],'columns':['ts','samples','voltage_v','current_a','power_w']}).encode()
  elif u.path=='/chart.umd.min.js':b=(root/'chart.umd.min.js').read_bytes();kind='text/javascript'
  elif u.path=='/':b=(root/'index.html').read_text().replace('<script src=',instrument+'<script src=',1).encode();kind='text/html'
  else:self.send_error(404);return
  self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def log_message(self,*args):pass
HTTPServer(('127.0.0.1',8766),Handler).serve_forever()
