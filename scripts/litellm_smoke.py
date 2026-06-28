#!/usr/bin/env python3
"""Quick litellm smoke test through the bigmodel.cn Anthropic-compatible proxy."""
import os
import sys

import litellm

# litellm 1.89+ may print a banner; turn it down.
litellm.suppress_debug_info = True

print(f"ANTHROPIC_API_BASE = {os.environ.get('ANTHROPIC_API_BASE', '<unset>')}")
print(f"ANTHROPIC_API_KEY set = {'yes' if os.environ.get('ANTHROPIC_API_KEY') else 'no'}")
print()

for model in ["anthropic/glm-4.6", "anthropic/glm-5-turbo"]:
    print(f"--- litellm.completion(model={model!r}) ---")
    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "say ok"}],
            max_tokens=10,
            temperature=0.0,
            drop_params=True,
        )
        print(f"  OK: {resp.choices[0].message.content!r}")
        print(f"  usage: {resp.usage}")
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {str(e)[:300]}")
