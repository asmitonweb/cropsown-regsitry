# Celery worker patches

Files here are mounted over the corresponding modules inside the
`openg2p/openg2p-registry-celery` base image (see `docker-compose.yml`,
service `celery-worker`), the same way `core_pkg` and the extension are
mounted. They exist because the fix lives in a base-image package that this
repo does not build.

## `intake_form_register_ingest_worker.py`

The stock worker's `_insert_register_row` inserts every intake master record
as a brand-new register row keyed on the intake row's own `internal_record_id`
(and raises `"Register row ... already exists"` on retry). It has no
parent-resolution, so approving a **Sowing** or **Harvesting** intake form
created a *new* crop-sown record instead of attaching to the crop-sown record
the Planning/Cultivation forms already made.

The patch adds, for `REGISTER`-purpose masters only, the same lookup the
synchronous core path (`openg2p_registry_core` →
`intake_form_data_service.process_submission_register_ingest`) already uses:
match an existing active record by `fayda_fan_id`/`farmer_id` + `crop_year` +
`production_season`, fold into it, and remap child (`TABLE`) rows onto the
resolved parent via `root_record_id_mapping`. Existing rows are updated instead
of raising, so retries are idempotent. Everything else the worker does
(history, documents, outgest fan-out, score computation) is unchanged.

Keep this in sync when bumping `RP_VERSION`: re-extract the base file and
re-apply the changes if the upstream worker changed.

The register-ingest task also uses a dedicated async engine with `NullPool`.
Task modules maintain separate event loops, so the shared `Engine` pool can
return an asyncpg connection created on a different loop after ODK ingestion.
That fails promotion with `Future attached to a different loop`, even after
approval succeeds. A connection is now closed when returned by this task,
preventing reuse across loops without changing the other workers' pools.
