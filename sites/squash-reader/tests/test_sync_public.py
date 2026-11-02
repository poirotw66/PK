"""Exercise the actual UTF-8 transport and confirmed-revision watcher."""
import hashlib
import hmac
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import sync_public


class SyncTests(unittest.TestCase):
    def test_utf8_body_and_signature_reach_the_http_server(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length']))
                expected=hmac.new(b'test-secret',self.headers['X-Sync-Time'].encode()+b'\n'+raw,hashlib.sha256).hexdigest()
                received.append((json.loads(raw),hmac.compare_digest(expected,self.headers['X-Sync-Signature'])))
                body=b'{"version":"test-version"}'
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(sync_public,'SITE',f'http://127.0.0.1:{server.server_port}'):
                sync_public.publish('/sync/update',{'version':'test-version','name':'測試 " 名單\n換行'},'test-secret')
            self.assertEqual(received,[({'version':'test-version','name':'測試 " 名單\n換行'},True)])
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_only_changed_confirmed_versions_are_sent(self):
        first={'version':'a','revision_id':'one','matches':[]}
        second={'version':'b','revision_id':'two','matches':[]}
        with patch.object(sync_public,'PublicFeed') as feed, patch.object(sync_public,'get_secret',return_value='test-secret'), patch.object(sync_public,'publish') as publish, patch.object(sync_public.time,'sleep'):
            feed.return_value.read.side_effect=[first,first,second,KeyboardInterrupt()]
            with self.assertRaises(KeyboardInterrupt): sync_public.run(object())
            self.assertEqual([call.args[1]['version'] for call in publish.call_args_list],['a','b'])

    def test_transport_failure_retries_without_dropping_the_revision(self):
        payload={'version':'a','revision_id':'one','matches':[]}
        with patch.object(sync_public,'PublicFeed') as feed, patch.object(sync_public,'get_secret',return_value='test-secret'), patch.object(sync_public,'publish',side_effect=[RuntimeError('Temporary failure'),{}]) as publish, patch.object(sync_public.time,'sleep'), patch.object(sync_public.LOG,'error'):
            feed.return_value.read.side_effect=[payload,payload,KeyboardInterrupt()]
            with self.assertRaises(KeyboardInterrupt): sync_public.run(object())
            self.assertEqual(publish.call_count,2)
            self.assertEqual(publish.call_args_list[0].args[1],publish.call_args_list[1].args[1])


if __name__=='__main__': unittest.main()
