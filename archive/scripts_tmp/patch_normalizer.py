"""Universal Patch Normalizer: fix LLM-generated diffs so they apply cleanly.

Strategy:
  1. Parse the LLM's intent from the diff (file, old lines, new lines, context)
  2. Find the matching location in the actual repo source
  3. Apply the changes and use `git diff` to produce a valid patch

Applied equally to ALL baselines. Does NOT read gold patch or test specs.
"""
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


def parse_llm_diff(patch_text: str) -> list[dict]:
    """Parse a (possibly malformed) unified diff into structured hunks.

    Returns list of {file: str, hunks: [{old_start, old_count, new_start, new_count,
              context_before, old_lines, new_lines, context_after}]}
    """
    # Clean markdown fences
    text = patch_text.strip()
    lines = text.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    text = "\n".join(lines)

    files = []
    current_file = None
    current_hunk = None
    hunk_lines = []

    for line in text.split("\n"):
        if line.startswith("--- ") or line.startswith("diff --git "):
            if current_hunk and current_file:
                _finalize_hunk(current_file, current_hunk, hunk_lines)
                hunk_lines = []
                current_hunk = None
            if line.startswith("--- "):
                path = line[4:].strip()
                # Strip a/ or b/ prefix
                if path.startswith("a/"):
                    path = path[2:]
                elif path.startswith("b/"):
                    path = path[2:]
                if current_file and current_file["path"] != path:
                    if current_hunk:
                        _finalize_hunk(current_file, current_hunk, hunk_lines)
                        hunk_lines = []
                        current_hunk = None
                    files.append(current_file)
                    current_file = None
                if not current_file:
                    current_file = {"path": path, "hunks": []}
            continue
        if line.startswith("+++ "):
            continue
        if line.startswith("@@") and line.endswith("@"):
            if current_hunk and current_file:
                _finalize_hunk(current_file, current_hunk, hunk_lines)
                hunk_lines = []
            current_hunk = _parse_hunk_header(line)
            continue
        if current_hunk:
            hunk_lines.append(line)

    if current_hunk and current_file:
        _finalize_hunk(current_file, current_hunk, hunk_lines)
    if current_file:
        files.append(current_file)

    return files


def _parse_hunk_header(line: str) -> dict:
    m = re.search(r"@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(.*)", line)
    if m:
        return {
            "old_start": int(m.group(1)),
            "old_count": int(m.group(2)) if m.group(2) else 1,
            "new_start": int(m.group(3)),
            "new_count": int(m.group(4)) if m.group(4) else 1,
            "context_func": m.group(5).strip(),
        }
    return {"old_start": 1, "old_count": 1, "new_start": 1, "new_count": 1, "context_func": ""}


def _finalize_hunk(file_info: dict, hunk: dict, lines: list[str]):
    """Split hunk lines into context_before, old_lines, new_lines, context_after."""
    old_lines = []
    new_lines = []
    context_before = []
    context_after = []
    state = "before"

    for line in lines:
        if line.startswith("-"):
            old_lines.append(line[1:])
            state = "middle"
        elif line.startswith("+"):
            new_lines.append(line[1:])
            state = "middle"
        elif line.startswith(" "):
            if state == "before":
                context_before.append(line[1:])
            else:
                context_after.append(line[1:])
        # Skip empty lines or non-prefixed lines - they're fuzzy context

    hunk["context_before"] = context_before
    hunk["old_lines"] = old_lines
    hunk["new_lines"] = new_lines
    hunk["context_after"] = context_after
    file_info["hunks"].append(hunk)


