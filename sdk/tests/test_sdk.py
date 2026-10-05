import asyncio
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from loupe import Client, Span, configure, trace, patch_openai, patch_anthropic
from loupe.instrumentation import wrap

class SDKTests(unittest.TestCase):
    def setUp(self):
        self.spans = []
        sink = self.spans
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                sink.extend(body['spans'])
                self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.client = configure(endpoint=f'http://127.0.0.1:{self.server.server_port}', flush_interval=.02)
    def tearDown(self):
        self.client.close(); self.server.shutdown(); self.server.server_close()

    def test_nested_ids_and_privacy(self):
        @trace('tool')
        def child(secret): return secret
        @trace('run')
        def parent(): return child('SECRET')
        self.assertEqual(parent(), 'SECRET'); self.assertTrue(self.client.flush())
        parent_span = next(s for s in self.spans if s['name']=='run')
        child_span = next(s for s in self.spans if s['name']=='tool')
        self.assertEqual(parent_span['trace_id'], child_span['trace_id'])
        self.assertEqual(child_span['parent_span_id'], parent_span['span_id'])
        self.assertEqual(len(parent_span['trace_id']),32)
        self.assertNotIn('SECRET',json.dumps(self.spans))

    def test_exception_identity_preserved(self):
        error=RuntimeError('sensitive exception body')
        @trace()
        def bad(): raise error
        with self.assertRaises(RuntimeError) as caught: bad()
        self.assertIs(caught.exception,error); self.client.flush()
        self.assertEqual(self.spans[0]['status'],'ERROR')
        self.assertNotIn('sensitive',json.dumps(self.spans))

    def test_async_context_isolation(self):
        @trace('child')
        async def child(): await asyncio.sleep(.001)
        @trace('parent')
        async def parent(): await child()
        async def run(): await asyncio.gather(parent(),parent())
        asyncio.run(run()); self.client.flush()
        roots=[s for s in self.spans if s['parent_span_id'] is None]
        self.assertEqual(len(roots),2); self.assertNotEqual(roots[0]['trace_id'],roots[1]['trace_id'])
        self.assertEqual(len(set(s['trace_id'] for s in self.spans)),2)

    def test_failed_export_does_not_block_host(self):
        failed=Client(endpoint='http://127.0.0.1:1',max_retries=0,timeout=.05,flush_interval=.01)
        started=time.monotonic()
        with Span('run',client=failed): pass
        self.assertLess(time.monotonic()-started,.05)
        failed.flush(); self.assertEqual(failed.dropped,1); failed.close()

    def test_queue_overflow_is_counted(self):
        failed=Client(max_queue=1,flush_interval=10)
        gate=threading.Event()
        failed._send=lambda batch: gate.wait(.2)
        for i in range(50): failed.submit({'name':str(i)})
        self.assertGreater(failed.dropped,0); gate.set(); failed.close()

    def test_patch_preserves_response_and_tokens(self):
        response=SimpleNamespace(model='test-model',id='id1',usage=SimpleNamespace(prompt_tokens=4,completion_tokens=3))
        fn=wrap(lambda **kwargs:response,'openai')
        self.assertIs(fn(model='test-model',messages=['private']),response)
        self.client.flush(); self.assertEqual(self.spans[0]['attributes']['gen_ai.usage.input_tokens'],4)
        self.assertNotIn('private',json.dumps(self.spans))
        self.assertIs(wrap(fn,'openai'),fn)

    def test_stream_passthrough(self):
        response=object(); fn=wrap(lambda **kwargs:response,'openai')
        self.assertIs(fn(model='m',stream=True),response); self.client.flush(); self.assertEqual(self.spans,[])

    def test_installed_provider_patches_and_undo(self):
        from openai.resources.chat.completions import Completions
        from anthropic.resources.messages import Messages
        o,a=Completions.create,Messages.create
        undo1=patch_openai(); undo2=patch_anthropic()
        self.assertTrue(Completions.create._loupe_patched); self.assertTrue(Messages.create._loupe_patched)
        undo1();undo2();self.assertIs(Completions.create,o);self.assertIs(Messages.create,a)

    def test_langchain_parenting(self):
        from loupe.langchain import LoupeCallbackHandler
        handler=LoupeCallbackHandler(self.client)
        handler.on_chain_start({'name':'agent'},{},run_id='a')
        handler.on_tool_start({'name':'search'},'secret',run_id='b',parent_run_id='a')
        handler.on_tool_end('result',run_id='b');handler.on_chain_end({},run_id='a')
        self.client.flush(); self.assertEqual(len(self.spans),2)
        tool=next(s for s in self.spans if s['name']=='execute_tool search')
        root=next(s for s in self.spans if s['name']=='agent')
        self.assertEqual(tool['parent_span_id'],root['span_id'])

if __name__=='__main__': unittest.main()
