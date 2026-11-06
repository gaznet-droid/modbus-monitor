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

if __name__=='__main__':unittest.main()
