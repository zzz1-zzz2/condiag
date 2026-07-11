"""E1 preflight: verify 4 patches, print sizes/md5/changed_files."""
import hashlib
import re
from pathlib import Path

PATCHES = [
    ("plain_rerun",            "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke/miniswe/plain_rerun/django__django-12125/final/patch.diff"),
    ("feedback_retry",         "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke/miniswe/feedback_retry/django__django-12125/final/patch.diff"),
    ("broad_expansion",        "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke_rerun/miniswe/broad_expansion/django__django-12125/final/patch.diff"),
    ("condiag_retry_v2_alpha", "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke_rerun/miniswe/condiag_retry_v2_alpha/django__django-12125/final/patch.diff"),
]

print("=== E1 preflight: 4 patches ===")
all_ok = True
for bl, p in PATCHES:
    path = Path(p)
    if not path.is_file():
        print(f"  {bl:30s} MISSING  {p}")
        all_ok = False
        continue
    data = path.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    text = data.decode("utf-8", errors="replace")
    # count changed files (diff --git lines)
    changed = sorted(set(re.findall(r"^diff --git a/(\S+) b/\S+$", text, re.MULTILINE)))
    print(f"  {bl:30s} bytes={len(data):5d}  md5={md5}  changed_files={len(changed)}  files={changed}")
print()
print("ALL_OK:", all_ok)
