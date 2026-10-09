# Documenting changes

Every pull request should explain the problem, changed behavior, validation and delivery status. Use the pull request template. Link related issues and evidence when available.

For a performance change, add or update one descriptive report in `docs/performance/`. Include:

1. Which layer changed: coordinator, worker wrapper, combat kernel or storage.
2. Exact workload, build identity, worker count, timing boundary and command or harness needed to reproduce it.
3. Matched before/after results and correctness checks. Distinguish battle time, worker time, whole-run time and strategy quality.
4. Limitations, unresolved questions, and whether source, development binaries or normal desktop builds include the change.
5. Compact machine-readable evidence; keep huge traces and generated databases out of Git.

Add a short `CHANGELOG.md` entry for relevant changes, link the detailed report, and add its link to `docs/README.md`. Update architecture/build/provenance pages if their facts change. Prefer descriptive filenames over multiple unrelated `FINDINGS.md` files.

Keep historical evidence intact. Date new measurements and explicitly supersede old conclusions instead of silently changing their numbers. Before merging documentation, check relative links and confirm claims against saved evidence.
