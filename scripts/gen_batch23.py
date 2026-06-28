"""Split remaining 40 Pilot50 instances into Batch2 (20) + Batch3 (20).

Batch1 (already done): 8 Django + 2 sympy
Batch2 strategy: All 22 non-sympy remaining, take 20 (keep NOOP for Batch3 mix)
Batch3 strategy: remaining 2 non-sympy + 13 sympy + 5 NOOP controls

Layer sharing: Batch2 mostly Django + non-sympy local; Batch3 sympy block.
Pathology mix target per memory:
  - Django EC/system-check (RELOCALIZE seeds): top priority in Batch1/Batch2
  - Django config/database (localization pathology): spread
  - Non-Django Python: spread (sympy + transformers + sklearn + astropy)
  - NOOP controls: Batch3 to test false positive
"""
from pathlib import Path

SELECTED = Path("/mnt/d/condiag-artifacts/condiag/v0/pilot50/pilot50_selected.txt")
BATCH1 = Path("/mnt/d/condiag-artifacts/condiag/v0/pilot50/pilot50_batch1.txt")
OUT_DIR = Path("/mnt/d/condiag-artifacts/condiag/v0/pilot50")

all_ids = [l.strip() for l in SELECTED.read_text().splitlines() if l.strip()]
batch1 = [l.strip() for l in BATCH1.read_text().splitlines() if l.strip()]
remaining = [x for x in all_ids if x not in set(batch1)]

# Split by group
def group(i: str) -> str:
    if i.startswith("django"):
        return "django"
    if i.startswith("sympy"):
        return "sympy"
    return "other"

# Pre-designated NOOP controls (last 5 in selected list — easier controls)
NOOP_IDS = {
    "django__django-11163",
    "django__django-11433",
    "django__django-11555",
    "django__django-12193",
    "django__django-12262",
}

other = [x for x in remaining if group(x) == "other"]
django = [x for x in remaining if group(x) == "django" and x not in NOOP_IDS]
sympy = [x for x in remaining if group(x) == "sympy"]
noop = [x for x in remaining if x in NOOP_IDS]

print(f"remaining={len(remaining)} other={len(other)} django={len(django)} sympy={len(sympy)} noop={len(noop)}")

# Batch2 (20): other (4) + 13 django + 3 sympy  → RELOCALIZE candidates spread
# Batch3 (20): 5 django + 10 sympy + 5 NOOP   → NOOP at end (false-positive test)
batch2 = other + django[:13] + sympy[:3]
remaining_django = [x for x in django if x not in set(batch2)]
remaining_sympy = [x for x in sympy if x not in set(batch2)]
batch3 = remaining_django + remaining_sympy + noop

assert len(batch2) == 20, f"batch2 has {len(batch2)}"
assert len(batch3) == 20, f"batch3 has {len(batch3)}"
assert set(batch2) | set(batch3) == set(remaining), "coverage hole"
assert not (set(batch2) & set(batch3)), "overlap"

(OUT_DIR / "pilot50_batch2.txt").write_text("\n".join(batch2) + "\n")
(OUT_DIR / "pilot50_batch3.txt").write_text("\n".join(batch3) + "\n")
print(f"\nWrote batch2 ({len(batch2)}) and batch3 ({len(batch3)}).")
print("\nBatch2:")
for x in batch2: print(f"  {x}")
print("\nBatch3:")
for x in batch3: print(f"  {x}")
