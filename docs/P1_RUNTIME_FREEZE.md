# P1 Runtime Freeze

> Declared: 2026-07-23
> Runtime-tested baseline: c49a27f
> Freeze record commit: 2a05cf8
> Post-freeze fixes: ... (see git log)
> Canary: astropy__astropy-13398 (SWE-bench Verified)

## Freeze Scope

```
P0 protocol: CODE + RUNTIME FREEZE
P1-1 Bundle + Diagnoser: CODE + RUNTIME FREEZE
P1-2 Signal extraction / compression / branch repair: CODE + RUNTIME FREEZE
```

## Fairness Invariant

```text
Blocking gate:   tracked code-state equality (tracked_diff_sha)
Audit metadata:  untracked manifest + restore status (recorded, not blocking)
```

## Canary Evidence

| Checkpoint | Instance | Status |
|---|---|---|
| R1 submission | astropy__astropy-13398 | submitted (51 calls) |
| Patch Integrity | — | valid |
| Official Harness | — | UNRESOLVED |
| FailureWitness | — | 3 tests captured |
| FailureFeatureBundle | — | built |
| Diagnosis | — | API_DEFINITION (conf=high) |
| Compression | — | 86% reduction |
| SF Restore | — | passed (tracked SHA match) |
| SF R2 submission | — | submitted (64 calls) |
| CD Restore | — | passed (tracked SHA match) |
| CD R2 submission | — | submitted (51 calls) |
| SF Harness | — | UNRESOLVED (10 failures) |
| CD Harness | — | UNRESOLVED (10 failures) |
| Fairness (tracked) | — | r1_vs_sf=true r1_vs_cd=true |
| Verdict | — | both_fail |

## Local Tests

```text
185 passed, 4 skipped, 0 failures
(1 pre-existing: test_execution_policy — missing frozen pool file)
```

## Artifacts Saved

```
run_manifest.json
comparison.json
round1/integrity_report.json
round1/harness_eval.json
round1/failure_witness.json
round1/failure_feature_bundle.json
round1/workspace_snapshot.json
sf/integrity_report.json
sf/harness_eval.json
sf/trajectory.json
cd/diagnosis.json
cd/integrity_report.json
cd/harness_eval.json
cd/trajectory.json
fairness_debug/sf/*     (diffs, SHAs, files, untracked)
fairness_debug/cd/*     (diffs, SHAs, files, untracked)
```

## Next

```text
P1-3: Diagnosis-Guided Reshaping + Router + Revision Contract
```