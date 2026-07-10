"""Batch verify: run fixed pipeline on all 5 first-failed instances."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/swelite/condiag")

# All first-failed instances with their actual batch paths
INSTANCE_CONFIG = [
    ("django__django-11820", "d4_9_batch2_17x4"),
    ("django__django-12125", "batch2_d4_7"),
    ("django__django-13513", "batch2_d4_7"),
    ("django__django-16454", "d4_9_batch2_17x4"),
    ("sympy__sympy-20428",   "batch2_d4_7"),
]

ARTIFACTS_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0")
TAXONOMY_PATH = ARTIFACTS_ROOT / "pathology_taxonomy.json"
OUT_DIR = ARTIFACTS_ROOT / "verify_fixes"
WS_ROOT = Path.home() / "condiag" / "workspaces"

from experiments.retry_trigger import classify as rt_classify
from experiments.condiag_packet_only import run_packet_only
from experiments.manifest_builder import get_problem_statement, get_base_commit, get_gold_patch

results = []
for instance_id, batch_tag in INSTANCE_CONFIG:
    print(f"\n{'='*70}")
    print(f"INSTANCE: {instance_id}  (batch={batch_tag})")
    print(f"{'='*70}")

    attempt_1 = ARTIFACTS_ROOT / batch_tag / "runs" / "miniswe" / "base_miniswe" / instance_id / "attempt_1"
    if not attempt_1.is_dir():
        print(f"  ❌ attempt_1 dir not found: {attempt_1}")
        # Try alternative paths
        alt = ARTIFACTS_ROOT / "batch2_d4_7" / "runs" / "miniswe" / "base_miniswe" / instance_id / "attempt_1"
        alt2 = ARTIFACTS_ROOT / "d4_9_batch2_17x4" / "runs" / "miniswe" / "base_miniswe" / instance_id / "attempt_1"
        print(f"     tried: {alt} -> {alt.is_dir()}")
        print(f"     tried: {alt2} -> {alt2.is_dir()}")
        results.append({"instance": instance_id, "status": "NO_ATTEMPT_1_DIR"})
        continue

    rs_path = attempt_1 / "runtime_signals.json"
    if not rs_path.is_file():
        print(f"  ❌ runtime_signals.json not found")
        results.append({"instance": instance_id, "status": "NO_RS"})
        continue

    # 1. Load runtime_signals
    rs_dict = json.loads(rs_path.read_text())
    exit_st = rs_dict.get("exit_status", "?")
    print(f"  1. Loaded rs_dict, exit_status={exit_st}")

    # 2. Classify via retry_trigger
    rt = rt_classify(rs_dict)
    print(f"  2. Trigger: type={rt.trigger_type}, retry={rt.should_retry}")
    for r in rt.trigger_reason:
        print(f"     reason: {r[:120]}")

    # 3. Resolve repo path
    ws = WS_ROOT / instance_id / "repo_base"
    repo_path = ws if ws.is_dir() else None
    base_commit = get_base_commit(instance_id)
    print(f"  3. Repo: {'✅ ' + str(repo_path) if repo_path else '❌ no repo'}")
    print(f"     base_commit={base_commit[:16] if base_commit else 'N/A'}")

    # 4. Run pipeline
    issue = get_problem_statement(instance_id)
    issue_preview = issue.replace("\n", " ").strip()[:120]
    print(f"     Issue: {issue_preview}...")

    instance_out = OUT_DIR / instance_id
    try:
        report = run_packet_only(
            attempt_1_dir=attempt_1,
            intervention_dir=instance_out / "intervention",
            instance_id=instance_id,
            agent="miniswe",
            model="deepseek/deepseek-v4-pro",
            trigger_result=rt,
            taxonomy_path=TAXONOMY_PATH,
            repo_path=repo_path,
            base_commit=base_commit,
            issue=issue,
        )

        executed = report.get("executed_actions_summary", {})
        selected_count = report.get("selected_evidence_count", 0)
        packet_chars = report.get("context_packet_chars", 0)
        repo_status = report.get("repo_status", "?")
        diagnosis = report.get("diagnosis", {})

        print(f"  4. RESULT:")
        print(f"     repo_status={repo_status}")
        print(f"     pathology={diagnosis.get('pathology')}")
        print(f"     5r={diagnosis.get('primary_5r_action')}")
        actions_detail = [f"{k}={v}" for k, v in executed.items() if k in ("done","skipped","no_candidates")]
        print(f"     actions: {'; '.join(actions_detail)}")
        print(f"     selected_evidence={selected_count}")
        print(f"     packet_chars={packet_chars}")

        # Packet preview
        if packet_chars > 500:
            packet_path = instance_out / "intervention" / "context_packet.md"
            ev_path = instance_out / "intervention" / "selected_evidence.json"
            if packet_path.is_file():
                packet = packet_path.read_text()
                ev = json.loads(ev_path.read_text()) if ev_path.is_file() else {"evidence": []}
                ev_items = ev.get("evidence", [])
                print(f"     packet_lines={len(packet.splitlines())}")
                print(f"     evidence_items={len(ev_items)}")
                for e in ev_items[:4]:
                    print(f"       ev: {e.get('id','?')} op={e.get('operation','?')} "
                          f"path={e.get('path','?'):40s} lines={e.get('start_line','?')}-{e.get('end_line','?')} "
                          f"score={e.get('score','?')}")

        results.append({
            "instance": instance_id,
            "status": "OK",
            "trigger": rt.trigger_type,
            "pathology": diagnosis.get("pathology"),
            "5r": diagnosis.get("primary_5r_action"),
            "actions_done": executed.get("done", 0),
            "actions_skipped": executed.get("skipped", 0),
            "evidence": selected_count,
            "packet_chars": packet_chars,
            "repo": repo_status,
            "packet_path": str(instance_out / "intervention" / "context_packet.md"),
        })
    except Exception as e:
        import traceback
        print(f"  ❌ ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        results.append({"instance": instance_id, "status": f"ERROR:{type(e).__name__}"})

# Summary
print(f"\n\n{'='*70}")
print("SUMMARY")
print(f"{'='*70}")
header = f"{'Instance':30s} {'Trigger':22s} {'Pathology':30s} {'5R':12s} {'Done':>5s} {'Skip':>5s} {'Ev':>3s} {'Chars':>6s} {'Repo':20s}"
print(header)
print("-" * 140)
for r in results:
    if r.get("status") == "OK":
        print(f"{r['instance']:30s} {r['trigger']:22s} {r['pathology']:30s} {r['5r']:12s} "
              f"{r['actions_done']:5d} {r['actions_skipped']:5d} {r['evidence']:3d} {r['packet_chars']:6d} {r['repo']:20s}")
    else:
        print(f"{r['instance']:30s} ❌ {r.get('status','?'):60s}")
