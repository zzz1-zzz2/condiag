"""D4-9 Step 3 summary generator.

Reads batch2_17x4_compare_matrix.csv and produces:
  - batch2_17x4_acceptance.md  (12 acceptance criteria check)
  - batch2_17x4_compare_summary.md  (baseline-level aggregates)
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4")
MATRIX_CSV = ROOT / "batch2_17x4_compare_matrix.csv"
ACCEPTANCE_MD = ROOT / "batch2_17x4_acceptance.md"
SUMMARY_MD = ROOT / "batch2_17x4_compare_summary.md"

BASELINES = ["base_miniswe", "feedback_retry", "broad_expansion", "condiag_packet_only"]


def _b(r: bool | None) -> str:
    return "✓" if r else "✗"


def _is_true(v) -> bool:
    """CSV round-trips booleans as strings; coerce properly."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def _section_acceptance(rows: list[dict]) -> list[str]:
    """12 acceptance criteria from user spec."""
    by_baseline = defaultdict(list)
    for r in rows:
        by_baseline[r["baseline"]].append(r)

    out: list[str] = []
    out.append("# D4-9 Step 3 Acceptance Check")
    out.append("")
    out.append(f"Total runs: {len(rows)} (expect 17 × 4 = 68)")
    out.append("")

    # 1. 17 × 4 = 68 rows
    ok = len(rows) == 68
    out.append(f"1. {_b(ok)} 17 × 4 = 68 rows generated ({len(rows)} actual)")

    # 2. validator ok or failure_reason clear
    n_ok = sum(1 for r in rows if r["validator_status"] == "ok")
    n_fail = sum(1 for r in rows if r["validator_status"] not in ("ok", ""))
    ok = n_ok == 68
    out.append(f"2. {_b(ok)} validator_status: {n_ok}/68 ok, {n_fail} failed")

    # 3. leakage_status clean
    n_clean = sum(1 for r in rows if r["leakage_status"] == "clean")
    ok = n_clean == 68
    out.append(f"3. {_b(ok)} leakage_status: {n_clean}/68 clean")

    # 4. base_miniswe has no intervention
    base_rows = by_baseline["base_miniswe"]
    n_with_pkt = sum(1 for r in base_rows if _is_true(r["has_context_packet"]))
    ok = n_with_pkt == 0
    out.append(f"4. {_b(ok)} base_miniswe: no context_packet.md ({n_with_pkt}/{len(base_rows)} have one)")

    # 5. feedback_retry only produces packet when should_retry=True
    fb_rows = by_baseline["feedback_retry"]
    fb_with_pkt = [r for r in fb_rows if _is_true(r["has_context_packet"])]
    fb_should = [r for r in fb_rows if _is_true(r["should_retry"])]
    ok = len(fb_with_pkt) == len(fb_should)
    out.append(
        f"5. {_b(ok)} feedback_retry: packet iff should_retry "
        f"({len(fb_with_pkt)} packets, {len(fb_should)} should_retry)"
    )

    # 6. broad_expansion: should_retry=True → broad_rg or broad_no_repo; else skipped
    be_rows = by_baseline["broad_expansion"]
    be_should = [r for r in be_rows if _is_true(r["should_retry"])]
    be_should_modes = Counter(r["packet_mode"] for r in be_should)
    be_skip = [r for r in be_rows if not _is_true(r["should_retry"])]
    be_skip_modes = Counter(r["packet_mode"] for r in be_skip)
    ok_should = all(m in ("broad_rg", "broad_no_repo") for m in be_should_modes)
    ok_skip = all(m == "skipped_no_retry" for m in be_skip_modes)
    ok = ok_should and ok_skip
    out.append(
        f"6. {_b(ok)} broad_expansion: should_retry modes={dict(be_should_modes)}, "
        f"no_retry modes={dict(be_skip_modes)}"
    )

    # 7. broad_expansion: no selected_evidence / recovery_report
    n_se = sum(1 for r in be_rows if _is_true(r["has_selected_evidence"]))
    n_rr = sum(1 for r in be_rows if _is_true(r["has_recovery_report"]))
    ok = n_se == 0 and n_rr == 0
    out.append(f"7. {_b(ok)} broad_expansion: selected_evidence={n_se}/17 recovery_report={n_rr}/17")

    # 8. condiag_packet_only: recovery_report.json always present
    cp_rows = by_baseline["condiag_packet_only"]
    n_rec = sum(1 for r in cp_rows if _is_true(r["has_recovery_report"]))
    ok = n_rec == 17
    out.append(f"8. {_b(ok)} condiag_packet_only: recovery_report {n_rec}/17")

    # 9. condiag_packet_only: NO_TRIGGER → condiag_noop
    cp_noop = [r for r in cp_rows if r["trigger_type"] == "NO_TRIGGER"]
    cp_noop_modes = Counter(r["packet_mode"] for r in cp_noop)
    ok = cp_noop_modes.get("condiag_noop", 0) == len(cp_noop)
    out.append(
        f"9. {_b(ok)} condiag_packet_only: NO_TRIGGER → condiag_noop "
        f"({cp_noop_modes.get('condiag_noop', 0)}/{len(cp_noop)})"
    )

    # 10. condiag_packet_only: should_retry → retrieval / guard / no_actions
    #     (RESTRAIN→guard is also a real execution path, not a stub)
    cp_retry = [r for r in cp_rows if _is_true(r["should_retry"])]
    cp_retry_modes = Counter(r["packet_mode"] for r in cp_retry)
    n_classified = sum(cp_retry_modes.get(m, 0) for m in (
        "condiag_retrieval", "condiag_guard", "condiag_diagnostic_only_no_actions",
    ))
    ok = n_classified == len(cp_retry)
    out.append(
        f"10. {_b(ok)} condiag_packet_only: should_retry modes={dict(cp_retry_modes)} "
        f"({n_classified}/{len(cp_retry)} classified)"
    )

    # 11. matrix can summarize packet size / candidates / evidence
    avg_pkt = lambda rs: sum(int(r["packet_chars"]) for r in rs) / max(len(rs), 1)
    avg_cand = lambda rs: sum(int(r["num_candidates"]) for r in rs) / max(len(rs), 1)
    avg_evi = lambda rs: sum(int(r["num_selected_evidence"]) for r in rs) / max(len(rs), 1)
    out.append(
        f"11. ✓ packet/candidate/evidence aggregatable: "
        f"broad(pkt={avg_pkt(be_rows):.0f} cand={avg_cand(be_rows):.1f})  "
        f"condiag(pkt={avg_pkt(cp_rows):.0f} evi={avg_evi(cp_rows):.1f})"
    )

    # 12. no leakage hits anywhere
    n_leak = sum(1 for r in rows if "leakage_hits" in r["leakage_status"])
    ok = n_leak == 0
    out.append(f"12. {_b(ok)} no leakage hits in any artifact ({n_leak} found)")

    out.append("")
    passed = sum(1 for line in out if line.startswith(("1. ✓", "2. ✓", "3. ✓", "4. ✓",
                                                         "5. ✓", "6. ✓", "7. ✓", "8. ✓",
                                                         "9. ✓", "10. ✓", "11. ✓", "12. ✓")))
    out.insert(2, f"Passed: {passed}/12")
    out.insert(3, "")
    return out


