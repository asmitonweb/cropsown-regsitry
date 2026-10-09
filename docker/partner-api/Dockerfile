# Crop Sown Registry — Partner API image.
#
# Thin extension of the registry-platform base image (see
# docker/staff-api/Dockerfile for the full rationale). Adds only the
# crop sown domain model; ENV defaults and the migrate+gunicorn CMD are inherited.
#
# Build context = repo root (so cropsown-extension/ is reachable).
ARG RP_VERSION=1.2.1
FROM openg2p/openg2p-registry-partner-api:${RP_VERSION}

# Option C: select crop sown's domain model (coexists with the base image's
# reference extension; env picks cropsown, no uninstall needed).
ENV REGISTRY_EXTENSION_MODULE=openg2p_registry_cropsown_extension

COPY cropsown-extension/ /app/cropsown-extension/
COPY core_pkg/ /usr/local/lib/python3.12/site-packages/openg2p_registry_core/
# Build-only escape hatch for networks that intercept TLS (corporate proxy, AV,
# some WSL setups): pip then sees a self-signed root CA and cannot verify
# pypi.org. Empty by default, so CI and Helm builds are unchanged. Locally:
#   PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org" docker compose build
# The scoped assignment keeps it out of the runtime image.
ARG PIP_TRUSTED_HOST=
RUN PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}" pip install --no-cache-dir /app/cropsown-extension
