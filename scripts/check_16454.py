"""Single-instance packet check for django-16454."""
import json, sys, re
sys.path.insert(0, "/home/swelite/condiag")
from pathlib import Path
from experiments.condiag_packet_only import run_packet_only

instance_id = "django-16454"
dir_name = "django__django-16454"
issue = "The handling of subparsers within an ArgumentParser can lead to errors"
base_commit = "9f159c254cc7a9b7d3d6299c059bccae4a23dc3e"

trigger_path = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/condiag_packet_only/django__django-16454/intervention/retry_trigger_result.json")
trigger_data = json.loads(trigger_path.read_text())

class TriggerResult:
    pass
tr = TriggerResult()
tr.trigger_type = trigger_data.get("trigger_type", "")
tr.trigger_reason = trigger_data.get("trigger_reason", [])
tr.should_retry = trigger_data.get("should_retry", False)
tr.confidence = trigger_data.get("confidence", 0.0)
tr.runtime_gap_status = trigger_data.get("runtime_gap_status", "")

attempt_1_dir = Path("/mnt/d/condiag-artifacts/condiag/v0/attempt_1/django-16454")
repo_path = Path("/mnt/d/condiag-artifacts/contextbench_local/mini_swe_django__django-16454")
out_dir = Path("/tmp/condiag-v2-check/django-16454")

rr = run_packet_only(
    attempt_1_dir=attempt_1_dir,
    intervention_dir=out_dir,
    instance_id=instance_id,
    agent="miniswe",
    model="deepseek-v4",
    trigger_result=tr,
    repo_path=repo_path,
    base_commit=base_commit,
    issue=issue,
)

print("Diagnosis:", rr["diagnosis"]["context_deficiency_type"])

packet = (out_dir / "context_packet.md").read_text()

# Find primary target section
target_section = re.search(
    r"(?:#+)\s*Primary Edit Target\s*\n\s*\n"
    r"\s*-\s*\*\*File\*\*:\s*`([^`]+)`\s*\n\s*\n"
    r"\s*-\s*\*\*Goal\*\*:\s*(.+?)(?:\n\s*\n|\n#|\Z)",
    packet, re.DOTALL
)
if target_section:
    print("Primary target file:", target_section.group(1))
    print("Primary target goal:", target_section.group(2).strip()[:80])
else:
    print("No target found, trying simpler regex...")
    m2 = re.search(r"\*\*File\*\*:\s*`([^`]+)`", packet)
    if m2:
        print("  File:", m2.group(1))
    m3 = re.search(r"\*\*Goal\*\*:\s*(.+?)(?:\n|$)", packet)
    if m3:
        print("  Goal:", m3.group(1).strip()[:80])

print("Packet chars:", len(packet))

print()
print("=== Evidence list ===")
selected = json.loads((out_dir / "selected_evidence.json").read_text())
for ev in selected.get("evidence", []):
    rel = ev.get("relation", "?")
    path = ev.get("path", "?")
    sym = ev.get("symbol", "") or ""
    print(f'  {rel:35s} {path:50s} {sym:30s} L{ev.get("start_line","?")}-{ev.get("end_line","?")}')

# Also show packet if small enough
if len(packet) < 5000:
    print()
    print("=== Packet (full) ===")
    print(packet)
