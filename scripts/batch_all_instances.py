"""Batch run all 19 available instances and report results."""
import json, sys, re, traceback
sys.path.insert(0, "/home/swelite/condiag")
from pathlib import Path
from experiments.condiag_packet_only import run_packet_only

RUNS_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
OUTPUT_BASE = Path("/tmp/condiag-v2-check-all")

# Collect all instances with complete data
INSTANCES = []
for p in sorted((RUNS_ROOT / "condiag_packet_only").iterdir()):
    inst = p.name
    parts = inst.split("__")
    short = parts[1] if len(parts) == 2 else inst

    # Check required files
    a1 = RUNS_ROOT / "base_miniswe" / inst / "attempt_1" / "runtime_signals.json"
    trig = p / "intervention" / "retry_trigger_result.json"
    ws = Path(f"/home/swelite/condiag/workspaces/{inst}/repo_base")

    if a1.exists() and trig.exists() and ws.exists():
        # Extract base_commit from existing run metadata
        old_exec = p / "intervention" / "executed_actions.json"
        if old_exec.exists():
            try:
                meta = json.loads(old_exec.read_text())
                rr = meta.get("repo_resolution") or {}
                bc = rr.get("base_commit_expected", "")
                if bc:
                    INSTANCES.append((short, inst, bc))
                    continue
            except:
                pass
        # fallback: read from trigger
        trigger = json.loads(trig.read_text())
        INSTANCES.append((short, inst, trigger.get("base_commit", "")))

# Also load issue text from attempt_1 data
def load_issue_from_trajectory(a1_dir: Path) -> str:
    """Extract PR description from raw_trajectory.json first user message."""
    traj = a1_dir / "raw_trajectory.json"
    if not traj.exists():
        return ""
    try:
        d = json.loads(traj.read_text())
        for m in d.get("messages", []):
            if m.get("role") == "user":
                content = ""
                if isinstance(m.get("content"), str):
                    content = m["content"]
                elif isinstance(m.get("content"), list):
                    for c in m["content"]:
                        if isinstance(c, dict) and c.get("text"):
                            content = c["text"]
                            break
                # Extract between <pr_description> tags
                m2 = re.search(r'<pr_description>\s*(.+?)\s*</pr_description>', content, re.DOTALL)
                if m2:
                    return m2.group(1).strip()
                return content[:500]
    except:
        pass
    return ""

print(f"Total instances to process: {len(INSTANCES)}")
print()

