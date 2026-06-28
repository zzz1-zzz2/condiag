"""mini-SWE-agent trajectory parser.

Reads `.traj.json` written by mini-swe-agent and produces a `ParsedTrajectory`
containing only runtime-visible facts.

Source of facts:
- info.exit_status, info.submission, info.model_stats.api_calls
- messages[*]:
  - assistant messages: ```bash``` blocks (commands) + <EXPLORE_CONTEXT>/<PATCH_CONTEXT> tags
  - user messages: <returncode>N</returncode><output>...</output> from shell

What we DO extract:
- viewed files / spans (from EXPLORE_CONTEXT; fallback to bash views)
- search commands (grep/rg/find)
- test commands and their outputs (pytest/unittest)
- edited files / hunks / line counts (from info.submission unified diff)
- final PATCH_CONTEXT declaration
- shape signals (submitted_without_tests, git checkout count, repeated edits)

What we DO NOT extract (must live in evaluation-only files):
- file_cov, symbol_cov, line_cov, EditLoc recall
- gold patch shape, official FAIL_TO_PASS / PASS_TO_PASS, resolved label
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import common as C
from .base import ParsedTrajectory, TrajectoryParser


_RETURNCODE_BLOCK = re.compile(
    r"<returncode>(-?\d+)</returncode>\s*<output>([\s\S]*?)</output>",
    re.IGNORECASE,
)


class MiniSWETrajectoryParser(TrajectoryParser):
    name = "miniswe"

    def parse(self, traj_path: Path) -> ParsedTrajectory:
        data: dict[str, Any] = _load_json(traj_path)
        pt = ParsedTrajectory()
        pt.agent = "mini-swe-agent"
        pt.instance_id = (
            data.get("instance_id")
            or traj_path.stem.replace(".traj", "")
            or traj_path.parent.name
        )

        info = data.get("info") or {}
        pt.exit_status = info.get("exit_status") or ""
        model_stats = info.get("model_stats") or {}
        pt.api_calls = int(model_stats.get("api_calls") or 0)

        messages = data.get("messages") or []
        pt.n_messages = len(messages)
        pt.n_assistant_messages = sum(1 for m in messages if m.get("role") == "assistant")
        pt.n_user_messages = sum(1 for m in messages if m.get("role") == "user")

        warnings: list[str] = []
        has_explore_context = False
        has_patch_context = False

        # --- Walk the message stream and pull out structured signals
        # We pair each assistant bash command with the following user output
        # so test-failure extraction can be tied to its command.
        viewed_files_in_order: list[str] = []
        viewed_spans: dict[str, list[list[int]]] = {}
        patch_context_blocks: list[str] = []
        search_commands: list[str] = []
        test_commands: list[dict] = []
        test_runs: list[dict] = []
        test_output_samples: list[dict] = []
        test_failures: list[str] = []
        last_user_tail: list[str] = []
        bash_count = 0
        git_checkout_count = 0
        submitted_without_tests = True  # flip if any test command seen

        for i, msg in enumerate(messages):
            role = msg.get("role") or ""
            content = msg.get("content") or ""

            if role == "assistant":
                # Bash blocks
                bash_blocks = C.extract_bash_blocks(content)
                for cmd in bash_blocks:
                    if C.looks_like_complete_task(cmd):
                        continue
                    bash_count += 1
                    if "git checkout" in cmd:
                        git_checkout_count += 1
                    if C.is_search_command(cmd):
                        if cmd not in search_commands:
                            search_commands.append(cmd)
                    if C.is_test_command(cmd):
                        submitted_without_tests = False
                        test_commands.append({"msg_index": i, "command": cmd})

                # EXPLORE_CONTEXT blocks
                explore_blocks = C.extract_tag_blocks(content, "explore_context") + C.extract_tag_blocks(
                    content, "EXPLORE_CONTEXT"
                )
                if explore_blocks:
                    has_explore_context = True
                    for blk in explore_blocks:
                        for path, spans in C.parse_file_lines_pairs(blk).items():
                            rel = C.normalize_repo_path(path) or path
                            if rel not in viewed_files_in_order:
                                viewed_files_in_order.append(rel)
                            viewed_spans.setdefault(rel, []).extend(spans)

                # PATCH_CONTEXT blocks
                pc_blocks = C.extract_tag_blocks(content, "PATCH_CONTEXT")
                if pc_blocks:
                    has_patch_context = True
                    patch_context_blocks.extend(pc_blocks)

            elif role == "user":
                # Look for <returncode>...</returncode><output>...</output>
                m = _RETURNCODE_BLOCK.search(content)
                if m:
                    # Track last user tail (excluding big outputs)
                    last_user_tail.append(content[:400])
                    # Match this output back to the most recent test command at i-1 (or earlier)
                    rc = int(m.group(1))
                    output = m.group(2)
                    # Find the most recent test command (within last 4 messages)
                    for tc in reversed(test_commands):
                        if tc.get("output_msg_index") is None and tc["msg_index"] < i and i - tc["msg_index"] <= 4:
                            tc["output_msg_index"] = i
                            tc["returncode_hint"] = rc
                            test_runs.append(
                                {
                                    "msg_index": tc["msg_index"],
                                    "command": tc["command"],
                                    "output_msg_index": i,
                                    "returncode": rc,
                                }
                            )
                            # Collect test failures from this output
                            for fail in C.extract_failed_tests_from_output(output):
                                if fail not in test_failures:
                                    test_failures.append(fail)
                            # Keep a short excerpt for human inspection
                            test_output_samples.append(
                                {
                                    "test_command_index": tc["msg_index"],
                                    "output_msg_index": i,
                                    "output_excerpt": output[:1500],
                                }
                            )
                            break

        # --- Viewed-but-dropped / Edited-but-not-viewed derivation needs the patch first
        patch_diff = info.get("submission") or ""
        patch_info = C.parse_unified_diff(patch_diff)

        # Final PATCH_CONTEXT = last declaration
        final_pc_files: list[dict] = []
        if patch_context_blocks:
            last_block = patch_context_blocks[-1]
            for path, spans in C.parse_file_lines_pairs(last_block).items():
                rel = C.normalize_repo_path(path) or path
                for span in spans:
                    final_pc_files.append(
                        {"file": rel, "lines": f"{span[0]}-{span[1]}"}
                    )
        # Also include any PATCH_CONTEXT declarations without explicit Lines
        # as `{file: ..., lines: '...'}` placeholder-free entries.

        # All patch context blocks (the agent may have declared it incrementally)
        all_pc_files: list[dict] = []
        for blk in patch_context_blocks:
            for path, spans in C.parse_file_lines_pairs(blk).items():
                rel = C.normalize_repo_path(path) or path
                for span in spans:
                    all_pc_files.append({"file": rel, "lines": f"{span[0]}-{span[1]}"})

        # Viewed-but-not-final
        final_pc_paths = {C.normalize_repo_path(p["file"]) for p in final_pc_files}
        viewed_but_not_final = [
            f for f in viewed_files_in_order if f not in final_pc_paths
        ]
        # Edited-but-not-viewed
        viewed_set = set(viewed_files_in_order)
        edited_but_not_viewed = [
            f for f in patch_info["edited_files"] if f not in viewed_set
        ]

        # viewed_total_line_bytes = sum of (end-start+1) across all spans
        viewed_total_line_bytes = 0
        for spans in viewed_spans.values():
            for s, e in spans:
                viewed_total_line_bytes += max(0, e - s + 1)

        # Shape signals
        repeated_edit_patterns: list[dict] = _detect_repeated_edit_patterns(
            patch_info["edited_spans"]
        )

        # --- Populate ParsedTrajectory
        pt.viewed_files_in_order = viewed_files_in_order
        pt.viewed_files_count = len(viewed_files_in_order)
        pt.viewed_spans = viewed_spans
        pt.viewed_total_line_bytes = viewed_total_line_bytes

        pt.bash_commands_count = bash_count
        pt.search_commands = search_commands
        pt.search_commands_count = len(search_commands)

        pt.test_commands = test_commands
        pt.test_runs = test_runs
        pt.test_runs_count = len(test_runs)
        pt.test_output_samples = test_output_samples[:8]  # cap to keep size sane
        pt.test_failures = test_failures
        pt.test_failures_count = len(test_failures)
        pt.possible_regression_failures = []  # placeholder; populated by post-hoc analyzer

        pt.patch_context_files = all_pc_files
        pt.patch_context_files_count = len(all_pc_files)
        pt.final_patch_context_files = final_pc_files
        pt.final_patch_context_files_count = len(final_pc_files)

        pt.edited_files = patch_info["edited_files"]
        pt.edited_files_count = len(patch_info["edited_files"])
        pt.edited_hunks_total = patch_info["hunks_total"]
        pt.edited_spans_per_file = patch_info["edited_spans"]
        # `changed_files` mirrors `edited_files` for compat with the original v0.1 schema
        pt.changed_files = list(patch_info["edited_files"])
        pt.changed_files_count = len(patch_info["edited_files"])
        pt.changed_lines_total = patch_info["added"] + patch_info["removed"]
        pt.changed_lines_added = patch_info["added"]
        pt.changed_lines_removed = patch_info["removed"]

        pt.repeated_edit_patterns = repeated_edit_patterns
        pt.repeated_edit_pattern_detected = bool(repeated_edit_patterns)
        pt.submitted_without_tests = submitted_without_tests
        pt.git_checkout_count = git_checkout_count

        pt.viewed_but_not_final_files = viewed_but_not_final
        pt.viewed_but_not_final_files_count = len(viewed_but_not_final)
        pt.edited_but_not_viewed_files = edited_but_not_viewed
        pt.edited_but_not_viewed_files_count = len(edited_but_not_viewed)

        pt.last_user_messages_tail = last_user_tail[-3:]

        pt.quality = {
            "has_explore_context": has_explore_context,
            "has_patch_context": has_patch_context,
            "has_patch_diff": bool(patch_diff),
            "has_test_outputs": bool(test_output_samples),
            "parse_warnings": warnings,
        }

        return pt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    import json

    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def _detect_repeated_edit_patterns(edited_spans_per_file: dict[str, list[int]]) -> list[dict]:
    """Detect likely mechanical repeated edits.

    Heuristic: same number of hunks across >=3 files suggests a batch replace.
    """
    if not edited_spans_per_file:
        return []
    counts = [len(v) for v in edited_spans_per_file.values()]
    if len(counts) >= 3:
        from collections import Counter

        most_common, n = Counter(counts).most_common(1)[0]
        if most_common >= 1 and n >= 3:
            return [
                {
                    "kind": "same_hunk_count_across_files",
                    "hunks_per_file": most_common,
                    "file_count": n,
                }
            ]
    return []
