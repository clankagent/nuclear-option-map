# Project status

The demo's `/status/` page shows the current job, its measured progress and remaining stages. Open it from the map toolbar. It reads a compact version-one JSON snapshot at `/maps/render-status/project-status.json`; no render or retry controls are exposed. Generated snapshots remain local and are not packaged with source.

Reuse the page by changing the data producer's job configuration, not the UI. The contract contains `job` identity/title, `state`, current `stage`, ordered `stages`, `progress`, named `groups` with per-level counts, source/census timestamps, collector health, and optional resources/billing/detail links. The local producer configuration and operating instructions live with that deployment; the page has no service names or machine paths.

Optional resource fields `cpuCoresUsed`, `cpuCoresAvailable`, and `cpuSampleSeconds` show measured CPU-seconds per elapsed second, available vCPUs, and the measurement interval. The first sample is unknown. Producers must reset the measurement when the job identity or CPU counter changes; a process lifetime average is not a current usage measurement.

Optional `timing` shows the current run duration, estimated remaining time and estimated finish in the viewer's local time zone. Supply `scopeLabel`, `status`, `measuredAt`, `elapsedSeconds`, `remainingSeconds`, `estimatedFinishAt`, `sampleCount`, `sampleTiles`, `sampleElapsedSeconds`, `unitsPerSecond`, `currentBatchOverdue`, and `assumption`. A forecast requires at least three completed intervals and fresh running data. Producers can weight recent full-workflow intervals by completed work; intervals need not contain the same number of tiles. Include loading, collection, waits and handover in the measurement. The page hides the finish forecast during pauses or stale updates. The expandable explanation states the sampled work and weighted rate. Estimates apply only to the named phase; they do not establish completion or the duration of later phases.

Progress counts are provisional output observations. Reaching100% does not imply verification, appearance acceptance or delivery. A complete state requires explicit completion evidence from the producer. The page retains last-good data during failed requests and distinguishes old source measurements from a fresh JSON write. Source and progress measurement ages are shown separately. Refresh fetches a new snapshot; it does not start a job. Automatic polling pauses in a hidden tab.

A minimal producer snapshot looks like this (illustrative values):

```json
{"schemaVersion":1,"job":{"id":"example","title":"Build map tiles","subtitle":"Example job"},"state":"running","stage":{"id":"build","label":"Build tiles"},"generatedAt":"2026-01-01T12:00:00Z","sourceGeneratedAt":"2026-01-01T12:00:00Z","censusGeneratedAt":"2026-01-01T12:00:00Z","collector":{"ok":true,"lastAttemptAt":"2026-01-01T12:00:00Z","error":null,"staleAfterSeconds":180},"progress":{"unit":"tiles","completed":5,"total":10,"measuredAt":"2026-01-01T12:00:00Z","censusIntervalSeconds":120},"stages":[{"id":"build","label":"Build tiles","state":"active"}],"groups":[],"completion":{"datasetComplete":false}}
```

`running`, `blocked`, `review`, and `complete` are supported job states; stage states are `pending`, `active`, and `complete`. Optional values can be omitted or null. Counts must be nonnegative numbers. Detail links accept only HTTP(S) or root-relative paths. Text from the snapshot is rendered as text, never HTML. This view is a deployment companion; the map library itself does not require a status producer.
