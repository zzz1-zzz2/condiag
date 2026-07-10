# Retry Context

## What Went Wrong

The previous attempt looked at the right code but the patch didn't correctly apply the findings. The evidence below shows what was discovered — align the fix with it.

## Primary Edit Target

- **File**: `sympy/polys/densetools.py`
- **Target**: `dmp_clear_denoms` (lines 1200-1270)
- **Goal**: Re-apply the key evidence the previous attempt saw but did not use in the final patch.

## Relevant Code

### Rehydrated (previously seen)

- **File**: `sympy/polys/densetools.py` (lines 1200-1270)
- **Relevance**: Attempt 1 viewed this span but it is absent from (or only partially present in) final PATCH_CONTEXT. Matches target keywords: ['densetools', 'Result', 'clear_denoms'].

```python
L 1200 | 
L 1201 |     if not convert:
L 1202 |         return common, f
L 1203 |     else:
L 1204 |         return common, dup_convert(f, K0, K1)
L 1205 | 
L 1206 | 
L 1207 | def _rec_clear_denoms(g, v, K0, K1):
L 1208 |     """Recursive helper for :func:`dmp_clear_denoms`."""
L 1209 |     common = K1.one
L 1210 | 
L 1211 |     if not v:
L 1212 |         for c in g:
L 1213 |             common = K1.lcm(common, K0.denom(c))
L 1214 |     else:
L 1215 |         w = v - 1
L 1216 | 
L 1217 |         for c in g:
L 1218 |             common = K1.lcm(common, _rec_clear_denoms(c, w, K0, K1))
L 1219 | 
L 1220 |     return common
L 1221 | 
L 1222 | 
L 1223 | def dmp_clear_denoms(f, u, K0, K1=None, convert=False):
L 1224 |     """
L 1225 |     Clear denominators, i.e. transform ``K_0`` to ``K_1``.
L 1226 | 
L 1227 |     Examples
L 1228 |     ========
L 1229 | 
L 1230 |     >>> from sympy.polys import ring, QQ
L 1231 |     >>> R, x,y = ring("x,y", QQ)
L 1232 | 
L 1233 |     >>> f = QQ(1,2)*x + QQ(1,3)*y + 1
L 1234 | 
L 1235 |     >>> R.dmp_clear_denoms(f, convert=False)
L 1236 |     (6, 3*x + 2*y + 6)
L 1237 |     >>> R.dmp_clear_denoms(f, convert=True)
L 1238 |     (6, 3*x + 2*y + 6)
# ... (truncated, 71 lines total)
```

### Rehydrated (previously seen)

- **File**: `sympy/polys/densetools.py` (lines 1-50)
- **Relevance**: Attempt 1 viewed this span but it is absent from (or only partially present in) final PATCH_CONTEXT. Matches target keywords: ['densetools', 'densearith', 'densebasic'].

```python
L    1 | """Advanced tools for dense recursive polynomials in ``K[x]`` or ``K[X]``. """
L    2 | 
L    3 | 
L    4 | from sympy.polys.densearith import (
L    5 |     dup_add_term, dmp_add_term,
L    6 |     dup_lshift,
L    7 |     dup_add, dmp_add,
L    8 |     dup_sub, dmp_sub,
L    9 |     dup_mul, dmp_mul,
L   10 |     dup_sqr,
L   11 |     dup_div,
L   12 |     dup_rem, dmp_rem,
L   13 |     dmp_expand,
L   14 |     dup_mul_ground, dmp_mul_ground,
L   15 |     dup_quo_ground, dmp_quo_ground,
L   16 |     dup_exquo_ground, dmp_exquo_ground,
L   17 | )
L   18 | from sympy.polys.densebasic import (
L   19 |     dup_strip, dmp_strip,
L   20 |     dup_convert, dmp_convert,
L   21 |     dup_degree, dmp_degree,
L   22 |     dmp_to_dict,
L   23 |     dmp_from_dict,
L   24 |     dup_LC, dmp_LC, dmp_ground_LC,
L   25 |     dup_TC, dmp_TC,
L   26 |     dmp_zero, dmp_ground,
L   27 |     dmp_zero_p,
L   28 |     dup_to_raw_dict, dup_from_raw_dict,
L   29 |     dmp_zeros
L   30 | )
L   31 | from sympy.polys.polyerrors import (
L   32 |     MultivariatePolynomialError,
L   33 |     DomainError
L   34 | )
L   35 | from sympy.utilities import variations
L   36 | 
L   37 | from math import ceil as _ceil, log as _log
L   38 | 
L   39 | def dup_integrate(f, m, K):
L   40 |     """
# ... (truncated, 50 lines total)
```

### Neighbor test: `test_sympy__functions__special__polynomials__OrthogonalPolynomial`

- **File**: `sympy/core/tests/test_args.py` (lines 2600-2601)
- **Relevance**: Neighbor test matching concept keywords (3 matches).

```python
L 2600 | def test_sympy__functions__special__polynomials__OrthogonalPolynomial():
L 2601 |     pass
```

### Neighbor test: `test_sympy__functions__special__polynomials__jacobi`

- **File**: `sympy/core/tests/test_args.py` (lines 2604-2606)
- **Relevance**: Neighbor test matching concept keywords (3 matches).

```python
L 2604 | def test_sympy__functions__special__polynomials__jacobi():
L 2605 |     from sympy.functions.special.polynomials import jacobi
L 2606 |     assert _test_args(jacobi(x, 2, 2, 2))
```

## Guidelines

- Make the smallest change that fixes the issue.
- Do not modify files unrelated to the evidence above.
- Run the failing tests to verify before submitting.

## Retry Instruction

In `sympy/polys/densetools.py`, inspect `dmp_clear_denoms` (lines 1200-1270). Re-apply the key evidence the previous attempt saw but did not use in the final patch. Make a minimal, local edit. When overriding factory methods that create child objects (e.g. add_subparsers), check the parent class documentation for keyword arguments that customize child instantiation — use the standard library mechanism rather than wrapping return values. Run `test_sympy__functions__special__polynomials__OrthogonalPolynomial, test_sympy__functions__special__polynomials__jacobi` to verify the fix.
