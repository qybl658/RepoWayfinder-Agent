"""Independent output/rerun checks for the ordinary Grok task; no model needed."""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def verify(root, verification):
    checks = {}
    out = root/'results'
    accepted, rejected = read_csv(out/'accepted.csv'), read_csv(out/'quarantine.csv')
    s = json.loads((out/'summary.json').read_text(encoding='utf-8-sig'))
    checks['row_conservation'] = len(accepted)+len(rejected)==12
    checks['summary_counts'] = all(s.get(k)==v for k,v in {'total_rows':12,'accepted_rows':3,
                                                        'quarantined_rows':9,'accepted_total_cents':3250}.items())
    checks['reason_counts'] = s.get('reason_counts')=={'duplicate':1,'conflicting_id':2,'invalid':6}
    byid = {r['record_id']:r for r in accepted}
    checks['accepted_ids'] = set(byid)=={'A01','B02','I09'}
    checks['chinese_preserved'] = byid['A01']['name']=='实验笔记本' and byid['B02']['name']=='中性笔'
    checks['negative_quantity'] = int(byid['B02']['qty'])==-2
    checks['invalid_raw_preserved'] = any(r.get('date')=='09/30/2026' and r.get('record_id')=='D04' for r in rejected)
    checks['conflicts_preserved'] = sorted(int(r['qty']) for r in rejected if r['record_id']=='C03')==[1,2]
    checks['duplicate_normalization'] = any(r['record_id']=='A01' and r['qty']=='03' for r in rejected)
    checks['integer_totals'] = {key:int(row['total_cents']) for key,row in byid.items()}=={'A01':3750,'B02':-500,'I09':0}
    checks['source_row_numbers'] = sorted(int(r['row_number']) for r in rejected)==[3,5,6,7,8,9,10,11,12]
    checks['script_and_readme'] = (root/'reconcile.py').is_file() and (out/'README.md').is_file()
    before = {'accepted':accepted,'quarantine':rejected,
              'summary':{key:s.get(key) for key in ['total_rows','accepted_rows','quarantined_rows','accepted_total_cents','reason_counts']}}
    rerun = subprocess.run([sys.executable,str(root/'reconcile.py'),'input.csv','--output-dir','results'],cwd=root,
                           capture_output=True,text=True,encoding='utf-8')
    checks['independent_rerun'] = rerun.returncode==0
    after_s=json.loads((out/'summary.json').read_text(encoding='utf-8-sig'))
    checks['rerun_does_not_append'] = before=={'accepted':read_csv(out/'accepted.csv'),'quarantine':read_csv(out/'quarantine.csv'),
        'summary':{key:after_s.get(key) for key in before['summary']}}
    verification.mkdir(exist_ok=False)
    bom_input=verification/'bom-input.csv'
    bom_input.write_bytes(b'\xef\xbb\xbf'+(root/'input.csv').read_bytes().removeprefix(b'\xef\xbb\xbf'))
    bom_out=verification/'bom-results'
    bom_run=subprocess.run([sys.executable,str(root/'reconcile.py'),str(bom_input),'--output-dir',str(bom_out)],
                           cwd=root,capture_output=True,text=True,encoding='utf-8')
    bom_summary=json.loads((bom_out/'summary.json').read_text(encoding='utf-8-sig')) if (bom_out/'summary.json').exists() else {}
    checks['utf8_bom_input'] = bom_run.returncode==0 and before['summary']=={key:bom_summary.get(key) for key in before['summary']}
    return {'passed':all(checks.values()),'checks':checks}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--output',type=Path);p.add_argument('--scratch',type=Path,required=True)
    args=p.parse_args()
    try:result=verify(args.root,args.scratch)
    except Exception as exc:result={'passed':False,'error':f'{type(exc).__name__}: {exc}'}
    if args.output:args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['passed'] else 1)
