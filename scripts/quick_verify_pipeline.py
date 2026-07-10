"""Quick verification: pipeline's own synth + auto target_hints."""
import json, sys
from pathlib import Path

sys.path.insert(0, "/home/swelite/condiag")

from condiag import schemas as cschemas, trigger, leakage_guard
from experiments.condiag_packet_only import synthesize_manual_diagnosis

rs_dict = json.loads(Path(
    "/mnt/d/condiag-artifacts/condiag/v0/batch2_d4_7/runs/miniswe/base_miniswe/"
    "django__django-12125/attempt_1/runtime_signals.json"
).read_text())
rs = cschemas.RuntimeSignals.from_dict(rs_dict)
taxonomy = cschemas.PathologyTaxonomy.from_dict(
    json.loads(Path("/mnt/d/condiag-artifacts/condiag/v0/pathology_taxonomy.json").read_text())
)
tr = trigger.classify(rs, taxonomy)

issue = "When using __all__ in a Field.__init__, MigrationWriter.serialize() crashes with TypeError: 'int' object is not iterable."

md = synthesize_manual_diagnosis(tr, rs, agent="miniswe", model="deepseek/deepseek-v4-pro", issue=issue)

print("target_hints:", len(md.target_hints), "items")
kinds = {}
for h in md.target_hints:
    kinds[h["kind"]] = kinds.get(h["kind"], 0) + 1
print("  by kind:", kinds)
print("  samples:", [h for h in md.target_hints if h["kind"] == "symbol"][:5])
print("retrieval_plan:", [s["operation"] for s in md.retrieval_plan])
