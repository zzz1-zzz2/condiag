"""mini-SWE-Agent adapter (current v0 first-class adapter).

Wraps condiag.tools.build_case_bundle so the existing case_bundle pipeline
becomes an AgentAdapter implementation without refactor.

Mini-SWE raw run layout (per traj.json):
    - info.submission: final unified diff (-> patch.diff)
    - messages: assistant/user/tool-call stream (-> runtime_signals.json)
    - <EXPLORE_CONTEXT> / <PATCH_CONTEXT>: structured context markers
    - test command outputs (-> local_test_outputs.md)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .base import AgentAdapter, register_adapter


@register_adapter
class MinisweAdapter(AgentAdapter):
    name = "miniswe"
    display_name = "mini-SWE-Agent"
    status = "implemented"

    def build_case_bundle(
        self,
        raw_run_dir: Path,
        instance_id: str,
        out_dir: Path,
    ) -> dict:
        """Delegate to tools.build_case_bundle.

        `raw_run_dir` for mini-SWE is expected to contain the traj.json file.
        We accept either:
          - raw_run_dir/traj.json
          - raw_run_dir/<instance_id>.traj.json
          - raw_run_dir as the traj.json file itself
        """
        from ..tools.build_case_bundle import build_case_bundle

        traj_path = _resolve_traj_path(raw_run_dir, instance_id)
        return build_case_bundle(
            traj_path=traj_path,
            instance_id=instance_id,
            out_dir=out_dir,
            parser_name="miniswe",
        )

    def extract_runtime_signals(self, raw_run_dir: Path, instance_id: Optional[str] = None) -> dict:
        """Parse traj.json and return runtime_signals dict without writing files.

        Useful for trigger / classifier pipelines that don't need a full
        case_bundle on disk.
        """
        from ..tools.build_case_bundle import _get_parser

        traj_path = _resolve_traj_path(raw_run_dir, instance_id)
        parser = _get_parser("miniswe")
        pt = parser.parse(traj_path)
        if instance_id:
            pt.instance_id = instance_id
        return pt.to_dict()

    def extract_patch(self, raw_run_dir: Path, instance_id: Optional[str] = None) -> str:
        """Read info.submission directly from traj.json."""
        traj_path = _resolve_traj_path(raw_run_dir, instance_id)
        with traj_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("info") or {}).get("submission") or ""

    def extract_final_patch_context(self, raw_run_dir: Path, instance_id: Optional[str] = None) -> dict:
        """Parse <PATCH_CONTEXT> from traj.json."""
        rs = self.extract_runtime_signals(raw_run_dir, instance_id)
        return {
            "schema_version": "condiag.final_patch_context.v0",
            "instance_id": rs.get("instance_id"),
            "files": rs.get("final_patch_context_files") or [],
            "files_count": rs.get("final_patch_context_files_count") or 0,
        }

    def build_retry_input(
        self,
        context_packet_path: Path,
        task_metadata: dict,
    ) -> dict:
        """Translate ConDiag ContextPacket into mini-SWE retry user-message.

        Mini-SWE accepts a free-form user message; we wrap the packet as
        a system-augmented retry instruction.
        """
        packet_md = Path(context_packet_path).read_text(encoding="utf-8")
        instance_id = task_metadata.get("instance_id", "<unknown>")
        user_msg = (
            f"## ConDiag Recovery Context for {instance_id}\n\n"
            f"{packet_md}\n\n"
            f"---\n"
            f"Using the context above, revise your previous patch. Focus on the "
            f"evidence flagged by the diagnosis and avoid re-introducing the "
            f"same edits."
        )
        return {
            "agent": "miniswe",
            "retry_input_kind": "user_message",
            "user_message": user_msg,
            "context_packet_path": str(context_packet_path),
        }


# ===== helpers =====

def _resolve_traj_path(raw_run_dir: Path, instance_id: Optional[str] = None) -> Path:
    """Find the traj.json file under raw_run_dir.

    Tries (in order):
      1. raw_run_dir itself (if it's a .json file)
      2. raw_run_dir / <instance_id>.traj.json
      3. raw_run_dir / traj.json
      4. raw_run_dir / *.traj.json (first match)
    """
    p = Path(raw_run_dir)
    if p.is_file() and p.suffix == ".json":
        return p

    candidates = []
    if instance_id:
        candidates.append(p / f"{instance_id}.traj.json")
    candidates.extend([
        p / "traj.json",
    ])
    # glob fallback
    candidates.extend(sorted(p.glob("*.traj.json")))

    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        f"no traj.json found under {p} (instance_id={instance_id!r}); "
        f"tried: {[str(c) for c in candidates]}"
    )
