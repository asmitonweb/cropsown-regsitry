# Crop Sown Registry — Staff Portal API image.
#
# Under the inverted build model the registry-platform publishes the base image
# (platform code + fastapi-common/iam-core runtime + entrypoint/CMD/ENV). This
# image only adds the crop sown domain model on top: the cropsown-extension is
# pip-installed under its own import name (openg2p_registry_cropsown_extension)
# and selected at runtime by REGISTRY_EXTENSION_MODULE (Option C). ENV defaults
# and the migrate+gunicorn CMD are inherited from the base.
#
# RP_VERSION pins the openg2p-registry base image tag. Keep it in lockstep with
# the openg2p-registry chart dependency pinned in helm/openg2p-cropsown-registry/Chart.yaml.
#
# Build context = repo root (so cropsown-extension/ is reachable).
ARG RP_VERSION=1.2.1
FROM openg2p/openg2p-registry-staff-api:${RP_VERSION}

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
