#!/usr/bin/env python3
"""Generate legacy inventory of all code, artifacts, and Docker images."""
import json, os, hashlib, subprocess, csv, sys
from pathlib import Path

PROJECT_HOME = Path("/home/swelite/condiag")
ARTIFACT_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0")
OUTPUT_DIR = Path("/mnt/d/condiag-artifacts/condiag/manifests")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return "ERROR"

def collect_files(root, category):
    entries = []
    root = Path(root)
    if not root.exists():
        return entries
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if p.name.startswith("."):
            continue
        if "__pycache__" in str(p):
            continue
        rel = str(p.relative_to(root))
        try:
            sz = p.stat().st_size
        except OSError:
            sz = -1
        entries.append({
            "path": str(p),
            "relative_path": rel,
            "category": category,
            "size": sz,
            "ext": p.suffix,
            "hash_sha256_prefix": sha256(p),
        })
    return entries

print("Scanning WSL project code...", flush=True)
code_files = collect_files(PROJECT_HOME, "code")
code_files = [f for f in code_files if ".git" not in f["relative_path"]]
print(f"  {len(code_files)} files", flush=True)

print("Scanning artifacts...", flush=True)
artifact_files = collect_files(ARTIFACT_ROOT, "artifact")
print(f"  {len(artifact_files)} files", flush=True)

print("Scanning Docker images...", flush=True)
r = subprocess.run(
    ["docker", "images", "--format", "{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}"],
    capture_output=True, text=True, timeout=30
)
docker_entries = []
for line in r.stdout.strip().split("\n"):
    parts = line.split("\t")
    if len(parts) >= 5:
        docker_entries.append({
            "repository": parts[0],
            "tag": parts[1],
            "image_id": parts[2],
            "size": parts[3],
            "created": parts[4],
        })
    elif line.strip():
        docker_entries.append({"raw": line})
print(f"  {len(docker_entries)} images", flush=True)

inventory = {
    "metadata": {
        "generated_at": subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True).stdout.strip(),
        "project_home": str(PROJECT_HOME),
        "artifact_root": str(ARTIFACT_ROOT),
    },
    "code_files": code_files,
    "artifact_files": artifact_files,
    "docker_images": docker_entries,
}

json_path = OUTPUT_DIR / "legacy_inventory.json"
with open(json_path, "w") as f:
    json.dump(inventory, f, indent=1)
print(f"Wrote {json_path}", flush=True)

csv_path = OUTPUT_DIR / "legacy_inventory.csv"
with open(csv_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["category", "relative_path", "size", "ext", "hash_sha256_prefix", "full_path"])
    for entry in code_files + artifact_files:
        w.writerow([
            entry["category"], entry["relative_path"], entry["size"],
            entry["ext"], entry["hash_sha256_prefix"], entry["path"],
        ])
print(f"Wrote {csv_path}", flush=True)

docker_csv = OUTPUT_DIR / "legacy_docker_images.csv"
with open(docker_csv, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["repository", "tag", "image_id", "size", "created"])
    for img in docker_entries:
        w.writerow([img.get("repository",""), img.get("tag",""), img.get("image_id",""), img.get("size",""), img.get("created","")])
print(f"Wrote {docker_csv}", flush=True)

total_size = sum(e["size"] for e in code_files + artifact_files if e["size"] > 0)
n_py = sum(1 for e in code_files if e["ext"] == ".py")
n_json = sum(1 for e in code_files + artifact_files if e["ext"] == ".json")
print(f"\n=== Summary ===", flush=True)
print(f"  WSL code files: {len(code_files)} ({n_py} .py)", flush=True)
print(f"  Artifact files: {len(artifact_files)} ({n_json} .json)", flush=True)
print(f"  Docker images:  {len(docker_entries)}", flush=True)
print(f"  Total size:     {total_size / 1024 / 1024:.1f} MB (files only)", flush=True)
