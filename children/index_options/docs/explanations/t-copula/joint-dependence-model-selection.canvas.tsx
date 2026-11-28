import {
  Button,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Link,
  Row,
  Stack,
  Table,
  Text,
  useCanvasAction,
  useHostTheme,
  useState,
} from "cursor/canvas";

const reportPath = "\\\\wsl.localhost\\Ubuntu-24.04\\home\\russell\\reports\\Joint dependence model selection.md";

const sources = {
  student: {
    label: "Demarta and McNeil · Student t",
    href: "https://doi.org/10.1111/j.1751-5823.2005.tb00254.x",
  },
  factor: {
    label: "Oh and Patton · Factor copulas",
    href: "https://public.econ.duke.edu/~ap172/Oh_Patton_MV_factor_copula_JBES_2017.pdf",
  },
  shrinkage: {
    label: "Ledoit and Wolf · Shrinkage",
    href: "https://doi.org/10.1016/S0047-259X(03)00096-4",
  },
  checkerboard: {
    label: "Segers et al. · Empirical copulas",
    href: "https://arxiv.org/abs/1607.04430",
  },
  empiricalJulia: {
    label: "Copulas.jl · Empirical models",
    href: "https://lrnv.github.io/Copulas.jl/stable/bestiary/empirical",
  },
  vine: {
    label: "Nagler et al. · Sparse vines",
    href: "https://arxiv.org/abs/1801.09739",
  },
  vineJulia: {
    label: "VineCopulas.jl · Released v0.1.2",
    href: "https://github.com/Santymax98/VineCopulas.jl/blob/v0.1.2/README.md",
  },
  vineCompat: {
    label: "VineCopulas.jl · Tagged compatibility",
    href: "https://github.com/Santymax98/VineCopulas.jl/blob/v0.1.2/Project.toml",
  },
};

type Candidate = {
  id: string;
  name: string;
  structure: string;
  tail: string;
  role: string;
  assumption: string;
  evidence: string;
  implementation: string;
  references?: Array<{ label: string; href: string }>;
};

