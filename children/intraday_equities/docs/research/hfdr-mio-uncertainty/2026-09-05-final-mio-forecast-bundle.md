## Locked decision

The model zoo selects a modeling procedure; it is not the MIO interface. After
selection, the final model must be trained under the promoted procedure and
publish one versioned, as-of-safe MIO forecast bundle before capital is
eligible.

The model-owned bundle must contain:

- decision timestamp, entity, holding horizon, and model/release identity;
- gross and cost-adjusted conditional mean alpha;
- calibrated `pi_hat`, conservative `pi_upper`, and joint `U_pi` provenance;
- joint conditional-mean uncertainty `U_mu`;
- weighted joint net-return scenarios `U_r`;
- Gate-1/Gate-3 eligibility and every model/calibration/null/data hash needed
  to reproduce the values;
- units, horizon semantics, scenario weights, coverage targets, and expiry.

Current positions, cash, prices, liquidity, lot sizes, exposure limits, and
other portfolio state are separate MIO inputs and must not be invented by the
model bundle. The MIO must refuse a missing, stale, schema-incompatible, or
unverifiable bundle.

Research support and proposed constructions are recorded in
`docs/research/hfdr-mio-uncertainty/`. The exact bundle schema is an
implementation contract; requiring a complete, versioned, fail-closed handoff
is an owner-locked architectural decision.

**Decision criterion: judgemental.**
