# Canonical Base Eval Summary (Task 0)

**Generated**: by experiments/canonicalize_base_eval.py
**method_version**: v0
**plan_version**: plan_v1.0_post_validation

## Overview

| Metric | Count |
|---|---|
| Discovered base attempt_1 | 19 |
| Evaluated | 3 |
| Not evaluated | 16 |
| Env/patch-apply error | 0 |
| Resolved | 1 |
| Unresolved (first-failed pool) | 2 |
| Conflicts | 0 |

## First-Failed Pool

| instance_id | batch_id | patch_exists | failure_class | test_runs | test_failures | submitted_without |
|---|---|---|---|---|---|---|
| django__django-13513 | batch2_20260628_114704 | True | hidden-failure | 3 | 0 | False |
| sympy__sympy-19954 | batch2_20260628_114704 | True | hidden-failure | 1 | 0 | False |

## NOT EVALUATED Instances

These instances lack official base_miniswe eval results.
They need Docker-based official eval before Task 3.

| instance_id | patch_path | patch_chars |
|---|---|---|
| astropy__astropy-14995 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/astropy__astropy-14995/attempt_1/patch.diff | 3017 |
| django__django-10880 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-10880/attempt_1/patch.diff | 639 |
| django__django-11179 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-11179/attempt_1/patch.diff | 614 |
| django__django-11603 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-11603/attempt_1/patch.diff | 920 |
| django__django-11815 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-11815/attempt_1/patch.diff | 4344 |
| django__django-11820 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-11820/attempt_1/patch.diff | 698 |
| django__django-12125 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-12125/attempt_1/patch.diff | 2575 |
| django__django-13028 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-13028/attempt_1/patch.diff | 669 |
| django__django-13158 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-13158/attempt_1/patch.diff | 530 |
| django__django-14349 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-14349/attempt_1/patch.diff | 794 |
| django__django-15104 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-15104/attempt_1/patch.diff | 1919 |
| django__django-15973 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-15973/attempt_1/patch.diff | 617 |
| django__django-16454 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/django__django-16454/attempt_1/patch.diff | 1099 |
| scikit-learn__scikit-learn-25232 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/scikit-learn__scikit-learn-25232/attempt_1/patch.diff | 2207 |
| sympy__sympy-20428 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/sympy__sympy-20428/attempt_1/patch.diff | 628 |
| sympy__sympy-20590 | /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/base_miniswe/sympy__sympy-20590/attempt_1/patch.diff | 316 |

## Source Files Used

- /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/repair_smoke_eval_matrix.json
- /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/sympy19954_eval.json
- /mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/eval_anomaly_inspection.json
- /mnt/d/condiag-artifacts/condiag/v0/case_bundles/astropy__astropy-13398/official_eval.json
- /mnt/d/condiag-artifacts/condiag/v0/case_bundles/django__django-11400/official_eval.json
- /mnt/d/condiag-artifacts/condiag/v0/case_bundles/sympy__sympy-13877/official_eval.json
- /mnt/d/condiag-artifacts/condiag/v0/case_bundles/sympy__sympy-16597/official_eval.json

## Next Recommended Action

- 16 instances need official eval before Task 3.
- After eval, re-run this script to update canonical matrix.
- Once canonical matrix is complete, proceed to Task 1.
