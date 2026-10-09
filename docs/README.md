# Start here

Use this page as the repository map. The preserved `project/KA-Website` name contains optimizer code as well as its UI; the name does not mean everything inside is website code.

| I want to… | Open |
| --- | --- |
| Run the desktop or a short smoke check | [Root README](../README.md) |
| Understand the coordinator, workers and combat kernel | [Architecture and source map](ARCHITECTURE.md) |
| Find the worker checksum investigation and measured improvements | [Worker state checks](performance/worker-state-checks.md) |
| See changes and their delivery status | [Changelog](../CHANGELOG.md) |
| Build from source | [Build instructions](BUILD.md) |
| Know which source/binaries a launcher actually uses | [Provenance](PROVENANCE.md) |
| Review existing bugs | [Known problems](KNOWN_PROBLEMS.md) |
| Find verification and dependencies | [Verification](VERIFICATION.md), [dependencies](DEPENDENCIES.md) |
| Document a future change | [Documentation conventions](CONTRIBUTING.md) |

## Where things belong

- `docs/`: maintained explanations, navigation, build and verification instructions.
- `docs/performance/`: focused performance investigations with scope, before/after results and delivery status. Compact evidence goes in `docs/performance/evidence/`.
- `project/coordination/`: historical investigation workspaces, native sources and build manifests. Use the source map above instead of guessing from folder dates.
- `runtime/`: ignored local runs, databases and generated artifacts; these are not shared through GitHub.

A report describes its measured build. It does not prove that the pinned desktop build includes that change. Check the report's status and binary provenance before comparing results.
