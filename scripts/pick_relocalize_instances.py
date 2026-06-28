"""Pick RELOCALIZE-friendly instances from ContextBench Verified.

This is a deliberately simple keyword + repo scorer. It is NOT a diagnoser —
it only surfaces instances whose issue text and repo suggest a higher prior
on wrong-file-localization failures. The final RELOCALIZE seed decision is
still made by a human reviewing the post-mini-SWE miner output.

Two-pass selection strategy (per user spec, 2026-06-27 EOD):
    1. Score all Python instances by repo + keyword - penalty.
    2. Cross-reference with local Docker images.
    3. Output:
       - top 30 overall pool (regardless of local presence)
       - local candidates (has_local_image=True, sorted by score)
       - selected_local: top N local, recommended to run mini-SWE first
       - pull_candidates: top N external, recommended to pull only if
         local quality is insufficient
       - summary.md: human-readable breakdown

Scoring (per user spec, see project memory):
    repo:
        django/django                +5
        scikit-learn/scikit-learn    +3
        sympy/sympy                  +2
        astropy/astropy              +2
        other python repo            +1
    keywords (matched against problem_statement):
        E\d+ / W\d+ / models.E\d+ / fields.E\d+       +6
        SystemCheckError / system check / check framework   +5
        validation / ValidationError                          +4
        ImproperlyConfigured / configuration / settings      +4
        warning / deprecation                                 +3
        router / backend / database / collision               +3
        exception / traceback                                 +3
        registry / middleware / dispatch                      +2
    penalty:
        docstring / typo / documentation / comment            -4
        format / style / cleanup / refactor                   -4
        rename / trivial wording                              -3
    exclude:
        non-python language
        already a seed case (4 recovery + 1 noop)
        non-standard ContextBench-internal ids (no docker image)

Usage (after `docker images > /mnt/d/condiag-artifacts/docker/local_images.txt`):
    python3 scripts/pick_relocalize_instances.py \\
        --parquet /home/swelite/condiag/ContextBench/data/contextbench_verified.parquet \\
        --local-images /mnt/d/condiag-artifacts/docker/local_images.txt \\
        --out-pool /mnt/d/condiag-artifacts/condiag/v0/relocalize_instance_pool.csv \\
        --out-local /mnt/d/condiag-artifacts/condiag/v0/relocalize_local_candidates.csv \\
        --out-selected-local /mnt/d/condiag-artifacts/condiag/v0/relocalize_selected_local.txt \\
        --out-pull /mnt/d/condiag-artifacts/condiag/v0/relocalize_pull_candidates.txt \\
        --out-md /mnt/d/condiag-artifacts/condiag/v0/relocalize_selected_summary.md
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


SEED_CASES = {
    "sympy__sympy-16597",
    "sympy__sympy-13877",
    "astropy__astropy-13398",
    "django__django-11400",
    "django__django-13195",
}

# Standard swebench-style id: <repo>__<repo>-<digits>
# Non-standard ids (with long git-hash suffixes) are ContextBench-internal-only
# and don't have docker images for mini-SWE — exclude them upfront.
SWEBENCH_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.\-]+__[a-zA-Z0-9_.\-]+-\d+$")

REPO_SCORE = {
    "django/django": 5,
    "scikit-learn/scikit-learn": 3,
    "sympy/sympy": 2,
    "astropy/astropy": 2,
}


# (pattern, weight, label) — patterns matched case-insensitive against
# the problem_statement. Order matters only for the markdown legend.
KEYWORD_PATTERNS: list[tuple[re.Pattern, int, str]] = [
    # Django-style error codes — very strong RELOCALIZE signal
    (re.compile(r"\b(?:models|fields|checks)\.[EW]\d+\b", re.IGNORECASE), 6, "framework error code (models.E###)"),
    (re.compile(r"\b[EW]\d{3,4}\b"), 6, "bare error code (E### / W###)"),
    # Django system check framework
    (re.compile(r"\bSystemCheckError\b", re.IGNORECASE), 5, "SystemCheckError"),
    (re.compile(r"\bsystem check\b", re.IGNORECASE), 5, "system check"),
    (re.compile(r"\bcheck framework\b", re.IGNORECASE), 5, "check framework"),
    # Validation
    (re.compile(r"\bValidationError\b"), 4, "ValidationError"),
    (re.compile(r"\bvalidation\b", re.IGNORECASE), 4, "validation"),
    # Configuration
    (re.compile(r"\bImproperlyConfigured\b"), 4, "ImproperlyConfigured"),
    (re.compile(r"\bconfiguration\b", re.IGNORECASE), 4, "configuration"),
    (re.compile(r"\bsettings\b", re.IGNORECASE), 4, "settings"),
    # Warnings / deprecations
    (re.compile(r"\bwarning\b", re.IGNORECASE), 3, "warning"),
    (re.compile(r"\bdeprecation\b", re.IGNORECASE), 3, "deprecation"),
    # Infra
    (re.compile(r"\brouter\b", re.IGNORECASE), 3, "router"),
    (re.compile(r"\bbackend\b", re.IGNORECASE), 3, "backend"),
    (re.compile(r"\bdatabase\b", re.IGNORECASE), 3, "database"),
    (re.compile(r"\bcollision\b", re.IGNORECASE), 3, "collision"),
    # Exceptions
    (re.compile(r"\bexception\b", re.IGNORECASE), 3, "exception"),
    (re.compile(r"\btraceback\b", re.IGNORECASE), 3, "traceback"),
    # Lower-priority infra
    (re.compile(r"\bregistry\b", re.IGNORECASE), 2, "registry"),
    (re.compile(r"\bmiddleware\b", re.IGNORECASE), 2, "middleware"),
    (re.compile(r"\bdispatch\b", re.IGNORECASE), 2, "dispatch"),
]

PENALTY_PATTERNS: list[tuple[re.Pattern, int, str]] = [
    (re.compile(r"\bdocstring\b", re.IGNORECASE), -4, "docstring"),
    (re.compile(r"\btypo\b", re.IGNORECASE), -4, "typo"),
    (re.compile(r"\bdocumentation\b", re.IGNORECASE), -4, "documentation"),
    (re.compile(r"\bcomment\b", re.IGNORECASE), -4, "comment"),
    (re.compile(r"\bformat\b", re.IGNORECASE), -4, "format"),
    (re.compile(r"\bstyle\b", re.IGNORECASE), -4, "style"),
    (re.compile(r"\bcleanup\b", re.IGNORECASE), -4, "cleanup"),
    (re.compile(r"\brefactor\b", re.IGNORECASE), -4, "refactor"),
    (re.compile(r"\brename\b", re.IGNORECASE), -3, "rename"),
]


@dataclass
class ScoredInstance:
    instance_id: str  # original_inst_id (ConDiag format)
    cb_id: str        # ContextBench internal instance_id
    repo: str
    repo_score: int
    keyword_hits: list[tuple[str, int]] = field(default_factory=list)
    keyword_score: int = 0
    penalty_hits: list[tuple[str, int]] = field(default_factory=list)
    penalty_score: int = 0
    final_score: int = 0
    problem_statement_excerpt: str = ""
    has_local_image: bool = False
    matched_image: str = ""

    def to_row(self) -> dict:
        return {
            "instance": self.instance_id,
            "cb_id": self.cb_id,
            "repo": self.repo,
            "repo_score": self.repo_score,
            "keyword_score": self.keyword_score,
            "keyword_hits": "; ".join(f"{lbl}({w:+d})" for lbl, w in self.keyword_hits),
            "penalty_score": self.penalty_score,
            "penalty_hits": "; ".join(f"{lbl}({w:+d})" for lbl, w in self.penalty_hits),
            "final_score": self.final_score,
            "has_local_image": self.has_local_image,
            "matched_image": self.matched_image,
            "problem_statement_excerpt": self.problem_statement_excerpt,
        }


def load_local_images(path: Path) -> list[str]:
    """Read a `docker images --format '{{.Repository}}:{{.Tag}}' dump.
    Lines may be blank or commented; ignore those."""
    if not path.is_file():
        return []
    out: list[str] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def match_local_image(instance_id: str, local_images: list[str]) -> str:
    """Return the first matching local image tag, or "" if no match.

    Tries three patterns:
      1. Direct substring (Poly / Pro: "django__django-11630" appears in
         ghcr.io/timesler/swe-polybench.eval.x86_64.django__django-11630:latest)
      2. swebench Verified convention: instance_id "django__django-12858"
         -> image contains "sweb.eval.x86_64.django_1776_django-12858"
      3. fallback: <num_part> alone (e.g. "django-12858") — used only as a
         tiebreaker, requires the entire token to appear in the image
    """
    if not local_images:
        return ""

    inst_lower = instance_id.lower()
    parts = instance_id.split("__")
    swebench_needle: str | None = None
    num_part: str | None = None
    if len(parts) == 2:
        repo_part, num_part = parts[0], parts[1]
        repo_underscore = repo_part.replace("-", "_").lower()
        swebench_needle = f"sweb.eval.x86_64.{repo_underscore}_1776_{num_part.lower()}"
        num_part = num_part.lower()

    for img in local_images:
        img_l = img.lower()
        if inst_lower in img_l:
            return img
        if swebench_needle and swebench_needle in img_l:
            return img
    # Last resort: bare num_part match, but require word boundary to avoid
    # false positives (e.g. "django-1" matching "django-10000")
    if num_part:
        for img in local_images:
            # num_part already contains a hyphen (e.g. "django-12858"), so
            # substring match is reasonably specific. Still require both
            # the org and num to co-occur for safety.
            if num_part in img.lower():
                # double-check the org also appears (avoid pure coincidence)
                org_part = parts[0].lower() if len(parts) == 2 else ""
                if org_part and org_part in img.lower():
                    return img
    return ""


def score_one(row) -> ScoredInstance | None:
    """Score a single parquet row. Returns None if it should be excluded."""
    if str(row.get("language") or "").lower() != "python":
        return None
    inst = str(row.get("original_inst_id") or "").strip()
    if not inst or inst in SEED_CASES:
        return None
    if not SWEBENCH_ID_PATTERN.match(inst):
        return None

    repo = str(row.get("repo") or "").strip()
    repo_score = REPO_SCORE.get(repo, 1)

    text = str(row.get("problem_statement") or "")

    keyword_hits: list[tuple[str, int]] = []
    keyword_score = 0
    for pat, weight, label in KEYWORD_PATTERNS:
        if pat.search(text):
            keyword_hits.append((label, weight))
            keyword_score += weight

    penalty_hits: list[tuple[str, int]] = []
    penalty_score = 0
    for pat, weight, label in PENALTY_PATTERNS:
        if pat.search(text):
            penalty_hits.append((label, weight))
            penalty_score += weight

    final = repo_score + keyword_score + penalty_score

    return ScoredInstance(
        instance_id=inst,
        cb_id=str(row.get("instance_id") or ""),
        repo=repo,
        repo_score=repo_score,
        keyword_hits=keyword_hits,
        keyword_score=keyword_score,
        penalty_hits=penalty_hits,
        penalty_score=penalty_score,
        final_score=final,
        problem_statement_excerpt=text[:300].replace("\n", " ").strip(),
    )


def _balance_by_repo(scored: list[ScoredInstance], n_total: int) -> list[ScoredInstance]:
    """Apply the 8 Django + 4 priority-other + spare non-Django balance.

    Same logic as the previous select_top(), but parameterized so we can
    reuse it for both local-only and pull-only selections.
    """
    sorted_all = sorted(scored, key=lambda s: s.final_score, reverse=True)

    selected: list[ScoredInstance] = []
    seen: set[str] = set()

    def _count_repo(repo: str) -> int:
        return sum(1 for x in selected if x.repo == repo)

    # Phase 1: 8 Django
    django_cap = min(8, n_total)
    for s in sorted_all:
        if _count_repo("django/django") >= django_cap:
            break
        if s.repo != "django/django":
            continue
        if s.instance_id in seen:
            continue
        selected.append(s)
        seen.add(s.instance_id)
        if len(selected) >= n_total:
            break

    # Phase 2: 4 from scikit-learn / sympy / astropy (if room)
    other_priority = {
        "scikit-learn/scikit-learn",
        "sympy/sympy",
        "astropy/astropy",
    }
    other_cap = min(4, max(0, n_total - django_cap))
    taken_other = 0
    for s in sorted_all:
        if taken_other >= other_cap:
            break
        if s.repo not in other_priority:
            continue
        if s.instance_id in seen:
            continue
        selected.append(s)
        seen.add(s.instance_id)
        taken_other += 1
        if len(selected) >= n_total:
            break

    # Phase 3: fill up to n_total from any repo EXCEPT django/django
    for s in sorted_all:
        if len(selected) >= n_total:
            break
        if s.instance_id in seen:
            continue
        if s.repo == "django/django":
            continue
        selected.append(s)
        seen.add(s.instance_id)

    return sorted(selected, key=lambda s: s.final_score, reverse=True)


def render_summary_md(
    selected_local: list[ScoredInstance],
    selected_pull: list[ScoredInstance],
    n_total_scored: int,
    n_local_total: int,
) -> str:
    lines = [
        "# RELOCALIZE candidate instances — local-intersect summary",
        "",
        "Picked by `scripts/pick_relocalize_instances.py`. "
        "Score = repo + keyword - penalty.",
        "",
        f"- Scored Python instances: **{n_total_scored}**",
        f"- With local Docker image: **{n_local_total}**",
        f"- Selected for immediate mini-SWE (local): **{len(selected_local)}**",
        f"- Selected for pull-if-needed (external): **{len(selected_pull)}**",
        "",
        "Selection balance target per group: 8 Django + 4 priority-other + spare.",
        "",
        "## Phase 1 — local candidates (run mini-SWE first)",
        "",
        "These instances already have a Docker image locally. No `docker pull` needed.",
        "",
        "| # | Instance | Repo | Score | Local | Repo | Kw | Pen | Top keyword hits |",
        "|--:|----------|------|------:|:-----:|-----:|---:|----:|------------------|",
    ]
    for i, s in enumerate(selected_local, 1):
        kw_hits = ", ".join(lbl for lbl, _ in s.keyword_hits[:4]) or "—"
        lines.append(
            f"| {i} | `{s.instance_id}` | {s.repo} | {s.final_score} | yes | "
            f"{s.repo_score:+d} | {s.keyword_score:+d} | {s.penalty_score:+d} | {kw_hits} |"
        )

    lines.extend([
        "",
        "## Phase 2 — pull candidates (only if Phase 1 insufficient)",
        "",
        "These instances are NOT local. Pull top 3–5 only if Phase 1 doesn't yield a strong RELOCALIZE signal.",
        "",
        "| # | Instance | Repo | Score | Local | Repo | Kw | Pen | Top keyword hits |",
        "|--:|----------|------|------:|:-----:|-----:|---:|----:|------------------|",
    ])
    for i, s in enumerate(selected_pull, 1):
        kw_hits = ", ".join(lbl for lbl, _ in s.keyword_hits[:4]) or "—"
        lines.append(
            f"| {i} | `{s.instance_id}` | {s.repo} | {s.final_score} | no | "
            f"{s.repo_score:+d} | {s.keyword_score:+d} | {s.penalty_score:+d} | {kw_hits} |"
        )

    lines.append("")
    lines.append("## Problem statement excerpts")
    lines.append("")
    lines.append("### Phase 1 (local)")
    lines.append("")
    for s in selected_local:
        lines.append(f"#### `{s.instance_id}`  (score={s.final_score}, repo={s.repo})")
        lines.append("")
        lines.append("```")
        lines.append(s.problem_statement_excerpt)
        lines.append("```")
        lines.append("")
    if selected_pull:
        lines.append("### Phase 2 (pull)")
        lines.append("")
        for s in selected_pull:
            lines.append(f"#### `{s.instance_id}`  (score={s.final_score}, repo={s.repo})")
            lines.append("")
            lines.append("```")
            lines.append(s.problem_statement_excerpt)
            lines.append("```")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Pick RELOCALIZE candidate instances from ContextBench Verified")
    ap.add_argument(
        "--parquet",
        default="/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet",
    )
    ap.add_argument(
        "--local-images",
        default="/mnt/d/condiag-artifacts/docker/local_images.txt",
        help="Output of `docker images --format '{{.Repository}}:{{.Tag}}'`",
    )
    ap.add_argument("--out-pool", default="/mnt/d/condiag-artifacts/condiag/v0/relocalize_instance_pool.csv")
    ap.add_argument("--out-local", default="/mnt/d/condiag-artifacts/condiag/v0/relocalize_local_candidates.csv")
    ap.add_argument("--out-selected-local", default="/mnt/d/condiag-artifacts/condiag/v0/relocalize_selected_local.txt")
    ap.add_argument("--out-pull", default="/mnt/d/condiag-artifacts/condiag/v0/relocalize_pull_candidates.txt")
    ap.add_argument("--out-md", default="/mnt/d/condiag-artifacts/condiag/v0/relocalize_selected_summary.md")
    ap.add_argument("--pool-size", type=int, default=30)
    ap.add_argument("--local-size", type=int, default=8, help="Max instances to select from local candidates")
    ap.add_argument("--pull-size", type=int, default=5, help="Max instances to recommend for pull")
    args = ap.parse_args()

    parquet_path = Path(args.parquet)
    if not parquet_path.is_file():
        print(f"[ERR] parquet not found: {parquet_path}", file=sys.stderr)
        return 2

    # Pre-create output dirs
    for p in [args.out_pool, args.out_local, args.out_selected_local, args.out_pull, args.out_md]:
        Path(p).parent.mkdir(parents=True, exist_ok=True)

    local_images = load_local_images(Path(args.local_images))
    print(f"[pick] loaded {len(local_images)} local images from {args.local_images}")

    df = pd.read_parquet(parquet_path)
    print(f"[pick] loaded {len(df)} rows from {parquet_path}")

    scored: list[ScoredInstance] = []
    for _, row in df.iterrows():
        s = score_one(row)
        if s is not None:
            scored.append(s)
    print(f"[pick] {len(scored)} python instances after exclusions (seed case removed, non-python dropped)")

    # Tag local image presence
    n_local = 0
    for s in scored:
        match = match_local_image(s.instance_id, local_images)
        if match:
            s.has_local_image = True
            s.matched_image = match
            n_local += 1
    print(f"[pick] {n_local} of {len(scored)} candidates have a local Docker image")

    # Output 1: full top-N pool (regardless of local presence)
    sorted_scored = sorted(scored, key=lambda s: s.final_score, reverse=True)
    pool = sorted_scored[: args.pool_size]
    pool_csv = Path(args.out_pool)
    pd.DataFrame([s.to_row() for s in pool]).to_csv(pool_csv, index=False, encoding="utf-8")
    print(f"[pick] pool ({len(pool)} rows) -> {pool_csv}")

    # Output 2: all local candidates (sorted by score, no cap)
    local_all = sorted([s for s in scored if s.has_local_image], key=lambda s: s.final_score, reverse=True)
    local_csv = Path(args.out_local)
    pd.DataFrame([s.to_row() for s in local_all]).to_csv(local_csv, index=False, encoding="utf-8")
    print(f"[pick] local_candidates ({len(local_all)} rows) -> {local_csv}")

    # Output 3: selected local (8 Django + 4 priority-other + spare non-Django)
    selected_local = _balance_by_repo(local_all, n_total=args.local_size)
    selected_local_txt = Path(args.out_selected_local)
    selected_local_txt.write_text(
        "\n".join(s.instance_id for s in selected_local) + "\n", encoding="utf-8"
    )
    print(f"[pick] selected_local ({len(selected_local)}) -> {selected_local_txt}")

    # Output 4: pull candidates (top N external)
    remote_all = sorted([s for s in scored if not s.has_local_image], key=lambda s: s.final_score, reverse=True)
    selected_pull = _balance_by_repo(remote_all, n_total=args.pull_size)
    pull_txt = Path(args.out_pull)
    pull_txt.write_text(
        "\n".join(s.instance_id for s in selected_pull) + "\n", encoding="utf-8"
    )
    print(f"[pick] pull_candidates ({len(selected_pull)}) -> {pull_txt}")

    # Output 5: summary md
    md_path = Path(args.out_md)
    md_path.write_text(
        render_summary_md(selected_local, selected_pull, len(scored), len(local_all)),
        encoding="utf-8",
    )
    print(f"[pick] summary -> {md_path}")

    # Console summary
    by_repo_local: dict[str, int] = {}
    for s in selected_local:
        by_repo_local[s.repo] = by_repo_local.get(s.repo, 0) + 1
    by_repo_pull: dict[str, int] = {}
    for s in selected_pull:
        by_repo_pull[s.repo] = by_repo_pull.get(s.repo, 0) + 1
    print(f"[pick] selected_local by repo: {by_repo_local}")
    print(f"[pick] pull_candidates by repo: {by_repo_pull}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
