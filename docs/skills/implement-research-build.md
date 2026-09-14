# Implement a research finding or plan

1. Read implementation-workflow.md, the source finding/plan, nearest agent
   instructions and current RE-ENTRY. Select one bounded approved acceptance
   result and check the controlling dependency DAG and latest findings.
2. Inventory existing capability. Generic reusable behavior belongs in dskit
   core or its library pack; only domain policy belongs in the child. Extend
   existing seams instead of creating parallel plumbing.
3. Follow test-drive-development.md and skeptic-review.md automatically:
   proportional contract/test matrix, high-risk Phase 0 when applicable,
   focused RED/GREEN, family-wide fixes, two independent lenses, and lock.
4. ADR-before-code and execution/owner gates remain valid in interactive and
   chain runs alike. A runner's auto-approve setting does not supply missing
   user authorization. Reuse approvals already granted; do not ask again.
5. Record actual outcomes in the existing action journal/memo as appropriate.
   Do not edit owner-only path.csv or hand-edit generated decisioning README.
   Apply wrap.md for already-authorized commit, merge, push, and cleanup.
