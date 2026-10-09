# Crop Sown Registry — sanity/e2e image (thin extension of the platform suite).
#
# The registry-platform publishes openg2p-registry-sanity-tests: the whole test
# HARNESS (signing, DCI envelope building, PM/CM/Keycloak/AWE seeding, DB helpers,
# step logging, conftest banners) plus the extension-INDEPENDENT tests (Set 1:
# smoke + the fail-closed negative cases) and the entrypoint. All of that is
# inherited from the base image and runs unchanged here.
#
# This image overlays ONLY the Crop Sown Registry's field-specific parts (Set 2):
#   - sanity/fixtures.py   the seeded record + the crop sown register tables
#   - sanity/data_seed.py  injection into g2p_register_crop_sowns
#   - tests/test_e2e_dci.py            crop sown DCI template assertions
#   - tests/test_e2e_change_request.py crop sown register/history tables
# These overwrite the reference registry's versions at the same paths, so the
# inherited flow tests now exercise the Crop Sown Registry's fields. The register
# id, DCI reg-type, search text, scopes and CR tab/section are supplied as env
# by the Helm chart (sanity.* values), not baked here.
#
# RP_VERSION pins the base sanity image; keep it in lockstep with the other
# crop sown images and the openg2p-registry chart dependency.
#
# Build context = repo root.
ARG RP_VERSION=1.2.1
FROM openg2p/openg2p-registry-sanity-tests:${RP_VERSION}

COPY test/sanity/sanity/fixtures.py                /app/sanity/fixtures.py
COPY test/sanity/sanity/data_seed.py               /app/sanity/data_seed.py
COPY test/sanity/tests/test_e2e_dci.py             /app/tests/test_e2e_dci.py
COPY test/sanity/tests/test_e2e_change_request.py  /app/tests/test_e2e_change_request.py
