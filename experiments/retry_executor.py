"""Retry executor: inject context_packet into a fresh LLM call to produce attempt_2.

Reads attempt_1's traj.json for system_message + problem_statement, then appends
the context_packet (from intervention/) as additional context, and calls the LLM
(DeepSeek V4 via OpenAI-compatible API) to generate a corrected patch.

Output: attempt_2/patch.diff + attempt_2/messages.json (the LLM conversation)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional


# ---- API helper ----

def _call_llm(system: str, user_msg: str, *extra_msgs: str,
              model: str = "deepseek/deepseek-v4-pro",
              api_key: str = "",
              api_base: str = "https://api.deepseek.com") -> str:
    """Single-shot LLM call. Returns the assistant's text response."""
    import urllib.request
    import urllib.error

    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")

    if model.startswith("deepseek/"):
        api_model = "deepseek-chat"
    else:
        api_model = model

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    for extra in extra_msgs:
        if extra:
            messages.append({"role": "user", "content": extra})

    body = json.dumps({
        "model": api_model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.0,
    }).encode("utf-8")

    url = f"{api_base.rstrip('/')}/v1/chat/completions"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")[:500]
        raise RuntimeError(f"LLM API error {e.code}: {err_body}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM returned no choices: {json.dumps(data)[:300]}")

    content = choices[0].get("message", {}).get("content", "")
    return content


# ---- Diff extraction ----

_DIFF_PATTERN = re.compile(
    r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE
)

def _extract_diff(text: str) -> str:
    """Extract unified diff from LLM response."""
    # Try fenced code block first
    m = _DIFF_PATTERN.search(text)
    if m:
        return m.group(1).strip()
    # Fallback: look for diff --git
    lines = text.split("\n")
    out: list[str] = []
    in_diff = False
    for line in lines:
        if line.startswith("diff --git"):
            in_diff = True
        if in_diff:
            out.append(line)
        elif line.startswith("```") and in_diff:
            break
    if out:
        return "\n".join(out)
    return text.strip()


# ---- Retry message builder ----

def _read_traj_essentials(traj_path: Path, instance_id: str) -> tuple[str, str]:
    """Read model name + cleaned problem statement from traj.json.

    The raw user message contains mini-SWE exploration formatting instructions
    (e.g. "Your response must contain...") mixed with the actual PR description.
    We extract only the PR description part.
    """
    if not traj_path.is_file():
        raise FileNotFoundError(f"traj.json not found: {traj_path}")

    with traj_path.open("r", encoding="utf-8") as f:
        traj = json.load(f)

    info = traj.get("info") or {}
    model_cfg = (info.get("config") or {}).get("model") or {}
    model = model_cfg.get("model_name", "deepseek/deepseek-v4-pro")

    msgs = traj.get("messages") or []
    user_raw = ""
    for m in msgs:
        if m.get("role") == "user" and not user_raw:
            user_raw = m.get("content", "")
            break
    if not user_raw:
        user_raw = f"Fix the bug in instance {instance_id}."

    # Clean: extract only the PR description (between <pr_description> tags if present)
    import re
    pr_match = re.search(r"<pr_description>(.*?)(?:</pr_description>|Consider the following)", user_raw, re.DOTALL)
    if pr_match:
        user = "<pr_description>\n" + pr_match.group(1).strip() + "\n</pr_description>"
    else:
        # Fallback: strip mini-SWE formatting instructions
        user = re.sub(r"Your response must.*?<format_example>.*?</format_example>", "", user_raw, flags=re.DOTALL)
        user = user.strip()
    if not user or len(user) < 50:
        user = user_raw[:4000]

    return model, user


def build_retry_prompt(
    traj_path: Path,
    context_packet_path: Path,
    instance_id: str,
) -> tuple[str, str]:
    """Build a single-stage prompt that asks for analysis TAGGED with THOUGHT
    (to satisfy fine-tune) followed by the actual diff in a ```diff block.

    DeepSeek mini-SWE is fine-tuned to always produce THOUGHT + ```bash.
    We hijack this: keep THOUGHT, but replace ```bash with ```diff.
    """
    model, user = _read_traj_essentials(traj_path, instance_id)

    if context_packet_path.is_file():
        packet = context_packet_path.read_text(encoding="utf-8")
    else:
        packet = "(no context packet — retrying without added context)"

    # Override the system message to hijack the fine-tuned format.
    system = (
        "You are a software engineer fixing a bug. Instead of exploring with "
        "bash commands, you must output your fix as a unified diff.\n\n"
        "CRITICAL: Your response MUST follow this exact format:\n\n"
        "THOUGHT: your analysis here\n\n"
        "```diff\n"
        "diff --git a/path/to/file b/path/to/file\n"
        "--- a/path/to/file\n"
        "+++ b/path/to/file\n"
        "@@ -line,count +line,count @@\n"
        "... your changes ...\n"
        "```\n\n"
        "Do NOT output ```bash. Do NOT explore. Output the diff directly."
    )

    # Truncate user message to avoid token explosion
    user_short = user[:4000]

    retry_user = (
        f"## Bug Report\n\n{user_short}\n\n"
        f"## Additional Recovery Context\n\n{packet[:6000]}\n\n"
        f"Analyze the bug and the recovery context, then output your "
        f"corrected patch AS A UNIFIED DIFF in a ```diff code block."
    )

    return model, system, retry_user


