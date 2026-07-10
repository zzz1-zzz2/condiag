"""Check all 3 fixes work correctly. Uses same flow as actual pipeline."""
import json
from pathlib import Path

import sys
sys.path.insert(0, "/home/swelite/condiag")

INSTANCE = "django__django-12125"
BASE_COMMIT = "89d41cba392b759732ba9f1db4ff29ed47da6a56"
ATTEMPT_1 = Path(
    "/mnt/d/condiag-artifacts/condiag/v0/batch2_d4_7/runs/miniswe/base_miniswe/"
    f"{INSTANCE}/attempt_1"
)
TAXONOMY_PATH = Path("/mnt/d/condiag-artifacts/condiag/v0/pathology_taxonomy.json")
WS = Path.home() / "condiag" / "workspaces" / INSTANCE / "repo_base"
OUT_DIR = Path("/mnt/d/condiag-artifacts/condiag/v0/verify_fixes")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 1. Load raw runtime_signals dict (same as pipeline)
rs_dict = json.loads((ATTEMPT_1 / "runtime_signals.json").read_text())
print(f"1. Loaded rs_dict: type=dict, instance_id={rs_dict.get('instance_id')}")

# 2. Load taxonomy
taxonomy_dict = json.loads(TAXONOMY_PATH.read_text())

# 3. Classify via retry_trigger (same as baseline_handlers)
from experiments.retry_trigger import classify as rt_classify
rt = rt_classify(rs_dict)
print(f"2. RetryTriggerResult: type={rt.trigger_type}, retry={rt.should_retry}")

# 4. Run packet_only with repo_path (the fix)
from experiments.condiag_packet_only import run_packet_only

issue = "When using __all__ in a Field.__init__, MigrationWriter.serialize() crashes with TypeError: 'int' object is not iterable."

report = run_packet_only(
    attempt_1_dir=ATTEMPT_1,
    intervention_dir=OUT_DIR / "intervention",
    instance_id=INSTANCE,
    agent="miniswe",
    model="deepseek/deepseek-v4-pro",
    trigger_result=rt,
    taxonomy_path=TAXONOMY_PATH,
    repo_path=WS,
    base_commit=BASE_COMMIT,
    issue=issue,
)

executed = report.get("executed_actions_summary", {})
selected_count = report.get("selected_evidence_count", 0)
packet_chars = report.get("context_packet_chars", 0)
print(f"3. PIPELINE RESULT:")
print(f"   repo_status={report.get('repo_status','?')}")
print(f"   actions: {executed.get('done',0)} done, {executed.get('skipped',0)} skipped")
print(f"   selected_evidence={selected_count}")
print(f"   packet_chars={packet_chars}")

if packet_chars > 1000:
    packet = (OUT_DIR / "intervention" / "context_packet.md").read_text()
    ev = json.loads((OUT_DIR / "intervention" / "selected_evidence.json").read_text())
    print(f"4. PACKET PREVIEW:")
    for line in packet.splitlines()[:12]:
        print(f"   {line}")
    print(f"   ... ({packet_chars} chars, {len(packet.splitlines())} lines)")
    print(f"   evidence items: {len(ev.get('evidence',[]))}")
    print(f"\n✅ PIPELINE WORKS WITH FIXES")
else:
    print(f"\n❌ PACKET TOO SMALL, need debugging")

