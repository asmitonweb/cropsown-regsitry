# Crop Sown Registry — Celery image (workers + beat).
#
# Thin extension of the registry-platform celery base image (see
# docker/staff-api/Dockerfile for the full rationale). Adds only the
# crop sown domain model; both celery packages, the run_celery.py wrapper, the
# CELERY_* ENV defaults and the CMD are inherited from the base. A single image
# runs as worker or beat, selected at runtime via CELERY_APP.
#
# Build context = repo root (so cropsown-extension/ is reachable).
ARG RP_VERSION=1.2.1
FROM openg2p/openg2p-registry-celery:${RP_VERSION}

# Option C: select crop sown's domain model (coexists with the base image's
# reference extension; env picks cropsown, no uninstall needed).
ENV REGISTRY_EXTENSION_MODULE=openg2p_registry_cropsown_extension

COPY cropsown-extension/ /app/cropsown-extension/
COPY core_pkg/ /usr/local/lib/python3.12/site-packages/openg2p_registry_core/
# Overlay the base image's intake-form ingest worker with the parent-resolving
# fix (see docker/celery/patches/README.md). docker-compose mounts this for local
# dev; baking it here makes CI/Helm-built images carry the same fix. Keep in sync
# when bumping RP_VERSION.
COPY docker/celery/patches/intake_form_register_ingest_worker.py /usr/local/lib/python3.12/site-packages/openg2p_registry_celery_worker/tasks/intake_form_register_ingest_worker.py
# Build-only escape hatch for networks that intercept TLS (corporate proxy, AV,
# some WSL setups): pip then sees a self-signed root CA and cannot verify
# pypi.org. Empty by default, so CI and Helm builds are unchanged. Locally:
#   PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org" docker compose build
# The scoped assignment keeps it out of the runtime image.
ARG PIP_TRUSTED_HOST=
RUN PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}" pip install --no-cache-dir /app/cropsown-extension