def _patch_llm_response(raw: str) -> str:
    """If the LLM still outputs exploration format, extract the meaningful part
    and re-ask the LLM to format it as a diff (second phase)."""
    # If already has a diff block, extract and return
    if "```diff" in raw:
        return _extract_diff(raw)
    if "diff --git" in raw:
        return _extract_diff(raw)
    # If the response has the THOUGHT + ```bash pattern, we can't fix it
    # in a single call. Return empty to signal "no diff produced".
    return ""

    return model, system, retry_user


# ---- Main entry point ----

def run_retry(
    run_dir: Path,
    instance_id: str,
    traj_path: Path,
    context_packet_path: Optional[Path] = None,
    api_key: str = "",
) -> dict:
    """Execute a retry: call LLM with context_packet, save attempt_2.

    Returns a dict suitable for handler return value.
    """
    run_dir = Path(run_dir)
    attempt_2 = run_dir / "attempt_2"
    attempt_2.mkdir(parents=True, exist_ok=True)

    ctx_pkt = context_packet_path or (run_dir / "intervention" / "context_packet.md")

    # 1. Build prompt (cleaned PR description + diff-only instructions)
    model, system, retry_user = build_retry_prompt(
        traj_path, ctx_pkt, instance_id
    )

    # 2. Phase 1: analysis call (leverages fine-tuned THOUGHT format)
    raw = _call_llm(system, retry_user, model=model, api_key=api_key)

    # 3. If no diff yet, Phase 2: force diff output
    if "```diff" not in raw and "diff --git" not in raw:
        # Truncate the analysis to avoid blowing up context
        analysis_brief = raw[:4000]
        phase2_prompt = (
            f"Your analysis:\n\n{analysis_brief}\n\n"
            f"Based on your analysis above, output ONLY the unified diff "
            f"patch. No bash commands. No exploration. Just the diff in "
            f"a ```diff code block."
        )
        system2 = "Output ONLY a unified diff patch. No explanations. No bash. Just ```diff ... ```"
        raw2 = _call_llm(system2, phase2_prompt, model=model, api_key=api_key)
        raw = raw2  # use phase 2 output for diff extraction

    # 4. Extract diff
    diff = _extract_diff(raw)

    # 4. Save artifacts
    patch_path = attempt_2 / "patch.diff"
    patch_path.write_text(diff, encoding="utf-8")

    msgs_path = attempt_2 / "messages.json"
    msgs_path.write_text(json.dumps({
        "system": system[:500],
        "retry_user": retry_user[:2000],
        "response": raw[:8000],
        "patch_chars": len(diff),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    has_patch = len(diff.strip()) > 50 and ("diff --git" in diff or "--- " in diff or "@@" in diff)
    return {
        "handled": True,
        "reason": "retry_executed",
        "instance_id": instance_id,
        "model": model,
        "has_patch": has_patch,
        "patch_chars": len(diff),
        "attempt_2": str(attempt_2),
    }


# ---- Smoke runner ----

def smoke_retry_3cases(
    runs_root: str = "/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs",
    manifest_csv: str = "/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/manifest.csv",
    api_key: str = "",
    selection: Optional[list[str]] = None,
) -> dict:
    """Run retry on 3 selected instances × 4 baselines.

    Returns a dict with per-(instance, baseline) results.
    """
    import csv

    manifest = {}
    with open(manifest_csv, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            manifest[row["instance_id"]] = row

    if selection is None:
        selection = [
            "django__django-13513",
            "sympy__sympy-19954",
            "django__django-11099",
        ]

    baselines = [
        "feedback_retry",
        "broad_expansion",
        "condiag_packet_only",
    ]
    # Base: just re-run without context (same LLM, same ps, no extra context)
    # We'll treat base retry as a second attempt_1 call (no added context)

    results: dict[str, dict] = {}

    for iid in selection:
        row = manifest.get(iid)
        if not row:
            print(f"SKIP {iid}: not in manifest")
            continue
        traj_path = Path(row["traj_path"])
        for bl in baselines:
            key = f"{iid}/{bl}"
            run_dir = Path(runs_root) / "miniswe" / bl / iid
            ctx_pkt = run_dir / "intervention" / "context_packet.md"
            if not ctx_pkt.is_file():
                print(f"SKIP {key}: no context_packet.md")
                continue
            print(f"RETRY {key}...")
            try:
                r = run_retry(run_dir, iid, traj_path, ctx_pkt, api_key=api_key)
                results[key] = r
                print(f"  -> has_patch={r['has_patch']} chars={r['patch_chars']}")
            except Exception as e:
                results[key] = {"error": str(e)}
                print(f"  -> ERROR: {e}")

    # Also run base retry (no context augmentation) on the 3 cases
    for iid in selection:
        row = manifest.get(iid)
        if not row:
            continue
        traj_path = Path(row["traj_path"])
        key = f"{iid}/base_miniswe_retry"
        run_dir = Path(runs_root) / "miniswe" / "base_miniswe" / iid
        # base: no intervention/context_packet.md; pass None
        print(f"RETRY {key} (base, no context)...")
        try:
            r = run_retry(run_dir, iid, traj_path, None, api_key=api_key)
            results[key] = r
            print(f"  -> has_patch={r['has_patch']} chars={r['patch_chars']}")
        except Exception as e:
            results[key] = {"error": str(e)}
            print(f"  -> ERROR: {e}")

    return results


if __name__ == "__main__":
    import sys
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set")
        sys.exit(1)
    results = smoke_retry_3cases(api_key=api_key)
    print("\n=== RESULTS ===")
    for k, v in sorted(results.items()):
        patch = v.get("patch_chars", 0) if "error" not in v else "ERR"
        print(f"  {k:50s} patch={patch}")
