# Local Crop enrichment handoff

The live intake acceptance passed: signed Farmer lookup, separate business and
Farmer ADD AWE approvals, dependency hold, automatic queue release, and one
approved Farmer reference plus one active Crop. The stored Crop name matches
the approved source; its year and season are preserved. See
`evidence/local-intake-approvals.json`.

The approved-source UPDATE acceptance is **blocked**, not passed. The receiving
WebSub subscription registered and verified its callback. An explicit transport
probe reached the HMAC-protected callback and connector worker, then encountered
a Partner Management read timeout. Native error response serialization also
failed on a datetime. The probe was not a new source approval. See
`evidence/local-update-receiver.json`.

Opus owns the remaining Farmer source outgestion configuration and actual
central Farmer change/approval. Its populated Farmer register currently has
`outgest_applicable=false`. Native `edit_register` ignores that flag once data
exists; a successful HTTP response does not prove it changed. Use the reviewed
OAN metadata configuration path, preserving records, then read back the flag.
Farmer Staff API/UI remain owned by Opus; this work did not edit or seed them.

Crop Farmer UPDATE approvals now change only the Farmer reference. Existing
stored Crop mirrors remain until their own domain approval. New approved Crop
ingestion still populates that Crop's own identity fields from its approved
Farmer dependency. Native PostgreSQL and projection regressions verify this
boundary.

## Resume after runtime recovery

Docker control calls, fresh Crop login, and PostgreSQL host connections began
timing out while Windows had roughly 329–388 MB free RAM. No Docker Desktop or
WSL restart was performed. Stop requests for the task-added connector and WebSub
containers also timed out; inspect their actual state before starting anything.
Do not infer that they stopped. Records, queues, volumes, and checkpoints were
not removed.

Runtime artifacts are local and ignored under
`D:/WORK/Protean/OAN/local-stack/out/`; they contain private configuration and
must not be published. The named Python scripts below are retained under
`D:/WORK/Protean/Temp/fayda-popup-alignment/`.

1. Read `D:/WORK/Protean/agent-communications.md` and coordinate source ownership.
   Keep Livestock stopped while Crop runs. The existing 60-second watcher is
   read-only; it does not replace an agent reading and acting on lead messages.
2. Check Docker and application health. Reuse `crop-preview/runtime-current.yaml`
   with the rendered Crop compose. The local `crop.cmd` launcher now includes
   this override and uses `--no-deps`, preserving its current images and avoiding
   seed jobs. Do not regenerate the main Commons manifests.
3. Use `configure_crop_notification.py` to publish the new response template via
   native document upload and `update_data_model`. This preserves the original
   binding and uploads a UUID-keyed catalog document in the native `templates`
   bucket. The existing-database publication has **not run successfully** yet;
   it stopped at a fresh-login timeout. New-install metadata contains its binding.
4. Resume the isolated WebSub compose at
   `crop-update/rendered/integration/compose.yaml`, and the connector compose at
   `crop-update/connector-compose.yaml`. The connector worker command is configured
   for `--concurrency=1`; verify the running command. All four automatic legacy
   ODK pipelines were disabled through connector administration before its
   worker first started. Keep them disabled.
5. Read the native connector failed run and retry through its administration API
   after confirming whether registry ingestion committed. Preserve its event ID
   and deduplication state. Do not forge registry callbacks or approve via SQL.
6. Once Opus supplies a real approved source change and outgestion, follow native
   transformation, WebSub/Kafka delivery, connector ingestion, current-source
   lookup, local Farmer UPDATE CR, geographic AWE task, and signed callback.
   Verify old local values before target approval and new reference values after
   it; verify existing Crop rows and identity grants remain unchanged.
7. Post STEP4 PASS only with fresh read-back evidence. Then stop Crop runtime
   without deleting volumes and hand serial Livestock testing to Opus.

The response template preserves the native success/error envelope and converts
its timestamp to JSON text. Two local Jinja cases passed. Errors occurring before
native response-template resolution can still hit the platform's fallback JSON
serialization bug; no upstream code was patched or replaced.
