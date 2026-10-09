# Models

Fitted artifacts that outlive a single run — ML weights, optimization
solutions, serialized transforms. Run-scoped outputs stay in the run
directory the engine writes; a file belongs here only when a later
config or the serving loop must reload it by stable name.

Contents are gitignored (see `.gitignore`): reproducibility is the run
document's identity hash recorded in `docs/decisioning/actions.csv`,
never a committed binary or a filename convention.