const candidates: Candidate[] = [
  {
    id: "independence",
    name: "Independent uniforms",
    structure: "Independent cells",
    tail: "Independent extremes",
    role: "Sanity baseline",
    assumption: "Draw an independent uniform for each cell. All candidates use the same current price marginals.",
    evidence: "Learned dependence should improve forecasts of joint moves on the same outcomes.",
    implementation: "Single-stage Julia draws with the same inversion and scoring interface as the other candidates.",
  },
  {
    id: "dense-gaussian",
    name: "Dense shrunken Gaussian",
    structure: "Shrunken pairwise correlations",
    tail: "Symmetric, asymptotically independent",
    role: "Reference",
    assumption: "A dense positive-definite correlation matrix allows pair-specific associations. Shrinkage controls instability when joint origin histories are short.",
    evidence: "Retain dense structure if it consistently captures local risk or residual clusters missed by Gaussian factor pooling.",
    implementation: "Use the candidate Julia package for the Gaussian reference. A principal submatrix gives the model on the active cells.",
    references: [sources.shrinkage],
  },
  {
    id: "dense-student",
    name: "Dense matched Student t",
    structure: "Same dispersion as dense Gaussian",
    tail: "One shared scale, symmetric dependence",
    role: "Tail reference",
    assumption: "One shared radial draw per scenario introduces symmetric joint tail dependence while keeping the latent dispersion fixed for the comparison.",
    evidence: "Test for repeatable gains in joint skill. Check upper and lower calibration and the breadth of simultaneous extreme moves before choosing t.",
    implementation: "Use the tested Julia sampler. Select the degrees of freedom on permitted fit data with matched pair-origin terms. Finite degrees of freedom connect extremes even when the dispersion is diagonal.",
    references: [sources.student],
  },
  {
    id: "structured-gaussian",
    name: "Structured Gaussian",
    structure: "Broad factors + within-stock expiry blocks",
    tail: "Symmetric, asymptotically independent",
    role: "Lead challenger",
    assumption: "A few broad factors pool cross-stock evidence. Shrunken within-stock blocks retain dependence among a name’s original expiry ranks.",
    evidence: "Advance if pooling improves or maintains joint forecast quality across time without erasing local co-movement. Persistent residual clusters could justify more factors or retaining dense structure.",
    implementation: "A Julia sampler can use shared factor draws and small residual blocks. A joint fit and engineering benchmarks would need separate validation.",
    references: [sources.factor, sources.shrinkage],
  },
  {
    id: "checkerboard",
    name: "Empirical checkerboard",
    structure: "Common-origin rank boxes",
    tail: "Historical rank patterns with within-box jitter",
    role: "Small-panel control",
    assumption: "Choose one eligible common origin per draw, then sample each coordinate inside its rank interval. A complete panel with explicit tie handling gives exact uniform marginals.",
    evidence: "Use the same predeclared, well-covered small panels for this model and the parametric controls. Large unoccupied regions make it a weak guide to unseen joint extremes.",
    implementation: "Copulas.jl documents fit and sample support. Freeze the rank table for a panel and project the same draw when active cells change.",
    references: [sources.checkerboard, sources.empiricalJulia],
  },
  {
    id: "asymmetric-factor",
    name: "Asymmetric common factor",
    structure: "One directional shared shock",
    tail: "Allows stronger downside dependence",
    role: "Evidence-triggered challenger",
    assumption: "A common directional shock can raise joint downside probability. Transform each coordinate with its exact latent marginal CDF to retain uniformity.",
    evidence: "Advance only if broad downside co-exceedances remain understated across development periods after checking marginal calibration and observable issue-time state.",
    implementation: "Requires a specified latent law and a Julia fit and sampler. Keep the number of new tail parameters small.",
    references: [sources.factor],
  },
  {
    id: "truncated-vine",
    name: "Truncated vine",
    structure: "Fixed shallow conditional pair graph",
    tail: "Pair-specific tail families and rotations",
    role: "Evidence-triggered small panel",
    assumption: "Fit a shallow conditional pair graph on a fixed, well-covered panel. A sparse vine still models dependencies through pair distributions.",
    evidence: "Test only if repeatable localized conditional errors remain after simpler structure and asymmetry tests.",
    implementation: "VineCopulas.jl v0.1.2 fits and samples truncated C/D vines on complete data. Its released general R-vine sampler requires full depth. The tag requires Copulas.jl 0.1.x.",
    references: [sources.vine, sources.vineJulia, sources.vineCompat],
  },
];

