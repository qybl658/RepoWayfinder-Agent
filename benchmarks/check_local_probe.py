"""Model-free/offline discrimination: only a fresh owned loopback HTTP process."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
SERVER='''
from http.server import HTTPServer, BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body=b"PROBE-OK"
        self.send_response(200)
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self,*args): pass
server=HTTPServer(("127.0.0.1",0),Handler)
print(server.server_port,flush=True)
server.serve_forever()
'''
def main():
    host=shutil.which('pwsh')
    if not host: raise RuntimeError('Reuse existing pwsh; do not install anything.')
    server=subprocess.Popen([sys.executable,'-I','-u','-c',SERVER],cwd=ROOT,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf8')
    results=[]
    try:
        port=int(server.stdout.readline())
        for state in ['ready','closed']:
            if state=='closed':
                server.terminate();server.wait(timeout=10)
            command=[host,'-NoProfile','-File',str(ROOT/'check_local_probe.ps1'),
                '-TargetPort',str(port),'-OwnedPidValue',str(server.pid),'-ExpectedState',state]
            output=subprocess.check_output(command,cwd=ROOT,text=True,encoding='utf8',errors='replace')
            result=json.loads(output)
            for method in result['methods']:method['seconds']=round(method['seconds'],6)
            results.append(result)
        assert all(m['correct'] for r in results for m in r['methods'])
    finally:
        if server.poll() is None:server.terminate();server.wait(timeout=10)
    obj={'passed':True,'model_calls':0,'external_network_calls':0,'dependencies_installed':0,
        'owned_server_stopped':server.poll() is not None,'results':results,
        'scope':'One ready and one closed controlled state, fresh owned 127.0.0.1 server. Within each fresh pwsh process test original pattern then HTTP/socket/PID; ordering and module warm-up are not randomized.',
        'interpretation':'Tests command-pattern latency and correct detection, not full model task savings. Original patterns include sleeps only if initial state mismatches; explicit sleep is recorded. HTTP checks known body for readiness; socket/PID closure checks do not replace required business HTTP assertions.'}
    (ROOT/'local-probe-verification.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(obj,ensure_ascii=False))
if __name__=='__main__':main()
