#!/usr/bin/env python3
"""Probe ZAI/ZhipuAI endpoints with multiple models to find what works."""
import os
import sys
import json
import urllib.request
import urllib.error

KEY = None
for path in (os.path.expanduser("~/.config/mini-swe-agent/.env"),):
    if os.path.exists(path):
        for line in open(path):
            if line.strip().startswith("ZAI_API_KEY="):
                KEY = line.strip().split("=", 1)[1]
                break

if not KEY:
    print("ERROR: no ZAI_API_KEY in ~/.config/mini-swe-agent/.env")
    sys.exit(1)

print(f"loaded key (len={len(KEY)}, prefix={KEY[:8]}...)")
print()

ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODELS = ["glm-4.6", "glm-4.5", "glm-4.5-air", "glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4-flashx", "glm-4-long", "glm-4v", "glm-4v-flash"]

for model in MODELS:
    body_bytes = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "say ok"}],
        "temperature": 0,
        "max_tokens": 5,
    }).encode()

    req = urllib.request.Request(
        ENDPOINT,
        data=body_bytes,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    print(f"--- {model} ---")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")[:80]
            print(f"  OK: {content!r}")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:200]
        print(f"  HTTP {e.code}: {err_body}")
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
