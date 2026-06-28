"""Workspace-based retry runner.

Replaces the direct-diff protocol with a file-editing protocol:

  OLD (broken):  LLM outputs unified diff text  → save as patch.diff → git apply fails
  NEW (correct): LLM outputs file edits as code → write to workspace → git diff → valid patch

The retry prompt asks the model to output COMPLETE MODIFIED FILE CONTENT
for each file it wants to change. We write those files to a clean git workspace
and let git diff produce a guaranteed-valid patch.

Supports all 4 baselines:
  base_miniswe       → no retry (final = attempt_1)
  feedback_retry     → retry with feedback packet
  broad_expansion    → retry with broad expansion packet
  condiag_packet_only → retry with ConDiag context packet
"""
from __future__ import annotations

import json, os, re, subprocess, time
from pathlib import Path
from typing import Optional

# ---- API helper (same as retry_executor.py) ----

def _call_llm(system: str, user_msg: str, *extra_msgs: str,
              model: str = "deepseek/deepseek-v4-pro",
              api_key: str = "",
              api_base: str = "https://api.deepseek.com",
              max_tokens: int = 32768) -> str:
    import urllib.request, urllib.error

    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")

    api_model = "deepseek-chat" if model.startswith("deepseek/") else model

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_msg}]
    for extra in extra_msgs:
        if extra:
            messages.append({"role": "user", "content": extra})

    body = json.dumps({
        "model": api_model, "messages": messages,
        "max_tokens": max_tokens, "temperature": 0.0,
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
    return choices[0].get("message", {}).get("content", "")


# ---- File edit parsing ----

_FILE_HEADER_RE = re.compile(r"###\s*FILE:\s*(\S+)")


def parse_file_edits(response: str) -> dict[str, str]:
    """Parse model response into {file_path: full_new_content}.

    Expected format:
        ### FILE: path/to/file.py
        ```python
        [full modified content]
        ```

        ### FILE: path/to/other.py
        ```python
        [full modified content]
        ```

    Also handles bare code blocks without FILE headers (uses the first
    plausible source path found in the response).
    """
    edits = {}

    # Strategy 1: Parse ### FILE: headers
    parts = _FILE_HEADER_RE.split(response)
    # parts alternates: [before_first_header, path1, content1, path2, content2, ...]
    if len(parts) >= 3 and _FILE_HEADER_RE.match("### FILE: " + (parts[1] or "").strip()):
        # Remove the leading text before first header
        parts = parts[1:]  # drop leading junk
        for i in range(0, len(parts) - 1, 2):
            path = parts[i].strip()
            content_block = parts[i + 1] if i + 1 < len(parts) else ""
            code = _extract_code_block(content_block)
            if code and len(code) > 20:
                edits[path] = code
        if edits:
            return edits

    # Strategy 2: Look for code blocks with file paths in comments or nearby text
    # Pattern: a line with a file path, then a ``` block
    block_pattern = re.compile(
        r"(?:^|\n)(?:[#/]+\s*)?((?:[a-z_][\w/]*/)?[a-z_][\w/]*\.py)\s*\n"
        r"```(?:python|py)?\s*\n(.*?)```",
        re.DOTALL | re.IGNORECASE
    )
    for m in block_pattern.finditer(response):
        path = m.group(1).strip()
        code = m.group(2).strip()
        if code and len(code) > 20 and path not in edits:
            edits[path] = code

    return edits


def _extract_code_block(text: str) -> str:
    """Extract code from a markdown code block.
    
    Handles truncated output (no closing ```) by stripping the opening fence.
    """
    # Try full code block extraction first
    m = re.search(r"```(?:python|py|diff)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # No closing fence found (likely truncated output).
    # Strip the opening fence line if present.
    stripped = re.sub(r"^```(?:python|py|diff)?\s*\n?", "", text, count=1)
    stripped = stripped.strip()
    if stripped:
        return stripped
    return text.strip()


# ---- Workspace management ----

def setup_workspace(instance_id: str, test_patch: str) -> Path:
    """Ensure a clean workspace at base_commit with test_patch applied.

    Returns the workspace path.
    """
    ws_base = Path("/home/swelite/condiag/workspaces")
    ws = ws_base / instance_id
    if not ws.is_dir():
        raise FileNotFoundError(f"Workspace not found: {ws}")

    repo = ws / "repo_base"
    if not repo.is_dir():
        raise FileNotFoundError(f"Workspace repo_base not found: {repo}")

    # Reset to clean state
    subprocess.run(["git", "-C", str(repo), "checkout", "--", "."],
                   capture_output=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), "clean", "-fd"],
                   capture_output=True, timeout=30)

    # Apply test_patch (adds new tests without fixing the bug)
    if test_patch.strip():
        tp_file = repo / "_test_patch.diff"
        tp_file.write_text(test_patch, encoding="utf-8")
        r = subprocess.run(
            ["git", "-C", str(repo), "apply", str(tp_file)],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            raise RuntimeError(f"test_patch apply failed: {r.stdout} {r.stderr}")
        tp_file.unlink()

    return repo


def read_relevant_sources(ws: Path, context_packet: str, max_bytes: int = 30000) -> str:
    """Read source files referenced in the context_packet.

    Extracts file paths from the packet, reads those files from the workspace,
    and returns them as a code block for inclusion in the prompt.
    """
    # Extract likely file paths from the context_packet
    path_pattern = re.compile(r"(?:`|['\"]|^|[^\w/])(([\w.-]+/)*[\w.-]+\.py)(?:`|['\"]|$|[^\w/])", re.MULTILINE)
    seen = set()
    sources = []
    total = 0

    for m in path_pattern.finditer(context_packet):
        fname = m.group(1)
        if fname in seen:
            continue
        seen.add(fname)

        # Try to find the file in the workspace
        candidate = ws / fname
        if not candidate.is_file():
            # Try without leading path components
            for found in ws.rglob(fname):
                candidate = found
                break
        if not candidate.is_file():
            continue

        try:
            content = candidate.read_text(encoding="utf-8")
            if total + len(content) > max_bytes:
                # Truncate: include first N lines
                lines = content.split("\n")
                available = max_bytes - total
                truncated = "\n".join(lines[:max(50, available // 100)])
                sources.append(f"### FILE: {fname} (truncated)\n```python\n{truncated}\n```")
                break
            sources.append(f"### FILE: {fname}\n```python\n{content}\n```")
            total += len(content)
        except Exception:
            pass

    return "\n\n".join(sources)


# ---- Main entry point ----

def run_workspace_retry(
    run_dir: Path,
    instance_id: str,
    context_packet_path: Optional[Path] = None,
    test_patch: str = "",
    api_key: str = "",
) -> dict:
    """Execute a workspace-based retry.

    1. Set up clean workspace at base_commit + test_patch
    2. Build prompt with issue + context_packet + source files
    3. Call LLM to get file edits
    4. Write edits to workspace
    5. git diff to produce valid patch
    6. Save to attempt_2/patch.diff

    Returns dict with has_patch, patch_chars, attempt_2 path, etc.
    """
    run_dir = Path(run_dir)
    attempt_2_dir = run_dir / "attempt_2"
    attempt_2_dir.mkdir(parents=True, exist_ok=True)

    # Load context_packet
    ctx_pkt = context_packet_path or (run_dir / "intervention" / "context_packet.md")
    if ctx_pkt.is_file():
        packet_text = ctx_pkt.read_text(encoding="utf-8")
    else:
        packet_text = "(no context packet — retrying without added context)"

    # Load issue from attempt_1 traj
    traj_path = run_dir / "attempt_1" / "traj.json"
    issue_text = _read_issue_from_traj(traj_path, instance_id)

    # Set up workspace
    ws = setup_workspace(instance_id, test_patch)

    # Read relevant source files
    sources = read_relevant_sources(ws, packet_text)

    # Build prompt
    system, user_msg = _build_workspace_prompt(issue_text, packet_text, sources, instance_id)

    # Phase 1: Analysis + file edits
    raw = _call_llm(system, user_msg, api_key=api_key, max_tokens=4096)

    # Parse file edits from response
    edits = parse_file_edits(raw)

    # Phase 2: If no edits found, try a force-edit prompt
    if not edits:
        analysis_brief = raw[:3000]
        force_prompt = (
            f"Your analysis:\n\n{analysis_brief}\n\n"
            f"Based on your analysis above, output the COMPLETE modified content "
            f"for each file you want to change. Use this EXACT format:\n\n"
            f"### FILE: path/to/file.py\n"
            f"```python\n"
            f"[full modified file content here]\n"
            f"```\n\n"
            f"Make sure the code is syntactically correct and complete."
        )
        system2 = (
            "You are a code editor. Output ONLY the modified file contents. "
            "Use ### FILE: header for each file. Include the COMPLETE file, "
            "not just the changed lines. Be precise."
        )
        raw2 = _call_llm(system2, force_prompt, api_key=api_key, max_tokens=32768)
        edits = parse_file_edits(raw2)
        if edits:
            raw = raw2  # use phase 2 for artifact logging

    # Write edits to workspace
    files_written = []
    for fname, content in edits.items():
        # Resolve path: strip /testbed/ prefix if present
        clean = fname.replace("/testbed/", "").lstrip("/")
        target = ws / clean
        if not target.parent.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
        # Safety: strip any markdown fences that survived extraction
        content = content.lstrip("`")
        if content.startswith("python\n") or content.startswith("py\n"):
            content = content.split("\n", 1)[1] if "\n" in content else ""
        target.write_text(content, encoding="utf-8")
        files_written.append(clean)

    # git diff to produce valid patch
    r = subprocess.run(
        ["git", "-C", str(ws), "diff", "HEAD"],
        capture_output=True, text=True, timeout=30
    )
    diff = r.stdout.strip()

    has_patch = bool(diff) and "diff --git" in diff

    # Save artifacts
    patch_path = attempt_2_dir / "patch.diff"
    if has_patch:
        patch_path.write_text(diff, encoding="utf-8")
    else:
        patch_path.write_text(raw, encoding="utf-8")  # fallback: raw response

    messages = {
        "system": system[:500],
        "user_msg": user_msg[:3000],
        "response": raw[:8000],
        "files_written": files_written,
        "patch_chars": len(diff),
        "has_patch": has_patch,
    }
    (attempt_2_dir / "messages.json").write_text(
        json.dumps(messages, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "handled": True,
        "reason": "workspace_retry_executed",
        "instance_id": instance_id,
        "has_patch": has_patch,
        "patch_chars": len(diff),
        "files_written": files_written,
        "attempt_2": str(attempt_2_dir),
    }


def _read_issue_from_traj(traj_path: Path, instance_id: str) -> str:
    """Extract the original issue/problem statement from attempt_1's traj.json."""
    if not traj_path.is_file():
        # Try glob fallback
        candidates = list(traj_path.parent.glob("*.traj.json")) + list(traj_path.parent.glob("traj.json"))
        for c in candidates:
            if c.is_file():
                traj_path = c
                break
        else:
            return f"Fix the bug in instance {instance_id}."

    with traj_path.open("r", encoding="utf-8") as f:
        traj = json.load(f)

    msgs = traj.get("messages") or []
    for m in msgs:
        if m.get("role") == "user":
            raw = m.get("content", "")
            # Extract PR description
            pr_match = re.search(
                r"<pr_description>(.*?)(?:</pr_description>|Consider the following)",
                raw, re.DOTALL
            )
            if pr_match:
                return "<pr_description>\n" + pr_match.group(1).strip() + "\n</pr_description>"
            return raw[:4000]

    return f"Fix the bug in instance {instance_id}."


def _build_workspace_prompt(
    issue: str,
    context_packet: str,
    sources: str,
    instance_id: str,
) -> tuple[str, str]:
    """Build the system + user messages for workspace-based retry."""

    system = (
        "You are an expert software engineer fixing a bug. You have access to "
        "the repository source code and a diagnosis of a previous failed attempt.\n\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. Analyze the bug and the provided recovery context carefully.\n"
        "2. Identify which source files need to be modified.\n"
        "3. For EACH file you modify, output the COMPLETE new file content.\n"
        "4. Use this EXACT format for each file:\n\n"
        "### FILE: path/to/file.py\n"
        "```python\n"
        "[complete modified file content — NOT just the diff, the entire file]\n"
        "```\n\n"
        "5. Make sure the Python code is syntactically correct.\n"
        "6. Do NOT output unified diff format (no @@ headers, no ---/+++).\n"
        "7. Output the FULL file content, not just the changed parts.\n"
        "8. Preserve all existing imports, functions, and classes — only change what's needed."
    )

    user_msg = (
        f"## Bug Report\n\n"
        f"{issue[:4000]}\n\n"
        f"## Recovery Context (from previous failed attempt)\n\n"
        f"{context_packet[:6000]}\n\n"
        f"## Relevant Source Files\n\n"
        f"{sources}\n\n"
        f"---\n"
        f"Based on the bug report and recovery context above, "
        f"identify the files that need changes and output the COMPLETE "
        f"modified content for each file. "
        f"Use ### FILE: header + ```python code block for each file."
    )

    return system, user_msg


# ---- Smoke runner for 3 cases x 4 baselines ----

WS_BASE = Path("/home/swelite/condiag/workspaces")

SMOKE_CASES = {
    "django__django-13513": {
        "test_patch": "",  # populated from dataset
        "ws": WS_BASE / "django__django-13513",
    },
    "sympy__sympy-19954": {
        "test_patch": "",
        "ws": WS_BASE / "sympy__sympy-19954",
    },
    "django__django-11099": {
        "test_patch": "",
        "ws": WS_BASE / "django__django-11099",
    },
}


def smoke_workspace_retry(
    runs_root: str = "/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe",
    api_key: str = "",
    cases: Optional[list[str]] = None,
) -> dict:
    """Run workspace-based retry on selected instances x baselines."""
    from datasets import load_dataset

    # Load test_patches from dataset
    ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")
    test_patches = {}
    for row in ds:
        iid = row.get("instance_id", "")
        if iid in SMOKE_CASES:
            test_patches[iid] = row.get("test_patch") or ""

    baselines = ["feedback_retry", "broad_expansion", "condiag_packet_only"]
    if cases is None:
        cases = list(SMOKE_CASES.keys())

    results: dict[str, dict] = {}

    for iid in cases:
        tp = test_patches.get(iid, "")
        for bl in baselines:
            key = f"{iid}/{bl}"
            run_dir = Path(runs_root) / bl / iid
            ctx_pkt = run_dir / "intervention" / "context_packet.md"

            if not ctx_pkt.is_file():
                print(f"SKIP {key}: no context_packet.md")
                results[key] = {"error": "no_context_packet"}
                continue

            print(f"RETRY [{key}]...")
            try:
                r = run_workspace_retry(
                    run_dir=run_dir,
                    instance_id=iid,
                    context_packet_path=ctx_pkt,
                    test_patch=tp,
                    api_key=api_key,
                )
                results[key] = r
                print(f"  -> has_patch={r['has_patch']} chars={r['patch_chars']} "
                      f"files={r['files_written']}")
            except Exception as e:
                results[key] = {"error": str(e)}
                print(f"  -> ERROR: {e}")

    # Summary
    print(f"\n{'='*60}")
    print("WORKSPACE RETRY RESULTS")
    print(f"{'='*60}")
    for k, v in sorted(results.items()):
        if "error" in v:
            print(f"  {k:50s} ERROR: {v['error'][:60]}")
        else:
            print(f"  {k:50s} patch={v.get('patch_chars',0):>5d} chars  "
                  f"files={v.get('files_written',[])}")

    return results


if __name__ == "__main__":
    import sys
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not set. Set it via environment variable.")
        print("Usage: DEEPSEEK_API_KEY=sk-... python3 -m experiments.retry_runner_workspace")
        sys.exit(1)
    results = smoke_workspace_retry(api_key=api_key)