export default function JointDependenceModelSelection() {
  const theme = useHostTheme();
  const dispatch = useCanvasAction();
  const [selected, setSelected] = useState(
    candidates.find((candidate) => candidate.id === "structured-gaussian") ?? candidates[0],
  );

  return (
    <Stack gap={22} style={{ boxSizing: "border-box", maxWidth: 1120, margin: "0 auto", padding: "24px clamp(12px, 3vw, 32px)", color: theme.text.primary }}>
      <Stack gap={8}>
        <Row gap={10} justify="space-between" align="center" wrap>
          <H1>Joint dependence model selection</H1>
          <Button variant="secondary" onClick={() => dispatch({ type: "openFile", path: reportPath })}>
            Open research report
          </Button>
        </Row>
        <Text weight="semibold" size="small" style={{ color: theme.accent.primary }}>
          Research proposal · No empirical winner
        </Text>
        <Text>
          Lead with structured Gaussian against independent uniforms and a matched dense Gaussian/Student t pair.
          Inventory the mature PIT panel before fitting any model.
        </Text>
        <Text size="small" tone="secondary">
          Design scale is 166 stocks, up to 8 original expiry ranks and roughly 1,070 origins.
          Usable joint PIT counts are unaudited. Existing Julia timings are synthetic only. Comparative runtimes are unmeasured.
        </Text>
      </Stack>

      <section aria-labelledby="comparison-title">
        <Stack gap={8}>
          <div id="comparison-title"><H2>Candidate comparison</H2></div>
          <Text size="small" tone="secondary">
            Compare structure within Gaussian models and tail behavior at matched dispersion. Select a candidate to see the evidence it needs.
          </Text>
          <Table
            headers={["Candidate", "Dependence structure", "Tail behavior", "Role"]}
            rows={candidates.map((candidate) => {
              const active = candidate.id === selected.id;
              const selectButton = (
                <button
                  type="button"
                  aria-pressed={active}
                  aria-controls="candidate-details"
                  onClick={() => setSelected(candidate)}
                  style={{
                    appearance: "none",
                    background: active ? theme.fill.tertiary : "transparent",
                    color: active ? theme.accent.primary : theme.text.link,
                    border: `1px solid ${active ? theme.stroke.secondary : "transparent"}`,
                    borderRadius: 4,
                    cursor: "pointer",
                    font: "inherit",
                    fontWeight: 590,
                    padding: "3px 6px",
                    margin: "-3px -6px",
                    textAlign: "left",
                  }}
                >
                  {candidate.name}
                </button>
              );
              return [selectButton, candidate.structure, candidate.tail, candidate.role];
            })}
          />
          <section
            id="candidate-details"
            aria-labelledby="candidate-details-title"
            aria-live="polite"
            style={{ marginTop: 4, padding: "12px 14px", background: theme.fill.tertiary, borderRadius: 6 }}
          >
            <Stack gap={8}>
              <div id="candidate-details-title"><H3>{selected.name}</H3></div>
              <Text>{selected.assumption}</Text>
              <Grid columns="repeat(auto-fit, minmax(min(100%, 245px), 1fr))" gap={14}>
                <Stack gap={3}>
                  <Text size="small" weight="semibold">Evidence that would change the choice</Text>
                  <Text size="small" tone="secondary">{selected.evidence}</Text>
                </Stack>
                <Stack gap={3}>
                  <Text size="small" weight="semibold">Julia feasibility</Text>
                  <Text size="small" tone="secondary">{selected.implementation}</Text>
                </Stack>
              </Grid>
              {selected.references && (
                <Row gap={12} wrap>
                  {selected.references.map((reference) => (
                    <span key={reference.href}>
                      <Link href={reference.href} style={{ fontSize: 12 }}>
                        {reference.label}
                      </Link>
                    </span>
                  ))}
                </Row>
              )}
            </Stack>
          </section>
        </Stack>
      </section>

      <Divider />
      <section aria-labelledby="study-title">
        <Stack gap={10}>
          <div id="study-title"><H2>What every comparison holds fixed</H2></div>
          <Grid columns="repeat(auto-fit, minmax(min(100%, 250px), 1fr))" gap={18}>
            <Stack gap={4}>
              <H3>Evidence at the cutoff</H3>
              <Text size="small" tone="secondary">
                Align issued corrected PITs on the same historical origin and original expiry rank. Use origins before the cutoff
                and outcomes available by it. Keep common masks for paired comparisons and preserve locked Path rules and sealed windows.
              </Text>
            </Stack>
            <Stack gap={4}>
              <H3>Current marginal forecasts</H3>
              <Text size="small" tone="secondary">
                Draw one joint vector with a uniform for each current cell. Apply the current inverse recalibration Γ⁻¹, then the current
                inverse price CDF Ψ⁻¹. All contracts in a cell share its price.
              </Text>
            </Stack>
            <Stack gap={4}>
              <H3>What earns more complexity</H3>
              <Text size="small" tone="secondary">
                Compare prospective joint scores and both tails, including the count of stocks moving together.
                Use time blocks for uncertainty. Test temporal conditioning separately with issue-time variables.
                Keep the choice open if evidence is weak or inconsistent.
              </Text>
            </Stack>
          </Grid>
        </Stack>
      </section>
    </Stack>
  );
}
