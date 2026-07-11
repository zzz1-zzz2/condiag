import json, os
base = '/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe'
for inst in ['django__django-12125', 'sympy__sympy-20428']:
    for bl in ['feedback_retry', 'condiag_retry']:
        rp = os.path.join(base, bl, inst, 'run_report.json')
        print(f'=== {inst} {bl} ===')
        if os.path.isfile(rp):
            r = json.load(open(rp))
            pc = r.get('protocol_check') or {}
            print(f"  status={r.get('status')} final={r.get('final_source')} retry_no_change={r.get('retry_no_change')}")
            print(f"  tool_calls={pc.get('tool_calls_count')} valid={pc.get('valid')}")
            for w in r.get('warnings', []):
                print(f"  WARN: {w}")
            for e in r.get('errors', []):
                print(f"  ERROR: {e}")
            # Check patch
            ap = os.path.join(base, bl, inst, 'attempt_2', 'attempt_report.json')
            if os.path.isfile(ap):
                ar = json.load(open(ap))
                print(f"  has_patch={ar.get('has_patch')} patch_chars={ar.get('patch_chars')}")
        else:
            print('  NOT YET')
