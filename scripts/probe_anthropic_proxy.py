#!/usr/bin/env python3
"""Test if bigmodel.cn's Anthropic-compatible endpoint works with the user's key."""
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
    print("ERROR: no ZAI_API_KEY")
    sys.exit(1)

# Try the Anthropic-compatible proxy.
URL = "https://open.bigmodel.cn/api/anthropic/v1/messages"

# Test with both auth schemes and a couple model names.
TESTS = [
    ("Authorization Bearer + glm-5", {"Authorization": f"Bearer {KEY}"}),
    ("x-api-key + glm-5",            {"x-api-key": KEY}),
]

for label, extra_headers in TESTS:
    for model in ["glm-5", "glm-5-turbo", "glm-4.6", "glm-4-flash"]:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "say ok"}],
            "max_tokens": 10,
        }).encode()

        headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
        headers.update(extra_headers)

        req = urllib.request.Request(URL, data=body, headers=headers, method="POST")
        print(f"[{label}] model={model}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
                if "content" in data:
                    txt = data["content"][0].get("text", "")[:80] if data["content"] else ""
                    print(f"  OK: {txt!r} | usage={data.get('usage', {})}")
                else:
                    print(f"  OK (no content): {json.dumps(data)[:200]}")
        except urllib.error.HTTPError as e:
            err = e.read().decode()[:300]
            print(f"  HTTP {e.code}: {err}")
        except Exception as e:
            print(f"  FAIL: {type(e).__name__}: {e}")
