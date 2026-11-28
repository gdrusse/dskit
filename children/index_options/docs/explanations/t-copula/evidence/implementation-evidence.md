# Student t copula implementation evidence

Base `13068c945a99c6e52b3ceb3754e54b785fb7b328`, ADR-0331. The locked child Path was read. This packet used synthetic records only. The fit, score, sampling and native price inversion paths are separate timed operations.

## RED and GREEN

- `red.txt` ran the initial public fit outcome checks against a loading new-package scaffold. Both intended assertions failed because the fit returned no model or diagnostics.
- `install.txt` records the guarded dependency resolution for Julia 1.13.4 and Distributions 0.25.136 in the isolated depot.
- `green-smoke.txt` records an initial Cholesky storage-type error. Symmetric lower storage provides the concrete lower-triangular factor expected by the draw path.
- `green-tests-v1.txt` exposed two inaccurate fixture expectations. The observed origin sets for A/1, A/2 and B/1 were {1,2,5}, {1,2,3,4,5} and {3,4,5}. The A/2–B/1 overlap is three origins. At origin 5 all three coordinates are paired and carry endpoints 0, 1 and 0. Removing the A/1 and A/2 records at origin 5 leaves a single unmatched B/1 endpoint which contributes zero clips.
- `green-tests-v2.txt` records 99 passed under the guarded focused Julia command. It checks density scores against independent SciPy 1.18.1 values, time boundaries, delayed labels, overlap and repair, refusal paths, shared-radial tails, seeded batches and the two inverses. `readme-example.txt` executes the two Julia README examples directly.
- `sweep-exact.txt` records 40 refs and 95 other worktrees. The exact package and public API names had no near matches. The existing Python empirical copula consumes bootstrap return paths and has a different fitting contract.

## Synthetic runtime

WSL2 on an AMD Ryzen 7 7700X, Julia 1.13.4 and Distributions 0.25.136. One Julia thread, two OpenBLAS threads and one guarded benchmark process. Three warmed runs each. The 1,070×1,328 case has 881,128 dispersion pairs, 64 scoring edges, 1,349,750 fit records, and 240 later origins with 302,881 records. It evaluates five finite candidates and exact Gaussian. Sampling returns 2,048 scenarios in batches of 256.

- `benchmark-v1.txt` records warm median fit 0.537 s, score 0.0738 s, draw 0.479 s and price map 0.00379 s. Process peak RSS was 776,396 KiB, about 0.740 GiB. The LaTeX/PDF report uses this run.
- `benchmark-v2.txt` records fit 0.533 s, score 0.0767 s, draw 0.477 s and price map 0.00249 s. Process peak RSS was 775,364 KiB. Instantiated-depot package load took 0.284 s. The initial small case took 0.493 s including synthetic setup and warm repeats, of which the Julia timer reported 0.164 s compilation. The driver added this outer timer between the two runs. The implementation and fixture algorithm were unchanged.
- Cumulative allocations per wide call were approximately 582.1 MB for fit, 112.3 MB for score, 484.1 MB for draw and 21.8 MB for inverse mapping. Peak live process memory is the RSS measure. The draw returns about 21.8 MB of uniforms. The remaining allocation total largely comes from the pinned Student t CDF's incomplete-beta evaluation. In `cdf-latent-allocations.txt`, 100,000 Student t(4) evaluations allocate 17,140,752 bytes through either the typed Distributions call or its StatsFuns CDF.
- The 200×8 fit was about 0.0068 s warm and the 2×2,048 Gaussian draw control about 0.00010 s. The isolated dependency depot uses 136 MiB. The storage guard allowed the install, test and synthetic benchmark jobs and showed zero reservations after completion.

## Bound artifacts

- `julia/DskitCopulas/Project.toml` SHA-256 `6f1168cb953ebbde98fdcb8c49ca54d9389009d6a6b0eb238fd8706453d8f01a`.
- `julia/DskitCopulas/Manifest.toml` SHA-256 `1b977cd8b804125bf9553440450171c1674099c9e87e3e736c3f0a0b1e8ac97b`.
- `julia/DskitCopulas/README.md` SHA-256 `ed63f30f4d101587c8c2937039bed905cfdb20ba21ce3461b9937f05385528d6`.
- `julia/DskitCopulas/src/DskitCopulas.jl` SHA-256 `0fd263a39498d59ee580656d13fdc1a592c4a8bd93ae9d4b89e108a5cb60a408`.
- `julia/DskitCopulas/test/runtests.jl` SHA-256 `4a809372cbdcee709ba2ab17bd23b9d5b8ba0fb6301061489e5ccac2cc1c1cb7`.
- `julia/DskitCopulas/benchmark/run.jl` SHA-256 `30712375aadacb5aadc7daba067a2639c6b2c025222a671ec352a8a147e70bb0`.
- `children/index_options/docs/explanations/t-copula-implementation.tex` SHA-256 `efc199bb2cb2df4fefee852f0f00c76abaf861878d102774b6939357c66d5c7e`.
- The benchmark-v1 driver before the outer timing addition has SHA-256 `f54290c04265868ca5f7461f1fdbf1556e29043abe7db099ff93213caf7cb09d`.
- Rendered formulation PDF SHA-256 `102ca402f42707d277d0ac3d0c95767b4be0b6de7ab8fd80128e57d58a1a6b74`. See `pdf-visual-review.json`.

Initial review candidate `9b476c1eb4b7a31c5329adfcc06330ad7d86b2a9`. Staged diff check passed.

## Final reviewed candidate

Candidate `9b476c1eb4b7a31c5329adfcc06330ad7d86b2a9` is closed with two fresh independent Luna lenses: zero Critical/Major, one disclosed Minor test-coverage note deferred as described in `review-lock.json`. Correctness independently reran all 99 Julia assertions. Four focused documentation budget checks and `git diff --check` passed. The candidate code, test, contract and dependency blobs were unchanged during final reviews. All nine PDF pages were rendered and visually checked.

The retained correctness report is `../run/reviews/correctness-r1.md`; test-quality report is `reviews/tests-r1.md`. Exact digests and the Minor disposition are in `review-lock.json`. No candidate push or merge was performed; the isolated branch is retained alongside the simultaneous Claude work. Final storage admission was allowed with zero reservations, 8.11 GiB Windows available RAM and 15.15 GiB C: free; WSL and Windows memory were not added.
