"""Build manifest CSV from existing mini-SWE batch run (D4-4).

A 'manifest' is the bridge between an existing batch run directory
(produced by mini-SWE) and the baseline_runner. Given a batch root
like:

    /mnt/d/condiag-artifacts/runs/pilot50_batch2_20260628_114704/miniswe/Verified/

it produces a CSV with columns:

    instance_id, traj_path, run_dir, source_batch, agent, model, exit_status,
    api_calls, wall_time_seconds,
    repo_base_path, base_commit, repo_ready   (added in D4-8.5)

The baseline_runner uses --manifest to resolve instance_id -> traj_path
without re-running mini-SWE (the from_existing_traj mode).

D4-8.5 repo plumbing: `repo_base_path` is probed from <workspaces>/<instance>/repo_base/.
`base_commit` is looked up from the swe-bench_verified arrow file when available.
`repo_ready` = "yes" when repo_base_path/.git exists, else "no".

CSV (not JSON) on purpose: easy to inspect/diff/extend in a spreadsheet.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional


FIELDNAMES = [
    "instance_id",
    "traj_path",
    "run_dir",
    "source_batch",
    "agent",
    "model",
    "exit_status",
    "api_calls",
    "wall_time_seconds",
    "repo_base_path",
    "base_commit",
    "repo_ready",
]


# Default workspaces root (where pre-prepared repo_base dirs live)
DEFAULT_WORKSPACES_ROOT = Path("/home/swelite/condiag/workspaces")

# Cached swe-bench_verified lookup (instance_id -> {repo, base_commit, version})
_SWE_BENCH_VERIFIED_CACHE: Optional[dict] = None


def _load_swe_bench_verified() -> dict:
    """Load swe-bench_verified instance metadata (repo / base_commit / problem_statement / patch) once.

    Returns dict {instance_id: {"repo": str, "base_commit": str, "version": str,
                                 "problem_statement": str, "patch": str}}.
    Returns empty dict if the arrow file is unavailable (e.g. on a fresh box).
    """
    global _SWE_BENCH_VERIFIED_CACHE
    if _SWE_BENCH_VERIFIED_CACHE is not None:
        return _SWE_BENCH_VERIFIED_CACHE

    cache: dict[str, dict] = {}
    candidates = [
        Path("/mnt/d/condiag-artifacts/cache/hf/datasets/princeton-nlp___swe-bench_verified/default/0.0.0/c104f840cc67f8b6eec6f759ebc8b2693d585d4a/swe-bench_verified-test.arrow"),
    ]
    arrow_path = next((p for p in candidates if p.is_file()), None)
    if arrow_path is None:
        _SWE_BENCH_VERIFIED_CACHE = cache
        return cache

    try:
        import pyarrow as pa
        import pyarrow.ipc as ipc
        with open(arrow_path, "rb") as f:
            reader = ipc.open_stream(f)
            table = reader.read_all()
        df = table.to_pandas()
        for _, r in df.iterrows():
            cache[r["instance_id"]] = {
                "repo": r.get("repo", ""),
                "base_commit": r.get("base_commit", ""),
                "version": r.get("version", ""),
                "problem_statement": str(r.get("problem_statement", "") or ""),
                "patch": str(r.get("patch", "") or ""),
            }
    except Exception:
        pass

    _SWE_BENCH_VERIFIED_CACHE = cache
    return cache


def get_problem_statement(instance_id: str) -> str:
    """Look up the SWE-bench Verified problem_statement for an instance.

    Returns "" if instance_id is unknown or the arrow file is unavailable.
    Used by broad_expansion to source RG_ISSUE_KEYWORD_SEARCH queries.
    """
    return (_load_swe_bench_verified().get(instance_id) or {}).get("problem_statement", "")


def get_gold_patch(instance_id: str) -> str:
    """Look up the SWE-bench Verified gold patch for an instance.

    Returns "" if instance_id is unknown or the arrow file is unavailable.
    Used by packet_gold_overlap to extract gold files + gold lines.
    """
    return (_load_swe_bench_verified().get(instance_id) or {}).get("patch", "")


def _probe_repo(instance_id: str, workspaces_root: Path) -> dict:
    """Probe whether <workspaces_root>/<instance>/repo_base/ exists.

    Returns {"repo_base_path": str, "base_commit": str, "repo_ready": "yes"|"no"}.
    base_commit is filled from swe-bench_verified metadata when available
    (separate from repo_ready — base_commit may be known even if repo not yet
    checked out).
    """
    repo_base = workspaces_root / instance_id / "repo_base"
    repo_base_path = str(repo_base) if repo_base.is_dir() else ""
    # worktree: .git is a *file*; clone: .git is a directory. Accept both.
    git_marker = repo_base / ".git"
    repo_ready = "yes" if (git_marker.is_dir() or git_marker.is_file()) else "no"

    meta = _load_swe_bench_verified().get(instance_id) or {}
    return {
        "repo_base_path": repo_base_path,
        "base_commit": meta.get("base_commit", ""),
        "repo_ready": repo_ready,
    }


def _probe_traj(traj_path: Path) -> dict:
    """Read minimal fields from traj.json without crashing on malformed files."""
    out = {
        "model": "",
        "exit_status": "",
        "api_calls": "",
        "wall_time_seconds": "",
    }
    try:
        data = json.loads(traj_path.read_text(encoding="utf-8"))
    except Exception as e:
        out["model"] = f"<unreadable:{type(e).__name__}>"
        return out

    info = data.get("info") or {}
    ms = info.get("model_stats") or {}
    cfg = info.get("config") or {}
    model_cfg = cfg.get("model") or {}
    out["model"] = model_cfg.get("model_name") or ""
    out["exit_status"] = info.get("exit_status") or ""
    out["api_calls"] = int(ms.get("api_calls") or 0)

    msgs = data.get("messages") or []
    ts = [m.get("timestamp") for m in msgs if isinstance(m.get("timestamp"), (int, float))]
    if len(ts) >= 2:
        out["wall_time_seconds"] = round(float(ts[-1] - ts[0]), 1)
    return out


def build_manifest(
    batch_root: Path,
    output_csv: Path,
    agent: str = "miniswe",
    source_batch: Optional[str] = None,
    workspaces_root: Optional[Path] = None,
) -> dict:
    """Scan batch_root for *.traj.json files and emit a manifest CSV.

    Expected layout:
        <batch_root>/<instance_id>/<instance_id>.traj.json
        OR
        <batch_root>/<instance_id>.traj.json

    D4-8.5: also probes <workspaces_root>/<instance>/repo_base/ for each
    instance and fills repo_base_path / base_commit / repo_ready columns.

    Returns a summary dict (rows_written, batch_root, output_csv, ...).
    """
    batch_root = Path(batch_root)
    output_csv = Path(output_csv)
    if not batch_root.is_dir():
        raise FileNotFoundError(f"batch_root not a dir: {batch_root}")

    if source_batch is None:
        source_batch = batch_root.name

    if workspaces_root is None:
        workspaces_root = DEFAULT_WORKSPACES_ROOT

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    # Discover traj files. Support both per-instance dir and flat layouts.
    traj_files: list[Path] = []
    for sub in sorted(batch_root.iterdir()):
        if sub.is_dir():
            traj_files.extend(sorted(sub.glob("*.traj.json")))
        elif sub.is_file() and sub.name.endswith(".traj.json"):
            traj_files.append(sub)

    for traj in traj_files:
        instance_id = traj.name.removesuffix(".traj.json")
        probe = _probe_traj(traj)
        repo_probe = _probe_repo(instance_id, workspaces_root)
        row = {
            "instance_id": instance_id,
            "traj_path": str(traj),
            "run_dir": str(traj.parent),
            "source_batch": source_batch,
            "agent": agent,
            **probe,
            **repo_probe,
        }
        rows.append(row)

    with output_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    return {
        "rows_written": len(rows),
        "batch_root": str(batch_root),
        "output_csv": str(output_csv),
        "source_batch": source_batch,
        "agent": agent,
    }


def load_manifest(csv_path: Path) -> dict[str, dict]:
    """Read manifest CSV -> {instance_id: row_dict}."""
    csv_path = Path(csv_path)
    out: dict[str, dict] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            iid = row["instance_id"]
            out[iid] = row
    return out
