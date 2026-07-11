"""Search broadly for any context_packet v2-alpha / api_navigation / condiag_packet_only artifacts
for django-11820 and django-13513. Preflight follow-up."""
import os
from pathlib import Path

ARTIFACTS_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0")
CASES = ["django__django-11820", "django__django-13513"]

print("=== Broad search for packet/api/condiag artifacts ===")
for iid in CASES:
    print(f"\n--- {iid} ---")
    # Find any context_packet.md under artifacts root with this instance_id
    for root, dirs, files in os.walk(ARTIFACTS_ROOT):
        for f in files:
            if f == "context_packet.md" and iid in root:
                full = Path(root) / f
                try:
                    st = full.stat()
                    print(f"  packet: {full}  ({st.st_size} bytes)")
                except Exception:
                    pass
        # don't recurse into huge dirs
        if "/cache/" in root or "node_modules" in root:
            dirs[:] = []

    # Find any api_navigation.json
    for root, dirs, files in os.walk(ARTIFACTS_ROOT):
        for f in files:
            if "api_nav" in f.lower() and f.endswith(".json") and iid in root:
                full = Path(root) / f
                print(f"  api_nav: {full}  ({full.stat().st_size} bytes)")
        if "/cache/" in root:
            dirs[:] = []

    # Find any condiag_packet_only dir
    for root, dirs, files in os.walk(ARTIFACTS_ROOT):
        for d in dirs:
            if "condiag" in d.lower() and d != "condiag":
                full = Path(root) / d
                if iid in str(full) or (full / iid).exists():
                    print(f"  condiag_dir: {full}")
        if "/cache/" in root:
            dirs[:] = []

print("\n=== Also check ~/condiag codebase for packet builder outputs ===")
CONDIAG_HOME = Path("/home/swelite/condiag")
for root, dirs, files in os.walk(CONDIAG_HOME):
    if "/.git" in root or "/.venv" in root or "node_modules" in root:
        dirs[:] = []
        continue
    for f in files:
        if f == "context_packet.md" and any(iid in root for iid in CASES):
            full = Path(root) / f
            try:
                print(f"  packet: {full}  ({full.stat().st_size} bytes)")
            except Exception:
                pass