results = []
for short, inst, base_commit in INSTANCES:
    print(f"\n{'='*80}")
    print(f"  [{len(results)+1}/{len(INSTANCES)}] {short}")
    print(f"  base_commit={base_commit[:16]}...")
    print(f"{'='*80}")
    sys.stdout.flush()

    attempt_1_dir = RUNS_ROOT / "base_miniswe" / inst / "attempt_1"
    trigger_path = RUNS_ROOT / "condiag_packet_only" / inst / "intervention" / "retry_trigger_result.json"
    repo_path = Path(f"/home/swelite/condiag/workspaces/{inst}/repo_base")
    out_dir = OUTPUT_BASE / short

    # Load trigger
    trigger_data = json.loads(trigger_path.read_text())
    class TriggerResult:
        pass
    tr = TriggerResult()
    tr.trigger_type = trigger_data.get("trigger_type", "")
    tr.trigger_reason = trigger_data.get("trigger_reason", [])
    tr.should_retry = trigger_data.get("should_retry", False)
    tr.confidence = trigger_data.get("confidence", 0.0)
    tr.runtime_gap_status = trigger_data.get("runtime_gap_status", "")

    # Load issue
    issue = load_issue_from_trajectory(attempt_1_dir)

    try:
        rr = run_packet_only(
            attempt_1_dir=attempt_1_dir,
            intervention_dir=out_dir,
            instance_id=short,
            agent="miniswe",
            model="deepseek-v4",
            trigger_result=tr,
            repo_path=repo_path,
            base_commit=base_commit,
            issue=issue,
        )

        selected = json.loads((out_dir / "selected_evidence.json").read_text())
        evidence = selected.get("evidence", [])
        packet_text = (out_dir / "context_packet.md").read_text()
        packet_lines = len(packet_text.splitlines())

        # Relations present
        relations = sorted(set(e.get("relation", "") for e in evidence))

        # Primary target
        target_file = ""
        target_goal = ""
        parts = packet_text.split("## Primary Edit Target")
        if len(parts) > 1:
            section = parts[1].split("##")[0]
            fm = re.search(r"\*\*File\*\*:\s*`([^`]+)`", section)
            gm = re.search(r"\*\*Goal\*\*:\s*(.+?)(?:\n\s*\n|\n#|\Z)", section, re.DOTALL)
            if fm:
                target_file = fm.group(1)
            if gm:
                target_goal = gm.group(1).strip()[:80]

        row = {
            "instance": short,
            "trigger_type": tr.trigger_type,
            "diagnosis": rr["diagnosis"]["context_deficiency_type"],
            "retry_intent": rr["diagnosis"]["retry_intent"],
            "confidence": rr["diagnosis"]["confidence"],
            "paths": ", ".join(sorted(set(e.get("path", "") for e in evidence)))[:80],
            "relations": ", ".join(relations)[:60],
            "evidence_count": len(evidence),
            "packet_lines": packet_lines,
            "packet_chars": len(packet_text),
            "target_file": target_file[:60],
            "target_goal": target_goal[:60],
            "repo_status": rr.get("repo_status", "?"),
        }
        results.append(row)
        print(f"  Diagnosis: {row['diagnosis']}  (conf={rr['diagnosis']['confidence']})")
        print(f"  Trigger: {tr.trigger_type}")
        print(f"  Evidence: {len(evidence)} items, relations={relations}")
        print(f"  Packet: {packet_lines} lines, {len(packet_text)} chars")
        print(f"  Target: {target_file} — {target_goal[:60]}")
        print(f"  Repo: {rr.get('repo_status', '?')}")

    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        results.append({
            "instance": short,
            "trigger_type": tr.trigger_type if 'tr' in dir() else "?",
            "diagnosis": "ERROR",
            "retry_intent": "",
            "confidence": 0.0,
            "paths": "",
            "relations": "",
            "evidence_count": 0,
            "packet_lines": 0,
            "packet_chars": 0,
            "target_file": f"ERROR: {e}",
            "target_goal": "",
            "repo_status": "error",
        })

# Print summary table
print(f"\n\n{'='*80}")
print(f"  SUMMARY: {len(results)} instances")
print(f"{'='*80}")
print()
header = f"{'instance':25s} {'trigger':25s} {'diagnosis':35s} {'ev':3s} {'lines':>5s} {'target_file':40s}"
print(header)
print("-" * len(header))
for r in results:
    diag = r['diagnosis'][:33] + '..' if len(r['diagnosis']) > 35 else r['diagnosis']
    trig = r['trigger_type'][:23] + '..' if len(r['trigger_type']) > 25 else r['trigger_type']
    tf = r['target_file'][:38] + '..' if len(r['target_file']) > 40 else r['target_file']
    print(f"{r['instance']:25s} {trig:25s} {diag:35s} {r['evidence_count']:3d} {r['packet_lines']:5d} {tf:40s}")

# Count stats
diag_counts = {}
for r in results:
    d = r['diagnosis']
    diag_counts[d] = diag_counts.get(d, 0) + 1
print(f"\nDiagnosis distribution:")
for d, c in sorted(diag_counts.items(), key=lambda x: -x[1]):
    print(f"  {d:40s}: {c}")
print(f"  {'TOTAL':40s}: {len(results)}")
print(f"  {'Non-empty evidence':40s}: {sum(1 for r in results if r['evidence_count'] > 0)}")
print(f"  {'Empty evidence':40s}: {sum(1 for r in results if r['evidence_count'] == 0)}")

# Save summary
summary_path = OUTPUT_BASE / "batch_summary.json"
(summary_path).write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"\nSummary saved to {summary_path}")
