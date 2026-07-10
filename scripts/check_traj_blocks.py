"""Check traj files for explore_context/PATCH_CONTEXT blocks."""
import json, os

paths = [
    "/mnt/d/condiag-artifacts/runs/condiag_batch3_20260706_205758/miniswe/Verified/django__django-11163/django__django-11163.traj.json",
    "/mnt/d/condiag-artifacts/runs/condiag_batch5b_multi_16_20260709_121629/miniswe/Multi/alibaba__fastjson2-2559/alibaba__fastjson2-2559.traj.json",
    "/mnt/d/condiag-artifacts/runs/condiag_batch5c_pro_16_20260709_125530/miniswe/Pro/instance_ansible__ansible-1a4644ff15355fd696ac5b9d074a566a80fe7ca3-v30a923fb5c164d6cd18280c02422f75e611e8fb2/instance_ansible__ansible-1a4644ff15355fd696ac5b9d074a566a80fe7ca3-v30a923fb5c164d6cd18280c02422f75e611e8fb2.traj.json",
    "/mnt/d/condiag-artifacts/runs/condiag_batch5a_verified_11_20260708_161113/miniswe/Verified/django__django-11433/django__django-11433.traj.json",
]

for p in paths:
    d = json.load(open(p))
    msgs = d.get("messages", [])
    explore = sum(1 for m in msgs if "<explore_context>" in (m.get("content") or ""))
    patch = sum(1 for m in msgs if "<PATCH_CONTEXT>" in (m.get("content") or ""))
    short = os.path.basename(p)[:50]
    print(f"{short}: msgs={len(msgs)}, explore={explore}, patch={patch}")