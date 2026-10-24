# Crop Sown ODK configuration and acceptance

Crop Sown has four independent ODK pipelines: Planning, Cultivation, Sowing,
and Harvesting. Successful intake is only the first part of acceptance: verify
staff approval, the AWE callback, and asynchronous register promotion too.

## Configuration contract

- Set the ODK base URL, project, and published form ID for the target
  environment. A historical 404 in Connector Admin does not establish that the
  current form endpoint is missing; probe the configured OData endpoint.
- Use `CSR_DATA_MODEL`, sender `CropSown`, and register mnemonic `CropSown`.
  Partner Management normalizes the sender to `PARTNER_CROPSOWN`. Onboard and
  approve that partner using the supported Partner Management API. Keep its
  dedicated private key in a Kubernetes Secret, outside source control.
- The connector needs the native registry response envelope: the correlation
  ID is at `response_body.response_payload.correlation_id` and status at
  `response_header.response_status`. Keep the CSR data model's
  `response_template_document_id` NULL. The separate DCI model retains its DCI
  response template. The metadata seed repairs an existing CSR row as well as
  initializing new installations.
- The Celery ODK finalizer needs the same `KEYCLOAK_BASE_URL`,
  `KEYCLOAK_REALM`, `AUTH_CLIENT_ID`, and `AUTH_CLIENT_SECRET` as the staff API
  to acquire its AWE bearer token. Source the secret from the existing portal
  client Secret. Configuring only `REGISTRY_CELERY_WORKERS_AWE_*` does not
  supply those credentials to the extension's token helper.
- Preserve the configured AWE internal URL, callback URL, callback secret ID,
  and HMAC Secret. Worker auth is configured in both the wrapper chart and the
  dev deployment script's generated override.

The dedicated partner key does not add outbound signing to the connector;
signing enforcement must be assessed separately when enabled in an environment.

## Fresh-only recovery

Before modifying an existing pipeline, snapshot its nonsecret configuration,
cursor, pause state, and run counts. Pause the four pipelines while repairing
configuration. Preserve ODK credentials, mappings, and historical runs.

For an explicitly authorized fresh-only test, first verify that the OData API
accepts a submission-date filter. Set a common UTC boundary before uploading
new fixtures, then resume each pipeline with a timestamp checkpoint:

```json
{
  "_checkpoint": {
    "mode": "timestamp",
    "value": "<UTC cutoff>",
    "boundary_ids": [],
    "extra": {"field": "__system/submissionDate"}
  }
}
```

Do not clear idempotency keys, reset cursors, or replay old failures as part of
a fresh-only test. Preserve later cursor progress during any rollback so that
recovery does not unexpectedly reingest historical submissions.

## Approval and register-worker repair

The worker overlay in
`docker/celery/patches/intake_form_register_ingest_worker.py` already resolves
stage intakes onto an existing master. It also needs its own nonpooled async
engine: other task modules use separate event loops, and a pooled asyncpg
connection from the shared engine can belong to another loop. The failure
appears after approval as `Future attached to a different loop` in the
submission's register-ingest error field. The dedicated `NullPool` engine
closes each connection on return while leaving other task pools unchanged.
See [SQLAlchemy's multiple-event-loop guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-multiple-asyncio-event-loops).

Bake the overlay into the Celery image using the existing Dockerfile. A
temporary ConfigMap overlay can validate a repair on dev, but it must be
removed when an image containing the fix is deployed. Otherwise, its mounted
file continues to override newer image code.

## Complete acceptance procedure

1. Create clearly marked synthetic fixtures from the actual published forms.
   Use one new farmer/year/season and land ID across all four stages. Use
   valid identifiers, catalog values, dates, and positive areas. Preserve the
   same instance ID if checking or retrying an upload to prevent duplication.
2. Upload Planning through ODK Central with an approved submission-capable
   credential. Confirm HTTP success and exactly one Central submission.
3. Poll the connector without resetting its cursor. Require connector
   `SUCCESS`, a nonempty registry correlation ID, processed transformation and
   ingestion, and one `FINAL/PENDING` intake with an AWE request.
4. Use the normal staff portal OIDC login and an authorized approver. A direct
   `admin-cli` token may authenticate but omit the portal/AWE client roles.
   Decide the intake's actual open AWE task through
   `/awe/submit_task_decision`; require envelope `SUCCESS` as well as HTTP 200.
   Do not bypass the workflow by setting approval flags in SQL.
5. Wait for the callback and worker. Require `APPROVED`, register-ingest
   `PROCESSED`, no last error, one `ACTIVE` master with a generated functional
   ID, and one linked active planning line.
6. Repeat for Cultivation, then Sowing, then Harvesting. Sowing/Harvesting
   require the active master and preceding stage records; keep these checks
   enabled. Verify every stage attaches to the same master.
7. Poll again. Require no duplicate Central submissions, connector runs,
   intakes, masters, or stage lines. Check that historical run counts have not
   increased and that the last poll has no error.

Validate with the deployed application's APIs and read-only database checks.
Postman CLI can run matching saved requests when available; a CLI installation
blocked by its managed Private Cloud configuration does not establish live
success or failure. Record the alternative HTTP assertions and resulting
database states explicitly.

## Dev repair verification, 2026-10-19

The dev repair uses the existing four forms in ODK project 2. The accepted
fresh-only boundary was established before creating the synthetic submissions.
Old failed submissions were left in place. Only new test records were approved.

| Fresh stage | Central | Connector | AWE / intake | Register |
| --- | --- | --- | --- | --- |
| Planning | One submission | SUCCESS | APPROVED / FINAL | PROCESSED, one active line |
| Cultivation | One submission | SUCCESS | APPROVED / FINAL | PROCESSED, one active line |
| Sowing | One submission | SUCCESS | APPROVED / FINAL | PROCESSED, one active line |
| Harvesting | One submission | SUCCESS | APPROVED / FINAL | PROCESSED, one active line |

All four lines link to one active master with a generated functional ID and
final lifecycle `HARVESTING`. The fixture's crop areas remained 1 hectare and
harvest quantity 20 quintals. Normal portal OIDC authentication and the
provided administrator's existing AWE permission were used for decisions.
Follow-up polls all succeeded with zero fetched records; each fixture retained
one connector run and one intake. All 39 historical connector runs retained
their original counts and were not updated after the fresh-only boundary.
Exact dev application references and IDs are retained in the private local
acceptance evidence rather than this deployment-independent runbook.

Validation includes a read-only reproduction of the default pool's loop
failure, four successful reads alternating between two event loops using the
patched engine initialization, and rendering the pinned Helm dependency with
four assertions on worker auth values and Secret wiring. The CSR metadata SQL
was executed twice against a temporary table in a rolled-back transaction:
CSR was repaired and DCI remained unchanged.

Runtime dev fixes currently include worker auth environment entries and a
temporary `cropsown-register-ingest-dev-20261019` ConfigMap mounted over the
worker module. Source changes must be built and deployed to persist these
repairs. After deploying that image, remove only the corresponding
`register-ingest-dev-fix` mount and volume, roll the worker, verify the image's
module and auth settings, then remove the unused ConfigMap. Do not roll back
an entire deployment revision, which could also revert the auth repair.

Partner onboarding, private-key storage, and connector cursor state are runtime
configuration and are not promoted to staging by merging this repository.
Configure staging independently with its own endpoints, approved partner,
credentials, and fresh-only boundary where appropriate.
