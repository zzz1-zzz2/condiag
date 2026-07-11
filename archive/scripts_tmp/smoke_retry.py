"""Smoke run retry on 3 cases."""
import sys, os
sys.path.insert(0, "/home/swelite/condiag")
os.environ["DEEPSEEK_API_KEY"] = "sk-1a92a89bf90e447497a715994bb01dd6"
from experiments.retry_executor import smoke_retry_3cases
r = smoke_retry_3cases()
print()
print("=== RESULTS ===")
for k, v in sorted(r.items()):
    if "error" in v:
        print(f"  {k}: ERROR={v['error']}")
    else:
        print(f"  {k}: patch={v['patch_chars']} has_patch={v['has_patch']}")
