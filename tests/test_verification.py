"""Existing artifacts, finite commands, and owned loopback HTTP phases."""
import contextlib
import base64
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import venv
from unittest.mock import patch

import verification


SERVER = r'''
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
import base64, json, sys, time
class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body=b'', headers=()):
        self.send_response(status)
        for k,v in headers: self.send_header(k,v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path=='/health': return self.reply(200,b'{"ok":true}')
        if self.path=='/value':
            value=Path('data.json').read_bytes() if Path('data.json').exists() else b'{"value":"initial"}'
            return self.reply(200,value)
        if self.path=='/private':
            if 'user=alice' not in self.headers.get('Cookie',''): return self.reply(302,b'', [('Location','/login')])
            return self.reply(200,b'private')
        if self.path=='/escape': return self.reply(302,b'', [('Location','http://example.invalid/never-contact')])
        if self.path=='/huge': return self.reply(200,b'x'*4096)
        if self.path=='/slow': time.sleep(2); return self.reply(200,b'slow')
        if self.path=='/typed': return self.reply(200,b'{"nested":[true]}')
        if self.path=='/items':
            items=json.loads(Path('items.json').read_text(encoding='utf-8')) if Path('items.json').exists() else []
            return self.reply(200,json.dumps({'items':items},ensure_ascii=False).encode('utf-8'))
        if self.path=='/drip-header':
            self.wfile.write(b'HTTP/1.1 200 OK\r\n'); self.wfile.flush()
            for _ in range(100): self.wfile.write(b'X'); self.wfile.flush(); time.sleep(.05)
            return
        if self.path=='/drip-body':
            self.send_response(200); self.send_header('Content-Length','100'); self.end_headers()
            for _ in range(100): self.wfile.write(b'x'); self.wfile.flush(); time.sleep(.05)
            return
        return self.reply(404,b'not found')
    def do_POST(self):
        raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
        if self.path=='/raw-type':
            return self.reply(200,json.dumps({'content_type':self.headers.get('Content-Type'),'bytes':len(raw),
                                             'body_base64':base64.b64encode(raw).decode('ascii')}).encode())
        if self.path=='/items':
            if self.headers.get('Content-Type','').split(';',1)[0]!='application/json':
                return self.reply(415,b'{"error":"JSON required"}')
            try: value=json.loads(raw.decode('utf-8'))
            except (ValueError,UnicodeError): return self.reply(400,b'{"error":"invalid JSON"}')
            if not isinstance(value,dict) or not isinstance(value.get('text'),str) or not value['text'].strip():
                return self.reply(400,b'{"error":"invalid text"}')
            items=json.loads(Path('items.json').read_text(encoding='utf-8')) if Path('items.json').exists() else []
            item={'id':len(items)+1,'text':value['text']};items.append(item)
            Path('items.json').write_text(json.dumps(items,ensure_ascii=False),encoding='utf-8')
            return self.reply(201,json.dumps(item,ensure_ascii=False).encode('utf-8'))
        form=parse_qs(raw.decode())
        if self.path=='/login': return self.reply(200,b'login', [('Set-Cookie','user='+form['user'][0])])
        if self.path=='/write':
            if 'user=alice' not in self.headers.get('Cookie',''): return self.reply(403,b'forbidden')
            Path('data.json').write_text(json.dumps({'value':form['value'][0]}),encoding='utf-8')
            return self.reply(200,b'saved')
        return self.reply(404,b'not found')
server=ThreadingHTTPServer(('127.0.0.1',int(sys.argv[2])),Handler)
server.serve_forever()
'''


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='verification-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / 'result.json').write_text('{"answer":42}', encoding='utf-8')
        self.checks = [{'type':'json_value', 'path':'result.json', 'pointer':'/answer', 'expected':42}]

    def verify(self, **kw):
        return verification.verify(str(self.root), **kw)

    def service(self, requests=None, **kw):
        (self.root / 'server.py').write_text(SERVER, encoding='utf-8')
        return {'argv':['python','server.py','--port','{port}'],
                'ready':{'path':'/health','status':200,'contains':'true'},
                'requests':requests or [{'method':'GET','path':'/health','status':200}],
                'timeout_seconds':10, **kw}

    def report(self, result):
        return json.loads(Path(result['evidence_path']).read_text(encoding='utf-8'))

    def assert_port_closed(self, port):
        with socket.socket() as probe:
            probe.settimeout(.2)
            self.assertNotEqual(probe.connect_ex(('127.0.0.1',port)),0)

    def test_inspects_existing_dirty_non_git_artifact_without_fresh_claim(self):
        before=(self.root/'result.json').read_bytes()
        result=self.verify(checks=self.checks)
        self.assertTrue(result['ok'],result)
        self.assertIn('not job freshness',result['verification_scope'])
        self.assertEqual((self.root/'result.json').read_bytes(),before)
        self.assertEqual(self.report(result)['execution'],[])

    def test_polluted_artifact_does_not_pass(self):
        (self.root/'result.json').write_text('{"answer":true}',encoding='utf-8')
        result=self.verify(checks=self.checks)
        self.assertFalse(result['ok'])
        self.assertEqual(result['checks_passed'],0)

    def test_evidence_can_stay_inside_task_delivery_without_replacing_old_evidence(self):
        (self.root/'bench-delivery/.checks/old').mkdir(parents=True)
        old=self.root/'bench-delivery/.checks/old/note.txt';old.write_text('keep')
        result=self.verify(checks=self.checks,evidence_directory='bench-delivery/.checks')
        self.assertTrue(result['passed'],result)
        self.assertIn(self.root/'bench-delivery/.checks',Path(result['evidence_path']).parents)
        self.assertFalse((self.root/'.repowayfinder-checks').exists())
        self.assertEqual(old.read_text(),'keep')
        with self.assertRaises(ValueError): self.verify(checks=self.checks,evidence_directory='../escape')

    def test_all_input_validation_precedes_evidence_or_command(self):
        cases=[{'checks':self.checks,'run':{'argv':['python','-c','print(1)']},'unchanged':['../escape']},
               {'checks':self.checks,'run':{'argv':['python'],'bad':True}},
               {'checks':self.checks,'run':{'argv':['python']},'service':self.service()},
               {'checks':self.checks,'service':self.service([{'method':'GET','path':'http://example.invalid','status':200}])},
               {'checks':self.checks,'service':self.service([{'method':'GET','path':'/health','status':200,'json_pointer':'/~bad','expected':True}])},
               {'checks':self.checks,'run':{'argv':['python']},'unchanged':[]},
               {'checks':self.checks,'run':{'argv':['python'],'timeout_seconds':0}},
               {'checks':self.checks,'service':self.service([{'method':'POST','path':'/write','status':200,'json':{},'form':{}}])}]
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError): self.verify(**arguments)
        self.assertFalse((self.root/'.repowayfinder-checks').exists())

    def test_relative_directory_and_drive_root_rejected(self):
        for directory in ('.',self.root.anchor):
            with self.assertRaises(ValueError): verification.verify(directory, self.checks)
        self.assertFalse((self.root/'.repowayfinder-checks').exists())

    def test_redirected_evidence_directory_rejected_without_writes(self):
        outside=self.root/'outside'; outside.mkdir()
        try: (self.root/'.repowayfinder-checks').symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != 'nt': self.skipTest('symlinks unavailable')
            result=subprocess.run(['cmd','/c','mklink','/J',str(self.root/'.repowayfinder-checks'),str(outside)],capture_output=True)
            if result.returncode: self.skipTest('junctions unavailable')
        self.addCleanup(lambda: os.rmdir(self.root/'.repowayfinder-checks'))
        with self.assertRaises(ValueError): self.verify(checks=self.checks)
        self.assertEqual(list(outside.iterdir()),[])

    def test_finite_main_named_command_no_service_guess_or_stdout_leak(self):
        (self.root/'main.py').write_text('print("command completed")',encoding='utf-8')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            result=self.verify(checks=self.checks,run={'argv':['python','main.py']})
        self.assertTrue(result['passed'],result)
        self.assertEqual(output.getvalue(),'')
        self.assertEqual(result['interpreter'],sys.executable)
        report=self.report(result)
        self.assertEqual(len(report['execution']),1)
        self.assertIn('command completed',(Path(result['evidence_path']).parent/'run-1.log').read_text(encoding='utf-8'))
        self.assertFalse((self.root/'.venv').exists())

    def test_command_failure_retains_partial_effect_and_never_repeats(self):
        (self.root/'fail.py').write_text('from pathlib import Path\nPath("count.txt").write_text("once")\nraise SystemExit(3)',encoding='utf-8')
        result=self.verify(checks=self.checks,run={'argv':['python','fail.py']},unchanged=['result.json'])
        self.assertFalse(result['passed'])
        self.assertEqual(len(self.report(result)['execution']),1)
        self.assertEqual((self.root/'count.txt').read_text(),'once')
        self.assertTrue(any(e['kind']=='explicit_command_attempt' for e in result['side_effects']))

    def test_existing_task_venv_interpreter_is_reused_without_bootstrap(self):
        environment=self.root/'.venv'
        venv.EnvBuilder(with_pip=False).create(environment)
        python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        configuration=(environment/'pyvenv.cfg').read_bytes()
        (self.root/'interpreter.py').write_text('import sys\nfrom pathlib import Path\nPath("interpreter.txt").write_text(sys.executable)',encoding='utf-8')
        result=self.verify(checks=self.checks,run={'argv':['python','interpreter.py']})
        self.assertTrue(result['passed'],result)
        self.assertEqual(result['interpreter'],str(python))
        self.assertEqual(Path((self.root/'interpreter.txt').read_text()),python)
        self.assertEqual((environment/'pyvenv.cfg').read_bytes(),configuration)

    def test_failed_required_check_or_parse_stops_before_explicit_repeat(self):
        (self.root/'bad.py').write_text('from pathlib import Path\nPath("result.json").write_text("bad JSON")',encoding='utf-8')
        result=self.verify(checks=self.checks,run={'argv':['python','bad.py']},unchanged=['result.json'])
        self.assertFalse(result['passed'])
        self.assertEqual(len(self.report(result)['execution']),1)
        result=self.verify(checks=[{'type':'file_exists','path':'result.json'}],run={'argv':['python','bad.py']},unchanged=['result.json'])
        self.assertFalse(result['passed'])
        self.assertEqual(len(self.report(result)['execution']),1)

    def test_explicit_repeat_compares_parsed_json_not_formatting(self):
        script='from pathlib import Path\nimport json\np=Path("n.txt")\nn=int(p.read_text())+1 if p.exists() else 1\np.write_text(str(n))\nPath("result.json").write_text(json.dumps({"answer":42},indent=n))'
        (self.root/'repeat.py').write_text(script,encoding='utf-8')
        result=self.verify(checks=self.checks,run={'argv':['python','repeat.py']},unchanged=['result.json'])
        self.assertTrue(result['passed'],result)
        self.assertTrue(result['unchanged']['passed'])
        self.assertEqual((self.root/'n.txt').read_text(),'2')

    def test_explicit_repeat_changed_artifact_fails_even_with_passing_checks(self):
        script='from pathlib import Path\np=Path("n.txt")\np.write_text(p.read_text()+"x" if p.exists() else "x")'
        (self.root/'repeat.py').write_text(script,encoding='utf-8')
        result=self.verify(checks=self.checks,run={'argv':['python','repeat.py']},unchanged=['n.txt'])
        self.assertFalse(result['passed'])
        self.assertEqual(result['unchanged']['changed_paths'],['n.txt'])

    def test_explicit_repeat_uses_one_shared_deadline(self):
        (self.root/'slow.py').write_text('import time\ntime.sleep(.3)',encoding='utf-8')
        result=self.verify(checks=self.checks,run={'argv':['python','slow.py'],'timeout_seconds':.5},unchanged=['result.json'])
        self.assertFalse(result['passed'],result)
        self.assertTrue(result['timed_out'])
        self.assertLess(result['seconds'],2)

    def test_real_http_cookie_rejection_restart_and_persistence(self):
        steps=[{'method':'GET','path':'/private','status':302},
               {'method':'POST','path':'/login','status':200,'form':{'user':'alice'},'actor':'alice'},
               {'method':'POST','path':'/login','status':200,'form':{'user':'bob'},'actor':'bob'},
               {'method':'POST','path':'/write','status':403,'form':{'value':'wrong'},'actor':'bob'},
               {'method':'POST','path':'/write','status':200,'form':{'value':'persisted'},'actor':'alice'},
               {'restart':True},
               {'method':'GET','path':'/value','status':200,'json_pointer':'/value','expected':'persisted'},
               {'path':'/private','status':200,'actor':'alice'}]
        result=self.verify(service=self.service(steps))
        self.assertTrue(result['passed'],result)
        self.assertEqual(json.loads((self.root/'data.json').read_text()),{'value':'persisted'})
        starts=[e for e in result['side_effects'] if e['kind']=='service_start']
        self.assertEqual(len(starts),2)
        self.assertTrue(all(e['stopped'] for e in result['side_effects'] if e['kind']=='service_cleanup'))
        self.assert_port_closed(starts[0]['port'])
        summary_path=Path(result['summary_path'])
        self.assertEqual(summary_path.parent,Path(result['evidence_path']).parent)
        summary=summary_path.read_text(encoding='utf-8')
        self.assertIn('HTTP verification: PASS',summary)
        self.assertIn('POST "/write" -> HTTP 403',summary)
        self.assertIn('json_value=PASS',summary)
        self.assertIn('RESTART step 6: PASS',summary)
        self.assertIn('CLEANUP owned service #1: PASS',summary)
        self.assertIn('CLEANUP owned service #2: PASS',summary)
        self.assertNotIn('Set-Cookie',summary)
        self.assertNotIn('persisted',summary)

    def test_raw_bad_json_and_invalid_utf8_do_not_insert_and_service_continues(self):
        steps=[{'method':'POST','path':'/items','status':400,
                'body_base64':base64.b64encode(body).decode('ascii'),
                'content_type':'application/json'} for body in (b'{',b'{"text":"\xff"}',b'')]
        steps += [{'method':'POST','path':'/items','status':400,'body_text':'{','content_type':'application/json'},
                  {'method':'POST','path':'/items','status':400,'body_bytes':list(b'{"text":"\xff"}'),
                   'content_type':'application/json'}]
        steps += [{'path':'/items','status':200,'json_pointer':'','expected':{'items':[]}},
                  {'method':'POST','path':'/items','status':201,'json':{'text':'有效记录'},
                   'json_pointer':'/id','expected':1},
                  {'path':'/items','status':200,'json_pointer':'',
                   'expected':{'items':[{'id':1,'text':'有效记录'}]}},
                  {'path':'/health','status':200,'json_pointer':'/ok','expected':True}]
        # Compare the exact bytes received by a real server, including UTF-8
        # multibyte text, empty raw bodies and an invalid UTF-8 byte sequence.
        for body in (b'{', '汉🙂'.encode('utf-8'), b'', b'\xff'):
            representations=[{'body_base64':base64.b64encode(body).decode('ascii')}, {'body_bytes':list(body)}]
            if body != b'\xff': representations.append({'body_text':body.decode('utf-8')})
            for representation in representations:
                steps.append({'method':'POST','path':'/raw-type','status':200,**representation,
                              'json_pointer':'','expected':{'content_type':'application/octet-stream',
                              'bytes':len(body),'body_base64':base64.b64encode(body).decode('ascii')}})
        result=self.verify(service=self.service(steps))
        self.assertTrue(result['passed'],result)
        self.assertEqual(json.loads((self.root/'items.json').read_text(encoding='utf-8')),
                         [{'id':1,'text':'有效记录'}])
        self.assert_port_closed(next(e['port'] for e in result['side_effects'] if e['kind']=='service_start'))

    def test_raw_body_validation_rejects_before_service_or_evidence(self):
        cases=[{'body_base64':'%'}, {'body_base64':'ew=='+'\n'}, {'body_base64':[]},
               {'body_base64':'ew==','json':{}}, {'body_base64':'ew==','form':{}},
               {'body_base64':'','method':'GET'}, {'body_base64':'','method':'HEAD'},
               {'body_base64':'','content_type':'application/json\r\nX-Injected: yes'},
               {'body_base64':'','content_type':'非ASCII'},
               {'body_base64':'','content_type':'x'*201},
               {'content_type':'application/json','json':{}},
               {'body_text':None}, {'body_text':[]}, {'body_text':123}, {'body_text':'\ud800'},
               {'body_bytes':None}, {'body_bytes':'255'}, {'body_bytes':[True]}, {'body_bytes':[1.0]},
               {'body_bytes':[-1]}, {'body_bytes':[256]}, {'body_bytes':['1']},
               {'body_text':'{','body_bytes':[123]}, {'body_text':'{','body_base64':'ew=='},
               {'body_bytes':[123],'body_base64':'ew=='}, {'body_text':'','json':{}},
               {'body_bytes':[],'form':{}}, {'body_text':'','method':'GET'},
               {'body_text':'','method':'HEAD'}, {'body_bytes':[],'method':'GET'},
               {'body_bytes':[],'method':'HEAD'}, {'body_text':'','content_type':'application/json\r\nX: yes'},
               {'body_bytes':[],'content_type':'x'*201}]
        with patch('owned_process.spawn_owned') as spawn:
            for case in cases:
                step={'method':'POST','path':'/items','status':400,**case}
                with self.subTest(case=case), self.assertRaises(ValueError):
                    self.verify(service=self.service([step]))
            spawn.assert_not_called()
        self.assertFalse((self.root/'.repowayfinder-checks').exists())

    def test_raw_text_and_bytes_declaration_limits_precede_effects(self):
        specs=[self.service([{'method':'POST','path':'/items','status':400,**body}])
               for body in ({'body_text':'汉'*512}, {'body_bytes':[255]*512},
                            {'body_base64':base64.b64encode(b'x'*1024).decode('ascii')})]
        with patch.object(verification,'MAX_BODY',1024), patch('owned_process.spawn_owned') as spawn:
            for spec in specs:
                with self.subTest(field=next(k for k in spec['requests'][0] if k.startswith('body_'))):
                    with self.assertRaisesRegex(ValueError,'declaration exceeds'):
                        self.verify(service=spec)
            spawn.assert_not_called()
        self.assertFalse((self.root/'.repowayfinder-checks').exists())

    def test_encoded_and_total_request_payload_limits_precede_effects(self):
        # URL encoding expands UTF-8 bytes while the JSON declaration still
        # fits; test the actual wire limit rather than a matching string length.
        specs=[self.service([{'method':'POST','path':'/write','status':200,
                              'form':{'value':'汉'*120}}]),
               self.service([{'method':'POST','path':'/write','status':200,
                              'form':{'value':'汉'*65}} for _ in range(2)])]
        with patch.object(verification,'MAX_BODY',1024), patch('owned_process.spawn_owned') as spawn:
            for spec in specs:
                self.assertLessEqual(len(json.dumps(spec,ensure_ascii=False).encode('utf-8')),1024)
                with self.subTest(requests=len(spec['requests'])), self.assertRaises(ValueError):
                    self.verify(service=spec)
            spawn.assert_not_called()
        self.assertFalse((self.root/'.repowayfinder-checks').exists())

    def test_wrong_business_status_stops_requests_and_service(self):
        result=self.verify(service=self.service([{'method':'GET','path':'/missing?token=SUMMARY_SECRET','status':200},
                                                 {'method':'POST','path':'/write','status':200,'form':{'value':'never'}}]))
        self.assertFalse(result['passed'])
        self.assertEqual(result['first_failure']['actual'],404)
        self.assertFalse((self.root/'data.json').exists())
        self.assert_port_closed(next(e['port'] for e in result['side_effects'] if e['kind']=='service_start'))
        summary=Path(result['summary_path']).read_text(encoding='utf-8')
        self.assertIn('HTTP verification: FAIL',summary)
        self.assertIn('GET "/missing" [query omitted] -> HTTP 404',summary)
        self.assertIn('status=FAIL',summary)
        self.assertIn('CLEANUP owned service #1: PASS',summary)
        self.assertNotIn('SUMMARY_SECRET',summary)
        self.assertNotIn('POST "/write"',summary)

    def test_external_redirect_is_rejected_not_followed(self):
        result=self.verify(service=self.service([{'method':'GET','path':'/escape','status':302}]))
        self.assertFalse(result['passed'])
        self.assertIn('outside',result['first_failure']['reason'])

    def test_nested_json_types_cannot_false_pass(self):
        result=self.verify(service=self.service([{'method':'GET','path':'/typed','status':200,'json_pointer':'','expected':{'nested':[1]}}]))
        self.assertFalse(result['passed'])

    def test_http_body_limit_is_explicit_and_local_evidence_is_bounded(self):
        with patch.object(verification,'MAX_BODY',1024):
            result=self.verify(service=self.service([{'method':'GET','path':'/huge','status':200}]))
        self.assertFalse(result['passed'])
        self.assertTrue(result['first_failure']['body_truncated'])
        self.assertEqual((Path(result['evidence_path']).parent/'request-1.body').stat().st_size,1024)

    def test_request_declaration_size_fails_before_service_or_evidence(self):
        spec=self.service([{'method':'POST','path':'/write','status':200,'form':{'value':'x'*4096}}])
        with patch.object(verification,'MAX_BODY',1024), self.assertRaises(ValueError): self.verify(service=spec)
        self.assertFalse((self.root/'.repowayfinder-checks').exists())

    def test_service_start_failure_records_and_cleanup_does_not_touch_inputs(self):
        spec=self.service();spec['argv']=[str(self.root/'missing-SUMMARY_SECRET.exe')]
        result=self.verify(service=spec)
        self.assertFalse(result['passed'])
        self.assertTrue((self.root/'result.json').exists())
        summary=Path(result['summary_path']).read_text(encoding='utf-8')
        self.assertIn('HTTP verification: FAIL',summary)
        self.assertNotIn('SUMMARY_SECRET',summary)

    def test_occupied_port_fails_before_evidence_and_keeps_unrelated_listener(self):
        with socket.socket() as existing:
            existing.bind(('127.0.0.1',0));existing.listen()
            port=existing.getsockname()[1]
            with self.assertRaises(ValueError): self.verify(service=self.service(port=port))
            self.assertFalse((self.root/'.repowayfinder-checks').exists())
            with socket.create_connection(('127.0.0.1',port),timeout=1): pass

    def test_service_deadline_cleans_owned_process(self):
        result=self.verify(service=self.service([{'method':'GET','path':'/slow','status':200}],timeout_seconds=.5))
        self.assertFalse(result['passed'])
        self.assertTrue(result['timed_out'])
        self.assertLess(result['seconds'],2)
        self.assert_port_closed(next(e['port'] for e in result['side_effects'] if e['kind']=='service_start'))

    def test_slow_drip_headers_and_body_cannot_outlive_phase_deadline(self):
        for path in ('/drip-header','/drip-body'):
            with self.subTest(path=path):
                result=self.verify(service=self.service([{'path':path,'status':200}],timeout_seconds=.5))
                self.assertFalse(result['passed'],result)
                self.assertTrue(result['timed_out'],result)
                self.assertLess(result['seconds'],2)
                self.assert_port_closed(next(e['port'] for e in result['side_effects'] if e['kind']=='service_start'))


if __name__ == '__main__':
    unittest.main()
