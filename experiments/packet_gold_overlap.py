"""D4-9 Step 4 — Packet-vs-gold overlap matrix.

Question: does ConDiag's context_packet overlap gold context (file + line)
more than Feedback Retry's or Broad Expansion's?

Gold definition: lines added/modified in the SWE-bench Verified gold patch.
We parse the unified diff and collect, per file, the line numbers in the
*new* file that were added or modified.

Per-baseline candidate sources (only packet-bearing baselines; base_miniswe
has no intervention and is excluded):

  feedback_retry      context_packet.md  -> "edited files: N (...)" line
                                          (whole-file candidates, no line ranges)
  broad_expansion     broad_candidates.jsonl -> {path, start_line, end_line}
  condiag_packet_only selected_evidence.json -> {path, start_line, end_line}

Metrics per (instance, baseline):
  gold_files / gold_lines              counts from gold patch
  cand_files / cand_lines              counts from candidates (lines expanded)
  file_inter / line_inter              intersection sizes
  file_precision / file_recall / file_f1
  line_precision / line_recall / line_f1
  packet_chars                         size of context_packet.md in bytes
  overlap_per_1k_chars                 line_inter / (packet_chars / 1000)
                                       — efficiency: gold hits per 1k of packet

Outputs (under --out-dir):
  packet_gold_overlap_matrix.csv       one row per (instance, baseline)
  packet_gold_overlap_summary.md       per-baseline aggregates
  packet_gold_overlap_by_instance.md   per-instance side-by-side
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Optional

from experiments.manifest_builder import get_gold_patch


BASELINES = ["feedback_retry", "broad_expansion", "condiag_packet_only"]


# ===== Gold extractor =====

_HUNK_HDR_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_gold_patch(patch_text: str) -> dict[str, set[int]]:
    """Parse a unified diff into {path: set(line_numbers)} for added/modified lines.

    Lines counted are the *new* file line numbers of `+`-prefixed diff rows
    (additions and modifications). Removed lines do not advance the new-file
    cursor so subsequent `+` rows get correct line numbers.
    """
    gold: dict[str, set[int]] = {}
    current_file: Optional[str] = None
    new_line = 0
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            gold.setdefault(current_file, set())
        elif line.startswith("--- a/"):
            continue
        elif line.startswith("@@"):
            m = _HUNK_HDR_RE.match(line)
            if m:
                new_line = int(m.group(1))
        elif line.startswith("+++") or line.startswith("---"):
            # header lines we don't care about
            continue
        elif line.startswith("+"):
            if current_file:
                gold[current_file].add(new_line)
            new_line += 1
        elif line.startswith("-"):
            # removed line: doesn't advance new-file cursor
            continue
        elif line.startswith("\\"):
            # "\ No newline at end of file"
            continue
        else:
            # context line
            new_line += 1
    return gold


def _gold_files(gold: dict[str, set[int]]) -> set[str]:
    return {f for f, ls in gold.items() if ls}


def _gold_lines(gold: dict[str, set[int]]) -> set[tuple[str, int]]:
    return {(f, l) for f, ls in gold.items() for l in ls}


# ===== Candidate extractors =====

def extract_broad(broad_candidates_path: Path) -> list[dict]:
    """Broad candidates from broad_candidates.jsonl."""
    if not broad_candidates_path.is_file():
        return []
    out: list[dict] = []
    for line in broad_candidates_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        out.append({
            "path": obj.get("path", ""),
            "start_line": int(obj.get("start_line") or 0),
            "end_line": int(obj.get("end_line") or 0),
            "source": obj.get("source", ""),
            "id": obj.get("id", ""),
        })
    return out


def extract_condiag(selected_evidence_path: Path) -> list[dict]:
    """ConDiag selected evidence from selected_evidence.json."""
    if not selected_evidence_path.is_file():
        return []
    try:
        data = json.loads(selected_evidence_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[dict] = []
    for ev in data.get("evidence") or []:
        out.append({
            "path": ev.get("path", ""),
            "start_line": int(ev.get("start_line") or 0),
            "end_line": int(ev.get("end_line") or 0),
            "operation": ev.get("operation", ""),
            "id": ev.get("id", ""),
        })
    return out


_EDITED_FILES_RE = re.compile(r"edited files:\s*\d+\s*\(([^)]+)\)")


def extract_feedback(packet_path: Path) -> list[dict]:
    """Feedback packet only knows edited files; no line ranges.

    Parses the "edited files: N (file1, file2, ...)" line from
    Previous Patch Summary. Whole-file candidates.
    """
    if not packet_path.is_file():
        return []
    text = packet_path.read_text(encoding="utf-8")
    m = _EDITED_FILES_RE.search(text)
    if not m:
        return []
    files_str = m.group(1)
    files = [f.strip() for f in files_str.split(",") if f.strip()]
    return [
        {"path": f, "start_line": 0, "end_line": 0,
         "source": "edited_files_line", "id": f"F{i}"}
        for i, f in enumerate(files)
    ]


# ===== Metric computation =====

def _cand_files(cands: list[dict]) -> set[str]:
    return {c["path"] for c in cands if c["path"]}


def _expand_cand_lines(cands: list[dict]) -> set[tuple[str, int]]:
    """Expand candidates with start/end ranges into (file, line) pairs."""
    out: set[tuple[str, int]] = set()
    for c in cands:
        s, e = c["start_line"], c["end_line"]
        if s > 0 and e >= s:
            for l in range(s, e + 1):
                out.add((c["path"], l))
    return out


def _f1(p: float, r: float) -> float:
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def compute_overlap(cands: list[dict], gold: dict[str, set[int]]) -> dict:
    g_files = _gold_files(gold)
    g_lines = _gold_lines(gold)
    c_files = _cand_files(cands)
    c_lines = _expand_cand_lines(cands)

    file_inter = c_files & g_files
    line_inter = c_lines & g_lines

    file_p = len(file_inter) / len(c_files) if c_files else 0.0
    file_r = len(file_inter) / len(g_files) if g_files else 0.0
    line_p = len(line_inter) / len(c_lines) if c_lines else 0.0
    line_r = len(line_inter) / len(g_lines) if g_lines else 0.0

    return {
        "gold_files": len(g_files),
        "gold_lines": len(g_lines),
        "cand_files": len(c_files),
        "cand_lines": len(c_lines),
        "file_inter": len(file_inter),
        "line_inter": len(line_inter),
        "file_precision": round(file_p, 4),
        "file_recall": round(file_r, 4),
        "file_f1": round(_f1(file_p, file_r), 4),
        "line_precision": round(line_p, 4),
        "line_recall": round(line_r, 4),
        "line_f1": round(_f1(line_p, line_r), 4),
    }


def _packet_chars(run_dir: Path, baseline: str) -> int:
    pkt = run_dir / "intervention" / "context_packet.md"
    return pkt.stat().st_size if pkt.is_file() else 0


# ===== Run directory → row =====

def run_for_dir(run_dir: Path, baseline: str, instance_id: str) -> dict:
    gold_patch = get_gold_patch(instance_id)
    gold = parse_gold_patch(gold_patch)

    iv = run_dir / "intervention"
    if baseline == "feedback_retry":
        cands = extract_feedback(iv / "context_packet.md")
    elif baseline == "broad_expansion":
        cands = extract_broad(iv / "broad_candidates.jsonl")
    elif baseline == "condiag_packet_only":
        cands = extract_condiag(iv / "selected_evidence.json")
    else:
        return {}

    m = compute_overlap(cands, gold)
    m["instance_id"] = instance_id
    m["baseline"] = baseline
    m["cand_count"] = len(cands)
    m["packet_chars"] = _packet_chars(run_dir, baseline)
    if m["packet_chars"] > 0:
        m["overlap_per_1k_chars"] = round(
            m["line_inter"] / (m["packet_chars"] / 1000.0), 4
        )
    else:
        m["overlap_per_1k_chars"] = 0.0
    return m


# ===== Matrix build =====

FIELDNAMES = [
    "instance_id", "baseline",
    "gold_files", "gold_lines",
    "cand_count", "cand_files", "cand_lines",
    "file_inter", "line_inter",
    "file_precision", "file_recall", "file_f1",
    "line_precision", "line_recall", "line_f1",
    "packet_chars", "overlap_per_1k_chars",
]


def build_matrix(runs_root: Path, out_dir: Path,
                 baselines: Optional[list[str]] = None,
                 agent: str = "miniswe") -> int:
    baselines = baselines or BASELINES
    rows: list[dict] = []
    for bl in baselines:
        bdir = runs_root / agent / bl
        if not bdir.is_dir():
            continue
        for inst_dir in sorted(bdir.iterdir()):
            if not inst_dir.is_dir():
                continue
            r = run_for_dir(inst_dir, bl, inst_dir.name)
            if r:
                rows.append(r)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "packet_gold_overlap_matrix.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})
    print(f"[matrix] {len(rows)} rows -> {csv_path}")

    _write_summary(out_dir, rows, baselines)
    _write_by_instance(out_dir, rows, baselines)
    return len(rows)


# ===== Output: summary MD =====

def _avg(rows: list[dict], key: str) -> float:
    return sum(float(r.get(key) or 0) for r in rows) / len(rows) if rows else 0.0


def _write_summary(out_dir: Path, rows: list[dict], baselines: list[str]) -> None:
    by_bl: dict[str, list[dict]] = {bl: [] for bl in baselines}
    for r in rows:
        by_bl.setdefault(r["baseline"], []).append(r)

    # fire-only = cases where the baseline produced at least one candidate
    fire_only = {bl: [r for r in rs if r.get("cand_count", 0) > 0]
                 for bl, rs in by_bl.items()}

    out: list[str] = []
    out.append("# D4-9 Step 4 — Packet-vs-Gold Overlap Summary")
    out.append("")
    out.append("Gold = lines added/modified in SWE-bench Verified gold patch.")
    out.append("Candidates per baseline:")
    out.append("- **feedback_retry**: parsed from `edited files: N (...)` in packet")
    out.append("- **broad_expansion**: `broad_candidates.jsonl` entries (file + line range)")
    out.append("- **condiag_packet_only**: `selected_evidence.json` items (file + line range)")
    out.append("")
    out.append("## Per-baseline aggregates (Line-level, all 17 instances)")
    out.append("")
    out.append("| baseline | n | avg_cand | avg_pkt_chars | avg_line_inter | "
               "avg_line_P | avg_line_R | avg_line_F1 | avg_overlap/1k |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")

    for bl in baselines:
        rs = by_bl.get(bl, [])
        if not rs:
            continue
        out.append(
            f"| {bl} | {len(rs)} | {_avg(rs, 'cand_count'):.1f} | "
            f"{_avg(rs, 'packet_chars'):.0f} | {_avg(rs, 'line_inter'):.1f} | "
            f"{_avg(rs, 'line_precision'):.3f} | {_avg(rs, 'line_recall'):.3f} | "
            f"{_avg(rs, 'line_f1'):.3f} | {_avg(rs, 'overlap_per_1k_chars'):.2f} |"
        )

    out.append("")
    out.append("## Per-baseline aggregates (Line-level, FIRE-ONLY)")
    out.append("")
    out.append("Excludes cases where the baseline produced no candidates "
               "(e.g. NO_TRIGGER abstain / skipped_no_retry).")
    out.append("")
    out.append("| baseline | n_fire | avg_cand | avg_pkt_chars | avg_line_inter | "
               "avg_line_P | avg_line_R | avg_line_F1 | avg_overlap/1k |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for bl in baselines:
        rs = fire_only.get(bl, [])
        if not rs:
            continue
        out.append(
            f"| {bl} | {len(rs)} | {_avg(rs, 'cand_count'):.1f} | "
            f"{_avg(rs, 'packet_chars'):.0f} | {_avg(rs, 'line_inter'):.1f} | "
            f"{_avg(rs, 'line_precision'):.3f} | {_avg(rs, 'line_recall'):.3f} | "
            f"{_avg(rs, 'line_f1'):.3f} | {_avg(rs, 'overlap_per_1k_chars'):.2f} |"
        )

    out.append("")
    out.append("## Per-baseline aggregates (File-level, all 17)")
    out.append("")
    out.append("| baseline | n | avg_file_inter | avg_file_P | avg_file_R | avg_file_F1 |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for bl in baselines:
        rs = by_bl.get(bl, [])
        if not rs:
            continue
        out.append(
            f"| {bl} | {len(rs)} | {_avg(rs, 'file_inter'):.2f} | "
            f"{_avg(rs, 'file_precision'):.3f} | {_avg(rs, 'file_recall'):.3f} | "
            f"{_avg(rs, 'file_f1'):.3f} |"
        )

    out.append("")
    out.append("## Win rate (head-to-head, line_F1, all 17)")
    out.append("")
    inst_ids = sorted({r["instance_id"] for r in rows})
    wins = {bl: 0 for bl in baselines}
    ties = 0
    for iid in inst_ids:
        per_bl = {r["baseline"]: r["line_f1"] for r in rows if r["instance_id"] == iid}
        if not per_bl:
            continue
        best = max(per_bl.values())
        winners = [bl for bl, v in per_bl.items() if v == best]
        if len(winners) > 1:
            ties += 1
        else:
            wins[winners[0]] += 1
    out.append("| baseline | wins |")
    out.append("|---|---:|")
    for bl in baselines:
        out.append(f"| {bl} | {wins[bl]} |")
    out.append(f"| (tie) | {ties} |")

    out.append("")
    out.append("## Structural caveat — read before drawing conclusions")
    out.append("")
    out.append("The line/file overlap metric has a structural bias:")
    out.append("")
    out.append("- **Broad Expansion** uses `EDITED_FILE_WINDOW` (±40 around attempt_1's")
    out.append("  edited lines) — by construction it overlaps the files agent already touched,")
    out.append("  which often trivially overlap gold because agent's edits are usually gold-adjacent.")
    out.append("- **Feedback Retry** lists `edited files` from attempt_1 — same structural advantage.")
    out.append("- **ConDiag packet_only** uses `REHYDRATE_SEEN_EVIDENCE` which **excludes files")
    out.append("  already in PATCH_CONTEXT** — its job is to surface what agent dropped, not echo")
    out.append("  what's already there. So when agent edited the right file (the common case),")
    out.append("  ConDiag correctly does NOT re-suggest it, giving lower gold-overlap by design.")
    out.append("")
    out.append("ConDiag wins this metric only when the agent *dropped a gold-relevant file*")
    out.append("(sympy-19954: agent viewed `perm_groups.py` 2170-2220, dropped it, ConDiag REHYDRATE")
    out.append("pulled it back, gold = lines 2197/2201/2207-2209 → F1=0.159).")
    out.append("")
    out.append("**The fairer question is incremental recall**: of gold lines NOT in attempt_1's")
    out.append("patch, how many did each baseline surface? That is a follow-up analysis")
    out.append("(packet_gold_overlap_incremental.py, not yet built).")

    out.append("")
    summary_path = out_dir / "packet_gold_overlap_summary.md"
    summary_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[summary] -> {summary_path}")


# ===== Output: by-instance MD =====

def _write_by_instance(out_dir: Path, rows: list[dict], baselines: list[str]) -> None:
    by_inst: dict[str, dict[str, dict]] = {}
    for r in rows:
        by_inst.setdefault(r["instance_id"], {})[r["baseline"]] = r

    out: list[str] = []
    out.append("# D4-9 Step 4 — Packet-vs-Gold Overlap by Instance")
    out.append("")
    out.append("Per-instance side-by-side. Gold = added/modified lines in gold patch.")
    out.append("")
    out.append("| instance | baseline | cand | pkt_chars | line_P | line_R | line_F1 | line_inter | file_F1 | ov/1k |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for iid in sorted(by_inst.keys()):
        per = by_inst[iid]
        for bl in baselines:
            r = per.get(bl)
            if not r:
                continue
            out.append(
                f"| {iid} | {bl} | {r['cand_count']} | {r['packet_chars']} | "
                f"{r['line_precision']:.3f} | {r['line_recall']:.3f} | "
                f"{r['line_f1']:.3f} | {r['line_inter']} | {r['file_f1']:.3f} | "
                f"{r['overlap_per_1k_chars']:.2f} |"
            )
        out.append(f"| | | | | | | | | | |")

    out.append("")
    by_inst_path = out_dir / "packet_gold_overlap_by_instance.md"
    by_inst_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[by-inst] -> {by_inst_path}")


# ===== CLI =====

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--runs-root", default="/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs",
        help="root containing <agent>/<baseline>/<instance>/ tree",
    )
    p.add_argument(
        "--out-dir", default="/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4",
        help="output directory for matrix CSV + summary MDs",
    )
    p.add_argument(
        "--agent", default="miniswe",
        help="agent subdir under runs-root",
    )
    p.add_argument(
        "--baselines", default=",".join(BASELINES),
        help="comma-separated baseline list (default: feedback,broad,condiag)",
    )
    args = p.parse_args(argv)

    baselines = [b.strip() for b in args.baselines.split(",") if b.strip()]
    n = build_matrix(Path(args.runs_root), Path(args.out_dir),
                     baselines=baselines, agent=args.agent)
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
