## Proposed decision

For each signal, publish `pi_hat` and a conservative value `pi_upper` (or a
joint set `U_pi`). The capital optimizer must enforce the ADR-0088 policy

`max_{pi in U_pi} sum_i(x_i * pi_i) <= q * sum_i(x_i)`.

For a rectangular set this reduces to
`sum_i(x_i * pi_upper_i) <= q * sum_i(x_i)`. The calibration sample,
confidence level, set construction, gross-capital definition, and `q` are
pinned before optimization. The optimizer may not estimate them from the
holdings it chooses.

## Research backing and judgement

Robust optimization supplies the general worst-case-constraint construction,
and conformal risk control supplies data-calibrated control of monotone risks:

- Ben-Tal and Nemirovski (1999), *Robust solutions of uncertain linear
  programs*: https://doi.org/10.1016/S0167-6377(99)00016-4
- Angelopoulos et al. (2024), *Conformal Risk Control*:
  https://arxiv.org/abs/2208.02814

**Decision criterion: judgemental.** Worst-case protection is research-backed,
but the choice of `q`, confidence level, and box versus joint set expresses
the owner's capital-risk preference rather than an empirical fact.
