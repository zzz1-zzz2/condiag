"""Extract cost.json fields from a mini-SWE traj.json (D4-4).

mini-SWE trajectories store:
    info.model_stats.api_calls      (always present)
    info.model_stats.instance_cost  (0.0 when cost_tracking='ignore_errors')
    info.config.model.model_name    (e.g. 'deepseek/deepseek-v4-pro')
    info.exit_status                (e.g. 'Submitted')
    messages[].timestamp            (epoch float)

Token usage is NOT stored in the traj (DeepSeek V4 cost_tracking='ignore_errors'
drops per-call usage). v0 cost schema therefore allows token fields to be null.

Wall-clock is derived from first/last message timestamps.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def extract_cost_from_traj(
    traj_path: Path,
    instance_id: str,
    agent: str = "miniswe",
) -> dict:
    """Build a cost.json dict from a mini-SWE traj.json file.

    Returns schema:
        {
            "schema_version": "condiag.cost.v0",
            "agent": "miniswe",
            "instance_id": "...",
            "model": "...",
            "provider": "...",
            "attempts": [
                {"phase": "attempt_1", "api_calls": N, "prompt_tokens": null,
                 "completion_tokens": null, "total_tokens": null,
                 "wall_time_seconds": float}
            ],
            "total": {...same shape...},
            "estimated_usd": null,
            "pricing_source": "unavailable",
            "note": "..."
        }
    """
    traj_path = Path(traj_path)
    with traj_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    info = data.get("info") or {}
    model_stats = info.get("model_stats") or {}
    config = info.get("config") or {}
    model_cfg = config.get("model") or {}
    model_name = model_cfg.get("model_name") or "unknown"

    # provider derived from model_name prefix (e.g. "deepseek/deepseek-v4-pro" -> "deepseek")
    provider = "unknown"
    if "/" in model_name:
        provider = model_name.split("/", 1)[0]

    api_calls = int(model_stats.get("api_calls") or 0)
    instance_cost = float(model_stats.get("instance_cost") or 0.0)
    wall = _compute_wall_time_seconds(data)

    attempt_cost = {
        "phase": "attempt_1",
        "api_calls": api_calls,
        "prompt_tokens": None,        # not stored in traj
        "completion_tokens": None,    # not stored in traj
        "total_tokens": None,         # not stored in traj
        "wall_time_seconds": wall,
    }

    return {
        "schema_version": "condiag.cost.v0",
        "agent": agent,
        "instance_id": instance_id,
        "model": model_name,
        "provider": provider,
        "attempts": [attempt_cost],
        "total": {
            "api_calls": api_calls,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "wall_time_seconds": wall,
        },
        "estimated_usd": None if instance_cost == 0.0 else instance_cost,
        "pricing_source": (
            "mini-SWE model_stats.instance_cost"
            if instance_cost != 0.0
            else "unavailable (cost_tracking=ignore_errors; tokens not stored in traj)"
        ),
        "note": (
            "v0: mini-SWE traj does not store per-call token usage; "
            "api_calls and wall_time_seconds are populated; token fields are null. "
            "See artifact_schema.md §5.1 and design拍板 4.3."
        ),
    }


def _compute_wall_time_seconds(traj_data: dict) -> Optional[float]:
    """Derive wall-clock from first/last message timestamps."""
    msgs = traj_data.get("messages") or []
    timestamps = [m.get("timestamp") for m in msgs if isinstance(m.get("timestamp"), (int, float))]
    if len(timestamps) < 2:
        return None
    return float(timestamps[-1] - timestamps[0])
