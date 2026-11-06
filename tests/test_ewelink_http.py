import json
from pathlib import Path
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import test_ewelink as fixtures
import monitor

class HttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.AdapterTests();self.fixture.setUp();self.a=self.fixture.a
        root=self.fixture.root;(root/'index.html').write_text('Modbus hub')
        (root/'ewelink.js').write_text('// cached view')
        class Store:
            def live(self,*a,**kw):return {'stale':False,'latest':{'ts':1}}
        self.server=monitor.WebServer(('127.0.0.1',0),monitor.handler(Store(),root,ewelink=self.a))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.fixture.tearDown()
    def request(self,path,method='GET'):
        try:return urllib.request.urlopen(urllib.request.Request(self.base+path,method=method),timeout=3)
        except urllib.error.HTTPError as error:return error
    def test_public_cached_latest_and_no_private_file_access(self):
        with self.request('/api/ewelink/latest') as r:
            self.assertEqual(r.status,200);self.assertEqual(r.headers['Cache-Control'],'no-store')
            self.assertEqual(len(json.load(r)['devices']),5)
        for path in ['/etc/modbus-ewelink.json','/api/integrations/ewelink/connect','/api/ewelink/latest?foo=1']:
            with self.request(path) as r:self.assertIn(r.status,(400,404))
        self.assertFalse(self.fixture.calls)
    def test_callback_and_control_refused(self):
        with self.request('/api/integrations/ewelink/callback?state=bad&code=bad&region=eu') as r:self.assertEqual(r.status,400)
        with self.request('/api/ewelink/latest',method='POST') as r:self.assertEqual(r.status,501)
        for path in ['/api/ewelink/history?device=fridge&window=999d','/api/ewelink/history?device=unknown&window=24h']:
            with self.request(path) as r:self.assertEqual(r.status,400)
        self.assertFalse(self.fixture.calls)
    def test_success_strips_callback_query_and_replay_fails(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*a,**kw):return None
        q=self.fixture.query();query=urllib.parse.urlencode(dict(state=q['state'][0],code='code',region='eu'))
        url=self.base+'/api/integrations/ewelink/callback?'+query
        try:urllib.request.build_opener(NoRedirect).open(url,timeout=3)
        except urllib.error.HTTPError as r:
            self.assertEqual(r.code,303);self.assertEqual(r.headers['Location'],'/#ewelink')
            self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertEqual(r.headers['Referrer-Policy'],'no-referrer')
        else:self.fail('Expected a redirect')
        with self.request('/api/integrations/ewelink/callback?'+query) as r:self.assertEqual(r.status,400)

    def test_version_reports_deployed_commit_only(self):
        from unittest.mock import patch
        vf=self.fixture.root/'VERSION.json'
        vf.write_text(json.dumps({'commit':'abc123','deployed_at':'2026-11-06T09:00:00Z','subject':'Test','secret':'x'}))
        with patch.object(monitor,'VERSION_FILE',vf):
            with self.request('/version') as r:
                self.assertEqual(r.status,200);body=json.load(r)
        self.assertEqual(body,{'commit':'abc123','deployed_at':'2026-11-06T09:00:00Z','subject':'Test'})
        with patch.object(monitor,'VERSION_FILE',self.fixture.root/'missing.json'):
            with self.request('/version') as r:self.assertEqual(json.load(r)['commit'],None)
        with self.request('/version?x=1') as r:self.assertEqual(r.status,400)

    def hook(self,body,event='push',secret=b'sekrit',sig=None):
        import hashlib,hmac
        data=json.dumps(body).encode()
        if sig is None:sig='sha256='+hmac.new(secret,data,hashlib.sha256).hexdigest()
        req=urllib.request.Request(self.base+'/api/deploy-hook',data=data,method='POST',
            headers={'X-Hub-Signature-256':sig,'X-GitHub-Event':event,'Content-Type':'application/json'})
        try:return urllib.request.urlopen(req,timeout=3)
        except urllib.error.HTTPError as error:return error
    def test_deploy_hook_queues_only_signed_pushes_to_main(self):
        from unittest.mock import patch
        root=self.fixture.root;secret=root/'hook.secret';req=root/'deploy-request'
        with patch.object(monitor,'DEPLOY_SECRET_FILE',secret),patch.object(monitor,'DEPLOY_REQUEST_FILE',req):
            with self.hook({'ref':'refs/heads/main'}) as r:self.assertEqual(r.status,503)
            secret.write_bytes(b'sekrit\n')
            with self.hook({'ref':'refs/heads/main'},secret=b'wrong') as r:self.assertEqual(r.status,403)
            with self.hook({'ref':'refs/heads/main'},sig='') as r:self.assertEqual(r.status,403)
            self.assertFalse(req.exists())
            with self.hook({'zen':'hi'},event='ping') as r:self.assertEqual(r.status,200)
            with self.hook({'ref':'refs/heads/other'}) as r:self.assertEqual(json.load(r),{'queued':False})
            with self.hook({'ref':'refs/heads/main'},event='issues') as r:self.assertEqual(json.load(r),{'queued':False})
            self.assertFalse(req.exists())
            with self.hook({'ref':'refs/heads/main'}) as r:
                self.assertEqual(r.status,202);self.assertEqual(json.load(r),{'queued':True})
            self.assertTrue(req.exists())
        with self.request('/api/deploy-hook') as r:self.assertEqual(r.status,404)
        with self.request('/api/other',method='POST') as r:self.assertEqual(r.status,501)

if __name__=='__main__':unittest.main()
