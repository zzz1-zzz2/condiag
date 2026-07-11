"""Generate per-baseline predictions.jsonl for SWE-bench harness."""
import json
from pathlib import Path

OUT_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_official_eval_django12125")
PRED_DIR = OUT_ROOT / "predictions"
PRED_DIR.mkdir(parents=True, exist_ok=True)

PATCHES = [
    ("plain_rerun",            "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke/miniswe/plain_rerun/django__django-12125/final/patch.diff"),
    ("feedback_retry",         "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke/miniswe/feedback_retry/django__django-12125/final/patch.diff"),
    ("broad_expansion",        "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke_rerun/miniswe/broad_expansion/django__django-12125/final/patch.diff"),
    ("condiag_retry_v2_alpha", "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke_rerun/miniswe/condiag_retry_v2_alpha/django__django-12125/final/patch.diff"),
]

INSTANCE_ID = "django__django-12125"

print("=== Generating predictions.jsonl ===")
for bl, p in PATCHES:
    patch_text = Path(p).read_text(encoding="utf-8")
    pred = {
        "instance_id": INSTANCE_ID,
        "model_patch": patch_text,
        "model_name_or_path": f"condiag-task6-alpha-e1-{bl}",
    }
    out_path = PRED_DIR / f"predictions.{bl}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(pred, ensure_ascii=False) + "\n")
    print(f"  wrote {out_path}  ({out_path.stat().st_size} bytes, patch={len(patch_text)} chars)")
print()
print("PRED_DIR:", PRED_DIR)
