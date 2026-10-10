# Data enters through onboarding

Every dataset a run reads is a registered, acquired onboarding source: vendor
downloads, archives, exported parquet/JSON tables, and any table one run derived
and a later run consumes. `dskit.onboarding register-source` + `acquire` gives it
a snapshot, a content hash, a clock and a journal entry, and consumers read it
by catalog source name and stream.

- Configs and code name a source (`root` / `source` / `stream`). They never name
  an absolute path into a home directory, another checkout, a sibling `dskit-*`
  clone, a scratch worktree or `/tmp`. A path like that in a config is a defect,
  not a convenience.
- `root` is the store, the one absolute path a run config may carry; the only other
  place an origin path legitimately appears is a registration config
  (`children/*/configs/source-store-*.json` and similar), once. Files that are not row
  streams (parquet archives, tables) are acquired with the `localblobs` kind (ADR-0225) and
  read through `dskit.onboarding.payload_files(root, source, stream)`: a config names
  `{source, stream, relpath}`, never a path.
- A derived table that a later step or run reads is published back through
  onboarding first (the `localtables` connector covers directories of parquet and
  newline-JSON), and the producing run records the hashes of what it read.
- Check what already exists before adding anything: the store catalog,
  `tools/sweep/sweep`, ADR-0076 (`localtables`), ADR-0082 (binary artifacts, the
  `huggingface` pack) and the ADR-0182 amendment (the `optionshist` pack for the
  EOD SPY/QQQ/IWM chain archive). A new connector pack needs an ADR.
- Research clones and scratch worktrees are not data homes. No result may depend
  on a file that lives only in one of them or in the `pipeline_runs/` of an
  unmerged branch.
- If a study truly cannot use the onboarded form, its config notes and ADR say
  why, and an onboarding task is opened that same day. A permanent path is not
  the answer.

Why this exists: in Sep 2026 the index_options studies read the options archive, a
prepared panel and several derived parquet files by absolute path from sibling
clones, although onboarding packs for them existed. Nothing in the repo required
otherwise, so results ended up depending on files no catalog listed and a rerun
meant finding where each file had been left.
