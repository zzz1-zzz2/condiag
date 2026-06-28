"""Build a ConDiag case_bundle from a trajectory file.

This is the standard entry point that turns an agent trajectory (e.g.
mini-SWE `.traj.json`) into the on-disk layout consumed by ConDiag runtime
modules (trigger / scope_guard / retrieval_executor / etc.).

Outputs (written under <out>/<instance_id>/):
    raw_trajectory.json       — verbatim copy of the input traj
    runtime_signals.json      — runtime-visible facts only (v0.1 schema)
    patch.diff                — final submission patch (info.submission)
    local_test_outputs.md     — human-readable extraction of test runs
    final_patch_context.json  — last <PATCH_CONTEXT> declaration
    build_report.json         — provenance + parse-quality summary

What this script deliberately does NOT do:
- Classify pathology / 5R action (job of trigger.py / diagnosis_normalizer.py)
- Compute or copy gold / official-eval metrics (those live in
  contextbench_metrics.json / official_eval.json, produced by ContextBench
  evaluation tooling — separate step)

Usage:
    python -m condiag.tools.build_case_bundle \\
        --traj /path/to/<instance>.traj.json \\
        --instance <instance_id> \\
        --out /mnt/d/condiag-artifacts/condiag/v0/case_bundles/<instance_id>
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .parsers.base import ParsedTrajectory
from .parsers.miniswe import MiniSWETrajectoryParser


def build_case_bundle(
    traj_path: Path,
    instance_id: str,
    out_dir: Path,
    parser_name: str = "miniswe",
) -> dict:
    """Build a case_bundle from a trajectory file.

    Returns the build_report dict (also written to build_report.json).
    """
    traj_path = Path(traj_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    parser = _get_parser(parser_name)
    pt: ParsedTrajectory = parser.parse(traj_path)
    # Trust the CLI-provided instance_id override if given
    if instance_id:
        pt.instance_id = instance_id

    # --- raw_trajectory.json (verbatim copy)
    raw_traj_dst = out_dir / "raw_trajectory.json"
    shutil.copyfile(traj_path, raw_traj_dst)

    # --- runtime_signals.json
    runtime_signals = pt.to_dict()
    (out_dir / "runtime_signals.json").write_text(
        json.dumps(runtime_signals, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # --- patch.diff (extract from info.submission; for mini-SWE that's the unified diff)
    # mini-SWE puts the final patch in info.submission
    patch_diff = _read_submission(traj_path)
    (out_dir / "patch.diff").write_text(patch_diff, encoding="utf-8")

    # --- local_test_outputs.md
    md = _render_local_test_outputs_md(pt)
    (out_dir / "local_test_outputs.md").write_text(md, encoding="utf-8")

    # --- final_patch_context.json
    fpc = {
        "schema_version": "condiag.final_patch_context.v0",
        "instance_id": pt.instance_id,
        "files": pt.final_patch_context_files,
        "files_count": pt.final_patch_context_files_count,
    }
    (out_dir / "final_patch_context.json").write_text(
        json.dumps(fpc, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # --- build_report.json
    report = {
        "schema_version": "condiag.case_bundle_build_report.v0",
        "ok": True,
        "instance_id": pt.instance_id,
        "parser": parser.name,
        "traj_path": str(traj_path),
        "out_dir": str(out_dir),
        "counts": {
            "n_messages": pt.n_messages,
            "n_assistant_messages": pt.n_assistant_messages,
            "n_user_messages": pt.n_user_messages,
            "viewed_files_count": pt.viewed_files_count,
            "search_commands_count": pt.search_commands_count,
            "test_runs_count": pt.test_runs_count,
            "test_failures_count": pt.test_failures_count,
            "edited_files_count": pt.edited_files_count,
            "edited_hunks_total": pt.edited_hunks_total,
            "patch_context_files_count": pt.patch_context_files_count,
            "final_patch_context_files_count": pt.final_patch_context_files_count,
        },
        "quality": pt.quality,
        "warnings": list(pt.quality.get("parse_warnings") or []),
    }
    (out_dir / "build_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_PARSERS = {
    "miniswe": MiniSWETrajectoryParser,
}


def _get_parser(name: str):
    if name not in _PARSERS:
        raise ValueError(
            f"unknown parser '{name}'. Available: {sorted(_PARSERS.keys())}"
        )
    return _PARSERS[name]()


def _read_submission(traj_path: Path) -> str:
    try:
        with Path(traj_path).open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""
    return (data.get("info") or {}).get("submission") or ""


def _render_local_test_outputs_md(pt: ParsedTrajectory) -> str:
    """Compose the markdown we already use for human inspection / miner input."""
    lines: list[str] = []
    lines.append(f"# Local Test Outputs — {pt.instance_id}")
    lines.append("")
    lines.append(
        "Extracted from raw_trajectory.json. Each entry is a (test command, tool output excerpt) pair."
    )
    lines.append("")
    if not pt.test_runs:
        lines.append("_No test runs observed in this trajectory._")
        return "\n".join(lines) + "\n"

    # Pair test_runs with their output_samples by msg_index
    sample_by_cmd_idx = {s["test_command_index"]: s for s in pt.test_output_samples}
    for i, run in enumerate(pt.test_runs, 1):
        cmd_idx = run["msg_index"]
        sample = sample_by_cmd_idx.get(cmd_idx)
        lines.append(f"## Test run #{i}  (msg[{cmd_idx}])")
        lines.append("")
        lines.append("**command**:")
        lines.append("```")
        lines.append(run.get("command") or "")
        lines.append("```")
        lines.append("")
        out_idx = run.get("output_msg_index")
        if sample:
            lines.append(f"**output (msg[{out_idx}], first 1500 chars)**:")
            lines.append("```")
            lines.append(sample.get("output_excerpt") or "")
            lines.append("```")
            lines.append("")
        elif out_idx is None:
            lines.append(f"_No matching tool output captured (output_msg_index=None)._")
            lines.append("")

    if pt.test_failures:
        lines.append("## Observed test failures")
        lines.append("")
        for f in pt.test_failures:
            lines.append(f"- `{f}`")
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a ConDiag case_bundle from a trajectory file")
    ap.add_argument("--traj", required=True, help="Path to .traj.json (or compatible)")
    ap.add_argument("--instance", required=True, help="instance_id (e.g. django__django-13195)")
    ap.add_argument(
        "--out",
        required=True,
        help="Output directory; files are written under <out>/<instance_id>/",
    )
    ap.add_argument(
        "--parser",
        default="miniswe",
        choices=sorted(_PARSERS.keys()),
        help="Trajectory format parser to use (default: miniswe)",
    )
    args = ap.parse_args()

    out_root = Path(args.out)
    out_dir = out_root / args.instance if out_root.name != args.instance else out_root
    report = build_case_bundle(Path(args.traj), args.instance, out_dir, parser_name=args.parser)
    print(f"[build_case_bundle] ok  instance={report['instance_id']}  parser={report['parser']}")
    print(f"  -> {out_dir}")
    print(f"  counts: {report['counts']}")
    if report["warnings"]:
        print(f"  warnings: {report['warnings']}")


if __name__ == "__main__":
    main()
