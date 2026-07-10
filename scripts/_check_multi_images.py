"""Check Multi dataset instance info from HF cache."""
from datasets import load_dataset

ds = load_dataset("ByteDance-Seed/Multi-SWE-bench", split="train", streaming=True)

targets = [
    "BurntSushi__ripgrep-1367", "alibaba__fastjson2-2559", "axios__axios-5661",
    "catchorg__Catch2-1608", "clap-rs__clap-2501", "cli__cli-3270",
    "darkreader__darkreader-7241", "elastic__logstash-14027",
    "expressjs__express-3870", "facebook__zstd-2094", "fmtlib__fmt-1663",
    "grpc__grpc-go-2996", "jqlang__jq-2654", "mui__material-ui-34337",
    "nlohmann__json-2225", "ponylang__ponyc-2532"
]

found = {}
for ex in ds:
    iid = ex["instance_id"]
    if iid in targets:
        repo = ex.get("repo", "")
        img = ex.get("image_name", "")
        f2p = len(ex.get("FAIL_TO_PASS", []))
        p2p = len(ex.get("PASS_TO_PASS", []))
        tp = bool(ex.get("test_patch", ""))
        found[iid] = {"repo": repo, "image_name": img, "f2p": f2p, "p2p": p2p, "tp": tp}
        if len(found) == len(targets):
            break

for iid in targets:
    info = found.get(iid)
    if not info:
        print(f"{iid:50s} NOT FOUND")
        continue
    img = info["image_name"]
    img_short = img[:65] if len(img) > 65 else img
    repo = info["repo"]
    print(f"{iid:50s} repo={repo:30s}")
    print(f"{'':50s} img={img_short}")
    print(f"{'':50s} f2p={info['f2p']} p2p={info['p2p']} test_patch={info['tp']}")

# Check docker images
import subprocess
r = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                   capture_output=True, text=True, timeout=30)
all_images = set(r.stdout.strip().split("\n"))
print("\n=== Image availability check ===")
for iid in targets:
    info = found.get(iid)
    if not info:
        print(f"{iid:50s} NO DATASET ENTRY")
        continue
    img_name = info["image_name"]
    if img_name and img_name in all_images:
        print(f"{iid:50s} HAS IMAGE: {img_name}")
    else:
        id_docker = iid.replace("__", "_1776_")
        found_img = False
        for ai in all_images:
            if id_docker.lower() in ai.lower():
                print(f"{iid:50s} FOUND VIA: {ai}")
                found_img = True
                break
        if not found_img:
            print(f"{iid:50s} NO IMAGE FOUND (img_name={img_name})")
