"""Mini-SWE-Agent adapter — Agent Adapter (input side: attempt_1 -> case_bundle).

Wraps condiag.tools.build_case_bundle so the existing case_bundle pipeline
becomes an AgentAdapter implementation without refactor.

Mini-SWE raw run layout (per traj.json):
    - info.submission: final unified diff (-> patch.diff)
    - messages: assistant/user/tool-call stream (-> runtime_signals.json)
    - <EXPLORE_CONTEXT> / <PATCH_CONTEXT>: structured context markers
    - test command outputs (-> local_test_outputs.md)

Architecture boundary (v0.2, 2026-06-29):
    This class ONLY handles attempt_1 -> runtime_signals (Agent Adapter role).
    For retry injection (ContextPacket -> attempt_2 input), see:
        condiag.adapters.miniswe_retry_injection.MinisweRetryInjectionAdapter
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

    # =========================================================================
    # Agent Adapter: attempt_1 raw logs -> unified case_bundle
    # =========================================================================

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

    # =========================================================================
    # Deprecated: retry injection methods moved to MinisweRetryInjectionAdapter
    # =========================================================================

    def build_retry_input(self, request):
        """DEPRECATED: use MinisweRetryInjectionAdapter.build_retry_input()."""
        from .miniswe_retry_injection import MinisweRetryInjectionAdapter
        return MinisweRetryInjectionAdapter().build_retry_input(request)

    def build_retry_command(self, retry_input):
        """DEPRECATED: use MinisweRetryInjectionAdapter.build_retry_command()."""
        from .miniswe_retry_injection import MinisweRetryInjectionAdapter
        return MinisweRetryInjectionAdapter().build_retry_command(retry_input)

    def collect_retry_artifacts(self, run_dir, repo_dir):
        """DEPRECATED: use MinisweRetryInjectionAdapter.collect_attempt2_artifacts()."""
        from .miniswe_retry_injection import MinisweRetryInjectionAdapter
        return MinisweRetryInjectionAdapter().collect_attempt2_artifacts(run_dir, repo_dir)

    def has_valid_tool_loop(self, trajectory_path):
        """DEPRECATED: use MinisweRetryInjectionAdapter.validate_host_agent_run()."""
        from .miniswe_retry_injection import MinisweRetryInjectionAdapter
        return MinisweRetryInjectionAdapter().validate_host_agent_run(trajectory_path)


# =========================================================================
# Helpers
# =========================================================================

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
