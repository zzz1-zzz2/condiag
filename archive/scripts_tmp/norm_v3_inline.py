"""Normalizer v3: handle pure-add hunks, no-op hunks, markdown wrapping.

Key improvements over v2:
  1. Pure-add hunks (0 del_lines): match by finding ctx_before + ctx_after
     as a contiguous block in the file, insert add_lines between them.
  2. No-op detection: skip hunks where del_lines == add_lines.
  3. Markdown fence stripping (already in v2, retained).
  4. Better debug: show what was searched and where it matched.
"""
import re, json, sys
from pathlib import Path


def read_file(p):
    with open(str(p), "r", encoding="utf-8") as f:
        return f.read()


def write_file(p, content):
    with open(str(p), "w", encoding="utf-8") as f:
        f.write(content)


def normalize(raw_patch_text, repo_root="/testbed"):
    # Clean markdown fences
    lines = [l for l in raw_patch_text.strip().split("\n") if not l.startswith("```")]
    patch_text = "\n".join(lines)

    # Parse files and hunks
    files = []
    cur_file = None
    cur_hunk = None
    hunk_lines = []

    for line in patch_text.split("\n"):
        if line.startswith("--- "):
            if cur_hunk and cur_file:
                cur_file.setdefault("hunks", []).append(dict(cur_hunk, lines=hunk_lines[:]))
                hunk_lines = []
                cur_hunk = None
            if cur_file:
                files.append(cur_file)
            path = line[4:].strip()
            for pre in ("a/", "b/"):
                if path.startswith(pre):
                    path = path[len(pre):]
            cur_file = {"path": path, "hunks": []}
            continue
        if line.startswith("+++ ") or line.startswith("diff --git "):
            continue
        if line.startswith("@@") and "@@" in line[3:]:
            if cur_hunk and cur_file:
                cur_file.setdefault("hunks", []).append(dict(cur_hunk, lines=hunk_lines[:]))
                hunk_lines = []
            cur_hunk = {"header": line}
            continue
        if cur_hunk is not None:
            hunk_lines.append(line)

    if cur_hunk and cur_file:
        cur_file.setdefault("hunks", []).append(dict(cur_hunk, lines=hunk_lines[:]))
    if cur_file:
        files.append(cur_file)

    results = []
    for f in files:
        file_path = f["path"].replace("/testbed/", "").lstrip("/")
        abs_path = Path(repo_root) / file_path
        if not abs_path.is_file():
            results.append({"path": file_path, "applied": False, "error": "not_found"})
            continue

        content = read_file(abs_path)
        file_lines = content.split("\n")
        modified = False
        hunk_results = []

        for h in f.get("hunks", []):
            hunk_lines = h.get("lines", [])
            del_lines = []
            add_lines = []
            ctx_before = []
            ctx_after = []
            state = "before"
            for hl in hunk_lines:
                if hl.startswith("-"):
                    del_lines.append(hl[1:])
                    state = "middle"
                elif hl.startswith("+"):
                    add_lines.append(hl[1:])
                    state = "middle"
                elif hl.startswith(" "):
                    if state == "before":
                        ctx_before.append(hl[1:])
                    else:
                        ctx_after.append(hl[1:])

            # Skip empty hunks
            if not del_lines and not add_lines:
                hunk_results.append({"status": "empty"})
                continue

            # Skip no-op hunks (same lines deleted and added)
            if del_lines == add_lines:
                hunk_results.append({"status": "noop", "note": "del == add, skipped"})
                continue

            found = False

            # --- Strategy 1: Match by deletion lines (same as v2) ---
            if del_lines:
                del_stripped = [d.strip() for d in del_lines]
                for i in range(len(file_lines) - len(del_lines) + 1):
                    matches = sum(
                        1 for j, dl in enumerate(del_stripped)
                        if file_lines[i + j].strip() == dl
                    )
                    if matches == len(del_lines):
                        old_block = file_lines[i:i + len(del_lines)]
                        new_block = add_lines
                        file_lines = file_lines[:i] + new_block + file_lines[i + len(old_block):]
                        modified = True
                        found = True
                        hunk_results.append({
                            "status": "applied", "match_line": i + 1,
                            "del_lines": len(del_lines), "add_lines": len(add_lines),
                            "method": "del_exact_match"
                        })
                        break

                if not found:
                    # Fuzzy: match first deletion line (len >= 8 chars)
                    first_del = del_lines[0].strip()
                    if len(first_del) >= 8:
                        for i, fl in enumerate(file_lines):
                            if fl.strip() == first_del:
                                new_block = add_lines
                                file_lines = file_lines[:i] + new_block + file_lines[i + len(del_lines):]
                                modified = True
                                found = True
                                hunk_results.append({
                                    "status": "applied", "match_line": i + 1,
                                    "method": "first_del_fuzzy"
                                })
                                break

            # --- Strategy 2: Pure addition (0 del_lines) ---
            if not found and not del_lines and add_lines:
                if ctx_before:
                    full_ctx = ctx_before + ctx_after
                    if full_ctx:
                        ctx_stripped = [c.strip() for c in full_ctx]
                        for i in range(len(file_lines) - len(full_ctx) + 1):
                            matches = sum(
                                1 for j, cl in enumerate(ctx_stripped)
                                if file_lines[i + j].strip() == cl
                            )
                            if matches == len(full_ctx):
                                insert_at = i + len(ctx_before)
                                file_lines = file_lines[:insert_at] + add_lines + file_lines[insert_at:]
                                modified = True
                                found = True
                                hunk_results.append({
                                    "status": "applied", "match_line": insert_at + 1,
                                    "method": "ctx_block_match", "add_lines": len(add_lines)
                                })
                                break

                if not found and ctx_before:
                    # Fuzzy: match just the last ctx_before line as insertion anchor
                    last_ctx = ctx_before[-1].strip()
                    if len(last_ctx) >= 8:
                        for i, fl in enumerate(file_lines):
                            if fl.strip() == last_ctx:
                                insert_at = i + 1
                                # Verify ctx_after if present
                                if ctx_after:
                                    next_expected = ctx_after[0].strip()
                                    if insert_at < len(file_lines) and file_lines[insert_at].strip() != next_expected:
                                        continue
                                file_lines = file_lines[:insert_at] + add_lines + file_lines[insert_at:]
                                modified = True
                                found = True
                                hunk_results.append({
                                    "status": "applied", "match_line": insert_at + 1,
                                    "method": "last_ctx_before_fuzzy", "add_lines": len(add_lines)
                                })
                                break

                if not found and not ctx_before:
                    # Try to match by ctx_after only (insert before ctx_after)
                    if ctx_after:
                        first_after = ctx_after[0].strip()
                        if len(first_after) >= 8:
                            for i, fl in enumerate(file_lines):
                                if fl.strip() == first_after:
                                    file_lines = file_lines[:i] + add_lines + file_lines[i:]
                                    modified = True
                                    found = True
                                    hunk_results.append({
                                        "status": "applied", "match_line": i + 1,
                                        "method": "first_ctx_after_anchor", "add_lines": len(add_lines)
                                    })
                                    break

            if not found:
                hunk_results.append({
                    "status": "no_match",
                    "del_lines": len(del_lines),
                    "add_lines": len(add_lines),
                    "ctx_before_lines": len(ctx_before),
                    "ctx_after_lines": len(ctx_after),
                    "first_del_preview": del_lines[0][:60] if del_lines else "",
                    "first_ctx_preview": (ctx_before[0][:60] if ctx_before else ""),
                })

        if modified:
            write_file(abs_path, "\n".join(file_lines))

        results.append({"path": file_path, "applied": modified, "hunks": hunk_results})

    return results


if __name__ == "__main__":
    if len(sys.argv) > 1:
        patch_text = Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        patch_text = Path("/tmp/raw_patch.diff").read_text(encoding="utf-8")
    results = normalize(patch_text)
    print(json.dumps(results, indent=2))