def _section_aggregates(rows: list[dict]) -> list[str]:
    """Baseline-level aggregates for compare_summary.md."""
    by_baseline = defaultdict(list)
    for r in rows:
        by_baseline[r["baseline"]].append(r)

    out: list[str] = []
    out.append("# D4-9 Step 3 — Batch2 17×4 packet-level compare matrix")
    out.append("")
    out.append("**Scope caveat**: this is a packet-level intervention comparison,")
    out.append("*not* a repair-rate experiment. final = attempt_1 for all baselines.")
    out.append("")
    out.append("## Per-baseline aggregate (n=17 each)")
    out.append("")
    out.append("| baseline | n_packet | avg_pkt_chars | avg_candidates | avg_evidence | avg_actions_done | validator_ok | leakage |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|")

    for bl in BASELINES:
        rs = by_baseline[bl]
        n_pkt = sum(1 for r in rs if r["has_context_packet"])
        avg_pkt = sum(int(r["packet_chars"]) for r in rs) / 17
        avg_cand = sum(int(r["num_candidates"]) for r in rs) / 17
        avg_evi = sum(int(r["num_selected_evidence"]) for r in rs) / 17
        avg_done = sum(int(r["actions_done"]) for r in rs) / 17
        n_vok = sum(1 for r in rs if r["validator_status"] == "ok")
        n_clean = sum(1 for r in rs if r["leakage_status"] == "clean")
        out.append(
            f"| {bl} | {n_pkt}/17 | {avg_pkt:.0f} | {avg_cand:.2f} | {avg_evi:.2f} | "
            f"{avg_done:.2f} | {n_vok}/17 | {n_clean}/17 |"
        )

    out.append("")
    out.append("## packet_mode distribution")
    out.append("")
    for bl in BASELINES:
        rs = by_baseline[bl]
        modes = Counter(r["packet_mode"] or "(empty)" for r in rs)
        out.append(f"- **{bl}**: {dict(modes)}")

    out.append("")
    out.append("## trigger_type distribution (17 instances, same across baselines)")
    out.append("")
    trig = Counter(r["trigger_type"] for r in by_baseline["base_miniswe"])
    # trigger_type is empty for base_miniswe; read from feedback_retry instead
    trig = Counter(r["trigger_type"] for r in by_baseline["feedback_retry"])
    out.append(f"- {dict(trig)}")

    out.append("")
    out.append("## Broad Expansion RG execution")
    out.append("")
    be_rows = by_baseline["broad_expansion"]
    n_rg = sum(1 for r in be_rows if str(r["rg_executed"]).lower() == "true")
    total_q = sum(int(r["rg_queries_count"]) for r in be_rows)
    total_h = sum(int(r["rg_hits_count"]) for r in be_rows)
    out.append(f"- rg_executed=True: {n_rg}/17")
    out.append(f"- rg_queries_total: {total_q}")
    out.append(f"- rg_hits_total: {total_h}")

    out.append("")
    out.append("## ConDiag executed_actions summary")
    out.append("")
    cp_rows = by_baseline["condiag_packet_only"]
    total_done = sum(int(r["actions_done"]) for r in cp_rows)
    total_skip = sum(int(r["actions_skipped"]) for r in cp_rows)
    total_evi = sum(int(r["num_selected_evidence"]) for r in cp_rows)
    out.append(f"- total actions done: {total_done}")
    out.append(f"- total actions skipped: {total_skip}")
    out.append(f"- total selected_evidence: {total_evi}")

    out.append("")
    out.append("## Pathology distribution (condiag_packet_only)")
    out.append("")
    path = Counter(r["pathology"] or "(empty)" for r in cp_rows)
    for k, v in path.most_common():
        out.append(f"- {k}: {v}")

    return out


def main() -> int:
    rows = list(csv.DictReader(MATRIX_CSV.open(encoding="utf-8")))
    acc = _section_acceptance(rows)
    agg = _section_aggregates(rows)
    ACCEPTANCE_MD.write_text("\n".join(acc) + "\n", encoding="utf-8")
    SUMMARY_MD.write_text("\n".join(agg) + "\n", encoding="utf-8")
    print(f"acceptance -> {ACCEPTANCE_MD}")
    print(f"summary     -> {SUMMARY_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