def find_in_file(file_content: str, search_lines: list[str]) -> Optional[int]:
    """Find the line number (0-indexed) where search_lines appear in file_content.

    Uses fuzzy matching: requires at least 2 context lines to match. Returns line number or None.
    """
    file_lines = file_content.split("\n")
    search_text = "\n".join(search_lines).strip()
    if not search_text:
        return None

    # Try exact match first
    for i in range(len(file_lines) - len(search_lines) + 1):
        chunk = "\n".join(file_lines[i:i+len(search_lines)])
        if chunk.strip() == search_text:
            return i

    # Try subset match (at least 2 lines matching)
    if len(search_lines) >= 2:
        for i in range(len(file_lines) - 2 + 1):
            matches = 0
            for j, sl in enumerate(search_lines[:8]):  # check first 8 lines
                if i + j < len(file_lines) and file_lines[i + j].strip() == sl.strip():
                    matches += 1
            if matches >= min(2, len(search_lines)):
                return i

    return None


def apply_hunk(file_lines: list[str], hunk: dict, match_line: int) -> list[str]:
    """Apply a hunk's changes at match_line, return new file_lines."""
    # Build the full old block: context_before + old_lines + context_after
    old_block = hunk["context_before"] + hunk["old_lines"] + hunk["context_after"]
    new_block = hunk["context_before"] + hunk["new_lines"] + hunk["context_after"]

    # Verify the match by checking if old_block matches at match_line
    old_at_site = file_lines[match_line:match_line + len(old_block)]
    # Strip comparison
    matches = all(
        a.strip() == b.strip()
        for a, b in zip(old_at_site, old_block)
        if a.strip() or b.strip()  # allow blank lines to be fuzzy
    )
    if not matches:
        # Try just matching the deletions
        pass  # continue anyway, best effort

    # Replace
    result = file_lines[:match_line] + new_block + file_lines[match_line + len(old_block):]
    return result


