#!/bin/bash
set -e
SRC=/mnt/d/condiag-artifacts/cache/repos/github.com__django__django

declare -A COMMITS
COMMITS[django__django-10880]=838e432e3e5519c5383d12018e6c78f8ec7833c1
COMMITS[django__django-11099]=d26b2424437dabeeca94d7900b37d2df4410da0c

for iid in "${!COMMITS[@]}"; do
    commit="${COMMITS[$iid]}"
    DST="/home/swelite/condiag/workspaces/$iid/repo_base"
    mkdir -p "$(dirname "$DST")"

    # Clean half-written clone if present
    if [ -d "$DST" ] && [ ! -d "$DST/.git" ]; then
        rm -rf "$DST"
    fi

    if [ -d "$DST/.git" ]; then
        cur=$(cd "$DST" && git rev-parse HEAD 2>/dev/null || echo "")
        if [ "$cur" = "$commit" ]; then
            echo "exists+correct: $DST @ ${commit:0:8}"
            continue
        fi
        cd "$DST" && git checkout -q "$commit"
        echo "checked-out: $DST @ ${commit:0:8}"
    else
        # Use git worktree add — shares object store with cache, much faster than clone
        cd "$SRC" && git worktree add --detach "$DST" "$commit" >/dev/null 2>&1
        echo "worktree: $DST @ ${commit:0:8}"
    fi
done
