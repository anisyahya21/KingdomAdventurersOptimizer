# R10 narrow comparison adapter

Use the regular R10 configuration unchanged across the Rust, Go, and C++ adapters, including raw-input/table/kernel paths, provenance inputs, seed range, and output policy. Use the same 20-row raw-input file, `generations: 1`, `mutations: []`, and `seedPointers: ["/mathSeed"]`; leave `libSeed` constant in every raw row. Run each frozen R10 adapter at executor counts 5, 10, and 12 in distinct fresh output scopes. The coordinator aligns these controls across languages.

R10 adds only this opt-in configuration:

```json
"comparison": {
  "durableSyncBatchSize": 1,
  "stageTimingTelemetry": true
}
```

Omitting `comparison` on R10 preserves its legacy batching semantics; legacy R9 remains frozen. `durableSyncBatchSize: 1` sets the maximum number of result-journal rows per durable sync chunk (Windows uses `FlushFileBuffers`); it does not set the submission batch size. With no explicit comparison value, the maximum chunk follows the existing drain batch, which is capped at twice the worker count. The effective comparison settings are included in `searchPolicy`, so resume compatibility binds to them. The executable SHA-256 is captured automatically in provenance.

Timing fields cover several different scopes. Main-process stages include `configReadWallNs`, `inputHashVerificationWallNs`, `identityHashWallNs`, `monitorLaunchWallNs`, and `mainBeforePipelineWallNs`. The latter contains the preceding main-thread intervals; do not add them to it. Serial pipeline admission/preparation and journal work are wall durations. `workerExecuteCallWallNs` and the execution-stage totals are sums across workers, so concurrent intervals overlap and those sums are not run wall time. Execution stages are `importSetupWallNs`, `nativeRunWallNs`, `reportCollectionWallNs`, and `totalExecuteWallNs`; report collection includes checksum, getter, and projection work, while total execute excludes battle destruction after return. The outer worker-call duration includes wrapper/teardown overhead. CPU busy time is unmeasured. Checkpoint/status writes, leader export, and diagnostic-journal durations are not isolated by these fields.

The final R10 source is compiled; it has not yet been executed. Use external process start-to-exit elapsed time as the authoritative end-to-end comparison. Treat telemetry and sync counters as diagnostics only. They do not establish production evidence, and this document makes no performance claim.
