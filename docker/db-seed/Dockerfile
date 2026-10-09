# Crop Sown Registry — DB-seed image.
#
# Thin extension of the registry-platform db-seed base image. The base ships all
# the generic seeding MACHINERY (entrypoint.sh, load/upload scripts, the shared
# openg2p-data clone). This image adds ONLY crop sown's seed content: the SQL
# metadata, AWE metadata and Jinja templates carried by the cropsown-extension,
# plus the crop sown sub-table sample JSON. The entrypoint (inherited) reads
# /seed/* at container-run time; behaviour is toggled entirely via env from the
# Helm Job.
#
# Build context = repo root (so cropsown-extension/ and docker/db-seed/ are both
# reachable).
ARG RP_VERSION=1.2.1
FROM openg2p/openg2p-registry-db-seed:${RP_VERSION}

# The RP db-seed image ships the reference registry's seed; clear it so only
# crop sown's seed is applied.
RUN rm -rf /seed/meta_data/* /seed/awe_meta_data/* /seed/templates/* /seed/seed-data/*

COPY cropsown-extension/src/openg2p_registry_cropsown_extension/meta_data/     /seed/meta_data/
COPY cropsown-extension/src/openg2p_registry_cropsown_extension/awe_meta_data/ /seed/awe_meta_data/
COPY cropsown-extension/src/openg2p_registry_cropsown_extension/templates/     /seed/templates/
COPY docker/db-seed/sample-data/                                               /seed/sample-data/

# The real Ethiopia region/zone/woreda/kebele hierarchy for the master data DB.
# Only the docker-compose stack ever applied it (by hand, see
# docker/local-dev/geo-seed/import_ethiopia_geo.py), so deployed clusters kept
# the generic sample hierarchy from /openg2p-data and the geo dropdowns were
# wrong. entrypoint-cropsown.sh runs the platform's seeding, then applies this.
COPY docker/local-dev/geo-seed/ethiopia_geo_seed.sql/                          /seed/geo-seed/
COPY docker/db-seed/entrypoint-cropsown.sh                                     /seed/entrypoint-cropsown.sh
RUN chmod +x /seed/entrypoint-cropsown.sh

# Reporting layer read by cropsown-registry-dashboard-api: the cs_rpt_* views.
# Applied and refreshed by the chart's reporting jobs
# (templates/reporting-views.yaml), not by the seed entrypoint.
COPY docker/db-seed/reporting_views.sql                                        /seed/reporting_views.sql
ENTRYPOINT ["/seed/entrypoint-cropsown.sh"]
