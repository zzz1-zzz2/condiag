"""Generate predictions.jsonl from batch1 + batch2 base_miniswe attempt_1 patches."""
import json, os

preds = []

b2 = "/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe"
for d in sorted(os.listdir(b2)):
    pf = os.path.join(b2, d, "attempt_1", "patch.diff")
    if os.path.isfile(pf):
        p = open(pf).read()
        preds.append({"instance_id": d, "model_name_or_path": "miniswe_v4pro_batch2", "model_patch": p})

b1 = "/mnt/d/condiag-artifacts/runs/pilot50_batch1_20260627_234801/miniswe/Verified"
for d in sorted(os.listdir(b1)):
    tf = os.path.join(b1, d, d + ".traj.json")
    if os.path.isfile(tf):
        tr = json.load(open(tf))
        s = (tr.get("info") or {}).get("submission") or ""
        if s:
            preds.append({"instance_id": d, "model_name_or_path": "miniswe_v4pro_batch1", "model_patch": s})

out = "/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/predictions.jsonl"
with open(out, "w") as f:
    for p in preds:
        f.write(json.dumps(p) + "\n")

print(f"Wrote {len(preds)} predictions to {out}")
for p in preds:
    print(f"  {p['instance_id']:45s} | patch_chars={len(p['model_patch'])}")
