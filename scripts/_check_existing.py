import json, os

pro_result = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_pro_official/pro_eval_results.json"
if os.path.exists(pro_result):
    with open(pro_result) as f:
        data = json.load(f)
    print("=== Pro Eval Results ===")
    n = data.get("n_total", 0)
    r = data.get("n_resolved", 0)
    e = data.get("n_errors", 0)
    print(f"Total={n} Resolved={r} Errors={e}")
    for iid, res in sorted(data.get("results", {}).items()):
        short = iid[:60]
        if "error" in res:
            print(f"  {short} -> ERROR: {res[error][:40]}")
        else:
            status = "RESOLVED" if res.get("resolved") else "FAILED"
            f2p_ok = res.get("f2p_passed", 0)
            f2p_n = res.get("n_f2p", 0)
            p2p_ok = res.get("p2p_passed", 0)
            p2p_n = res.get("n_p2p", 0)
            print(f"  {short} -> {status} (f2p={f2p_ok}/{f2p_n}, p2p={p2p_ok}/{p2p_n})")
else:
    print("No Pro eval results found")

for p in [
    "/mnt/d/condiag/experiments/canonical_base_eval_matrix.csv",
    "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/canonical_base_eval_matrix.csv",
]:
    if os.path.exists(p):
        print(f"\n=== Canonical Matrix ({p}) ===")
        with open(p) as f:
            for line in f:
                print(f"  {line.strip()}")
        break
else:
    print("\nNo canonical matrix found")

preds_path = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/contextbench_input/preds_all.jsonl"
with open(preds_path) as f:
    preds_list = [json.loads(l) for l in f if l.strip()]

print(f"\n=== All {len(preds_list)} instances ===")
for d in sorted(preds_list, key=lambda x: x["instance_id"]):
    iid = d["instance_id"]
    patch = d.get("model_patch", "")
    plen = len(patch.strip())
    short = iid[:65]
    if plen < 20:
        print(f"  {short} -> EMPTY ({plen} chars)")
    else:
        print(f"  {short} -> patch {plen} chars")