def normalize_patch(raw_patch: str, repo_path: str) -> dict:
    """Normalize a raw LLM patch against a repo checkout.

    Returns {success, normalized_patch, report}
    """
    parsed = parse_llm_diff(raw_patch)

    if not parsed:
        return {"success": False, "error": "no_files_parsed", "normalized_patch": ""}

    report = {"files": []}
    all_diffs = []

    for file_info in parsed:
        path = file_info["path"]
        # Resolve path: strip common prefixes like /testbed/
        clean_path = path.replace("/testbed/", "").lstrip("/")
        abs_path = Path(repo_path) / clean_path

        if not abs_path.is_file():
            report["files"].append({"path": clean_path, "status": "file_not_found"})
            continue

        original_content = abs_path.read_text(encoding="utf-8")
        file_lines = original_content.split("\n")
        file_report = {"path": clean_path, "hunks": []}

        for hunk in file_info["hunks"]:
            # Try to find the context in the file
            search = hunk.get("context_before", []) + hunk.get("old_lines", [])
            if not search:
                # Try the context_func to find location
                func = hunk.get("context_func", "")
                if func:
                    for i, line in enumerate(file_lines):
                        if func in line:
                            match = i
                            break
                    else:
                        match = None
                else:
                    match = None
            else:
                match = find_in_file(original_content, search)

            hunk_report = {
                "old_start": hunk.get("old_start"),
                "matched_line": match + 1 if match is not None else None,  # 1-indexed
                "old_lines": len(hunk.get("old_lines", [])),
                "new_lines": len(hunk.get("new_lines", [])),
                "context_func": hunk.get("context_func", ""),
            }

            if match is not None:
                file_lines = apply_hunk(file_lines, hunk, match)
                hunk_report["status"] = "applied"
            else:
                hunk_report["status"] = "no_match"

            file_report["hunks"].append(hunk_report)

        # Write modified file back
        new_content = "\n".join(file_lines)
        abs_path.write_text(new_content, encoding="utf-8")
        file_report["modified"] = new_content != original_content

        report["files"].append(file_report)

    # Generate a proper diff from the repo
    result = subprocess.run(
        ["git", "diff", "--", "."],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    diff = result.stdout.strip()

    success = bool(diff) and "diff --git" in diff

    # Reset repo to undo the modifications (caller handles this)
    return {
        "success": success,
        "normalized_patch": diff,
        "report": report,
    }


def try_apply_with_normalizer(
    container_name: str,
    patch_text: str,
    repo_path: str = "/testbed",
) -> dict:
    """Attempt to apply a patch in a docker container using the normalizer.

    Process:
      1. Try strict git apply
      2. If fail, run normalize_patch inside the container to fix the diff
      3. Try git apply again with normalized patch
    """
    result = {
        "strict_apply_ok": False,
        "normalized_apply_ok": False,
        "eval_patch_type": "none",
        "normalization_report": {},
        "error_summary": "",
    }

    # Write patch to temp
    Path("/tmp/_raw_patch.diff").write_text(patch_text, encoding="utf-8")

    # Step 1: Try strict git apply
    subprocess.run(
        ["docker", "cp", "/tmp/_raw_patch.diff", f"{container_name}:/tmp/raw_patch.diff"],
        capture_output=True,
    )
    r = subprocess.run(
        ["docker", "exec", container_name, "bash", "-lc",
         "cd /testbed && git apply --check /tmp/raw_patch.diff 2>&1 && git apply /tmp/raw_patch.diff 2>&1"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode == 0:
        result["strict_apply_ok"] = True
        result["eval_patch_type"] = "strict_raw"
        return result

    result["error_summary"] = (r.stdout + r.stderr)[:200]

    # Step 2: Run normalizer inside container
    # Write the normalizer as a small script, run it inside the container
    normalizer_script = r'''
import re, subprocess, json
from pathlib import Path

def parse_llm_diff(text):
    text = text.strip()
    lines = text.split("\n")
    if lines and lines[0].startswith("```"): lines = lines[1:]
    if lines and lines[-1].startswith("```"): lines = lines[:-1]
    text = "\n".join(lines)

    files = []
    cur_file = None
    cur_hunk = None
    hunk_lines = []

    for line in text.split("\n"):
        if line.startswith("--- "):
            if cur_hunk and cur_file:
                cur_file.setdefault("hunks", []).append({**cur_hunk, "lines": hunk_lines[:]})
                hunk_lines = []
                cur_hunk = None
            if cur_file:
                files.append(cur_file)
            path = line[4:].strip()
            for pre in ("a/", "b/"):
                if path.startswith(pre): path = path[len(pre):]
            cur_file = {"path": path, "hunks": []}
            continue
        if line.startswith("+++ "): continue
        if line.startswith("diff --git "): continue
        if line.startswith("@@") and "@@" in line[3:]:
            if cur_hunk and cur_file:
                cur_file.setdefault("hunks", []).append({**cur_hunk, "lines": hunk_lines[:]})
                hunk_lines = []
            m = re.search(r"@@ -(\d+),?\d* \+(\d+),?\d* @@", line)
            cur_hunk = {"old_start": int(m.group(1)) if m else 1, "new_start": int(m.group(2)) if m else 1}
            continue
        if cur_hunk:
            hunk_lines.append(line)

    if cur_hunk and cur_file:
        cur_file.setdefault("hunks", []).append({**cur_hunk, "lines": hunk_lines[:]})
    if cur_file:
        files.append(cur_file)

    return files


def apply_file_changes(file_path, hunks):
    """Apply changes and git add the file. Return True if any hunk applied."""
    abs_path = Path("/testbed") / file_path.replace("/testbed/", "").lstrip("/")
    if not abs_path.is_file():
        return False

    content = abs_path.read_text()
    lines = content.split("\n")
    modified = False

    for hunk in hunks:
        hunk_lines = hunk.get("lines", [])
        if not hunk_lines:
            continue

        # Extract old and new lines
        old_lines = []
        new_lines = []
        ctx_before = []
        ctx_after = []
        state = "before"
        for line in hunk_lines:
            if line.startswith("-"):
                old_lines.append(line[1:])
                state = "middle"
            elif line.startswith("+"):
                new_lines.append(line[1:])
                state = "middle"
            elif line.startswith(" "):
                if state == "before":
                    ctx_before.append(line[1:])
                else:
                    ctx_after.append(line[1:])

        if not old_lines and not new_lines:
            continue

        # Search for old block in file
        old_block = ctx_before + old_lines + ctx_after
        new_block = ctx_before + new_lines + ctx_after

        if not old_block:
            continue

        # Find matching position
        found = False
        for i in range(len(lines) - len(old_block) + 1):
            match = True
            for j, ol in enumerate(old_block):
                fl = lines[i + j].strip() if i + j < len(lines) else ""
                if ol.strip() and fl != ol.strip():
                    match = False
                    break
            if match:
                # Apply replacement
                lines = lines[:i] + new_block + lines[i + len(old_block):]
                modified = True
                found = True
                break

        if not found:
            # Try to find just the deletions
            del_only = [l for l in old_lines if l.strip()]
            if del_only:
                for i in range(len(lines)):
                    matches = 0
                    for j, dl in enumerate(del_only[:3]):
                        if i + j < len(lines) and lines[i + j].strip() == dl.strip():
                            matches += 1
                    if matches >= min(2, len(del_only)):
                        lines = lines[:i] + new_block + lines[i + len(old_block):]
                        modified = True
                        break

    if modified:
        abs_path.write_text("\n".join(lines))
    return modified


# Main
patch_text = Path("/tmp/raw_patch.diff").read_text()
files = parse_llm_diff(patch_text)

results = []
for f in files:
    ok = apply_file_changes(f["path"], f.get("hunks", []))
    results.append({"path": f["path"], "applied": ok})

print(json.dumps({"files": results}))
'''

    norm_script = Path("/tmp/_normalize.py")
    norm_script.write_text(normalizer_script)
    subprocess.run(
        ["docker", "cp", str(norm_script), f"{container_name}:/tmp/normalize.py"],
        capture_output=True,
    )

    # Run the normalizer inside the container (this modifies files in /testbed)
    r = subprocess.run(
        ["docker", "exec", container_name, "bash", "-lc",
         "cd /testbed && python3 /tmp/normalize.py 2>&1"],
        capture_output=True, text=True, timeout=60,
    )
    result["normalization_report"] = (r.stdout + r.stderr)[:1000]

    # Generate proper diff from the modified workspace
    r = subprocess.run(
        ["docker", "exec", container_name, "bash", "-lc",
         "cd /testbed && git diff 2>&1"],
        capture_output=True, text=True, timeout=30,
    )
    normalized_diff = r.stdout

    if normalized_diff.strip():
        # Write normalized diff and apply it to a clean checkout
        Path("/tmp/_norm_patch.diff").write_text(normalized_diff)

        # Reset repo first
        subprocess.run(
            ["docker", "exec", container_name, "bash", "-lc",
             "cd /testbed && git checkout -- . 2>&1"],
            capture_output=True,
        )

        # Apply test_patch again if present
        subprocess.run(
            ["docker", "exec", container_name, "bash", "-lc",
             "cd /testbed && git apply /tmp/test.diff 2>&1"],
            capture_output=True,
        )

        # Apply normalized model patch
        subprocess.run(
            ["docker", "cp", "/tmp/_norm_patch.diff", f"{container_name}:/tmp/norm_patch.diff"],
            capture_output=True,
        )
        r = subprocess.run(
            ["docker", "exec", container_name, "bash", "-lc",
             "cd /testbed && git apply --check /tmp/norm_patch.diff 2>&1 && git apply /tmp/norm_patch.diff 2>&1"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            result["normalized_apply_ok"] = True
            result["eval_patch_type"] = "normalized"
        else:
            result["error_summary"] += " | norm_apply: " + (r.stdout + r.stderr)[:200]
    else:
        result["error_summary"] += " | no_diff_from_normalizer"

    return result


if __name__ == "__main__":
    # Quick test
    if len(sys.argv) > 1:
        patch_text = Path(sys.argv[1]).read_text()
        print("Parsed:", parse_llm_diff(patch_text))
    else:
        print("Usage: normalize_patch.py <patch.diff>")
