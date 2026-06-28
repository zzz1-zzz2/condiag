# Figures

Architecture figures for the ConDiag architecture draft.

| File | Used in | Description |
|---|---|---|
| `fig1_pipeline.png` | §3.1 | ConDiag position in the Host Agent repair pipeline |
| `fig2_5R_pathology.png` | §3.2 | 5R framework × 7-class pathology taxonomy + seed-case coverage |
| `fig3_v0_modules.png` | §3.5 | v0 module architecture (22 modules, 5 groups) |
| `fig4_recovery_flows.png` | §4.1 | 4 manual-seed recovery flows + 1 NO-OP baseline (5/5 PASS) |
| `fig5_pilot50_eval.png` | §5 | Pilot50 5-stage evaluation pipeline + leakage isolation |

## Source

Each PNG is rendered from its `.mmd` (mermaid) source via the
`mermaid.ink` rendering service. To re-render after editing the source:

```bash
cd ~/condiag/docs/figures
python3 /tmp/render_mermaid.py    # or any equivalent base64 + URL fetch script
```

The render script:

```python
import base64, urllib.request
from pathlib import Path

figs_dir = Path('.')
for mmd in sorted(figs_dir.glob('*.mmd')):
    encoded = base64.urlsafe_b64encode(mmd.read_bytes()).decode('ascii')
    url = f'https://mermaid.ink/img/{encoded}?type=png&width=2400&bgColor=white'
    out = mmd.with_suffix('.png')
    out.write_bytes(urllib.request.urlopen(url, timeout=60).read())
    print(out.name)
```

## Why not `mmdc`?

Local `mmdc` rendering was attempted but failed: the Windows-shipped
`node.exe` resolves modules via UNC paths that get mangled when invoked
through WSL bash. `mermaid.ink` produces equivalent output and requires no
local Node toolchain.
