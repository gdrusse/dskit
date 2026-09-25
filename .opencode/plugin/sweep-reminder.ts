// SWEEP THE REPO BEFORE BUILDING (tools/sweep): put the owner rule in the
// system prompt of every opencode session in this repo, so DeepSeek/GPT
// agents search before they write. The commit-msg hook enforces it.
const RULE =
  "dskit owner rule -- SWEEP THE REPO BEFORE BUILDING: we keep redoing work " +
  "that already exists. Before writing a new file, public class/function, " +
  "node kind or config, run `tools/sweep/sweep <name> <synonyms>` (searches " +
  "origin/main, every branch and every worktree) and extend what exists " +
  "instead of rebuilding it. Commits that add new files/symbols/kinds need a " +
  "trailer `Sweep: <what you searched; why this is not a duplicate>`; the " +
  "commit-msg hook refuses them otherwise."

export default async () => {
  return {
    "experimental.chat.system.transform": async (_input, output) => {
      if (!output.system.includes(RULE)) output.system.push(RULE)
    },
  }
}
