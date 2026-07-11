"""Lightweight repository index for ConDiag retrieval executor v0.

Indexes built over repo@base_commit (never patched tree):
  - file_index:    all .py files (path, size, line_count)
  - test_index:    test functions in tests/**/test_*.py
  - symbol_index:  classes + class methods via Python ast
  - lexical_index: on-demand ripgrep / Python regex fallback

Python ast handles Python sources; non-Python falls back to line search.
Will swap to tree-sitter for cross-language support later.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass
class Symbol:
    path: str
    kind: str          # class | function | async_function
    name: str          # Symbol | Symbol._eval_is_finite
    start_line: int
    end_line: int
    parent: Optional[str] = None


@dataclass
class TestFn:
    path: str
    name: str
    start_line: int
    end_line: int


@dataclass
class FileEntry:
    path: str
    line_count: int
    size_bytes: int


@dataclass
class RepositoryIndex:
    repo_root: str
    file_index: List[FileEntry] = field(default_factory=list)
    test_index: List[TestFn] = field(default_factory=list)
    symbol_index: List[Symbol] = field(default_factory=list)
    index_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "repo_root": self.repo_root,
            "file_index": [asdict(f) for f in self.file_index],
            "test_index": [asdict(t) for t in self.test_index],
            "symbol_index": [asdict(s) for s in self.symbol_index],
            "index_summary": self.index_summary,
        }


_BAD_SEGMENTS = {".git", "node_modules", "__pycache__", ".tox", ".eggs", "build", "dist"}


def _skip_segments(parts) -> bool:
    for p in parts:
        if p in _BAD_SEGMENTS:
            return True
        if p.endswith(".egg-info"):
            return True
    return False


def _walk_py_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.py"):
        if _skip_segments(p.parts):
            continue
        yield p


def _extract_symbols(py_path_str: str, source: str) -> List[Symbol]:
    out: List[Symbol] = []
    try:
        tree = ast.parse(source, filename=py_path_str)
    except SyntaxError:
        return out

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            out.append(Symbol(
                path=py_path_str, kind="class", name=node.name,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                parent=None,
            ))
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    out.append(Symbol(
                        path=py_path_str, kind="function",
                        name=node.name + "." + child.name,
                        start_line=child.lineno,
                        end_line=getattr(child, "end_lineno", child.lineno) or child.lineno,
                        parent=node.name,
                    ))
                elif isinstance(child, ast.AsyncFunctionDef):
                    out.append(Symbol(
                        path=py_path_str, kind="async_function",
                        name=node.name + "." + child.name,
                        start_line=child.lineno,
                        end_line=getattr(child, "end_lineno", child.lineno) or child.lineno,
                        parent=node.name,
                    ))
    seen: dict = {}
    for s in out:
        key = (s.path, s.name, s.start_line)
        if key not in seen or s.end_line > seen[key].end_line:
            seen[key] = s
    return list(seen.values())


def _extract_tests(py_path_str: str, source: str) -> List[TestFn]:
    out: List[TestFn] = []
    try:
        tree = ast.parse(source, filename=py_path_str)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                out.append(TestFn(
                    path=py_path_str, name=node.name,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                ))
    return out


def build_index(repo_root: Path) -> RepositoryIndex:
    repo_root = Path(repo_root).resolve()
    idx = RepositoryIndex(repo_root=str(repo_root))
    n_files = 0
    n_lines_total = 0

    for py in _walk_py_files(repo_root):
        try:
            source = py.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(py.relative_to(repo_root))
        line_count = source.count("\n") + 1
        idx.file_index.append(FileEntry(
            path=rel, line_count=line_count, size_bytes=len(source.encode("utf-8")),
        ))
        n_files += 1
        n_lines_total += line_count

        idx.symbol_index.extend(_extract_symbols(rel, source))
        is_test_file = py.name.startswith("test_") or any(seg == "tests" for seg in py.parts)
        if is_test_file:
            idx.test_index.extend(_extract_tests(rel, source))

    idx.index_summary = {
        "py_file_count": n_files,
        "total_py_line_count": n_lines_total,
        "symbol_count": len(idx.symbol_index),
        "class_count": sum(1 for s in idx.symbol_index if s.kind == "class"),
        "function_count": sum(1 for s in idx.symbol_index if s.kind != "class"),
        "test_count": len(idx.test_index),
        "repo_root": str(repo_root),
    }
    return idx


def write_summary(out_dir: Path, idx: RepositoryIndex) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "repository_index_summary.json"
    p.write_text(json.dumps(idx.index_summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def find_symbol(idx: RepositoryIndex, name_query: str) -> List[Symbol]:
    q = name_query.strip()
    exact = [s for s in idx.symbol_index if s.name == q]
    if exact:
        return exact
    return [s for s in idx.symbol_index if q in s.name]


def find_tests(idx: RepositoryIndex, name_query: str) -> List[TestFn]:
    q = name_query.strip()
    exact = [t for t in idx.test_index if t.name == q]
    if exact:
        return exact
    return [t for t in idx.test_index if q in t.name]


def read_span(repo_root: Path, path: str, start: int, end: int, context: int = 0) -> str:
    p = Path(repo_root) / path
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return ""
    lo = max(1, start - context)
    hi = min(len(lines), end + context)
    return "\n".join(lines[lo - 1: hi])


def rg_search(repo_root: Path, pattern: str, max_hits: int = 50) -> List[dict]:
    """Run ripgrep if available; fall back to Python re walk."""
    try:
        result = subprocess.run(
            ["rg", "--no-heading", "-n", "--type", "py", pattern, str(repo_root)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            hits = []
            for line in result.stdout.splitlines()[:max_hits]:
                m = re.match(r"^(.+?):(\d+):(.*)$", line)
                if m:
                    try:
                        rel = str(Path(m.group(1)).relative_to(repo_root))
                    except ValueError:
                        rel = m.group(1)
                    hits.append({"path": rel, "line": int(m.group(2)), "content": m.group(3)})
            return hits
    except FileNotFoundError:
        pass

    hits = []
    rx = re.compile(pattern)
    for py in _walk_py_files(Path(repo_root)):
        try:
            rel = str(py.relative_to(repo_root))
        except ValueError:
            continue
        try:
            for i, line in enumerate(py.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append({"path": rel, "line": i, "content": line})
                    if len(hits) >= max_hits:
                        return hits
        except Exception:
            continue
    return hits
