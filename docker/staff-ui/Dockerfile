# Crop Sown Registry — Staff Portal UI image.
#
# The registry-platform publishes the whole portal; this image changes only its
# branding assets. The portal paints its page background with a Tailwind
# arbitrary value:
#
#     bg-primary-first bg-[url('/images/common/bg_pattern.png')]
#
# so the background is swapped by replacing that file at its fixed path — the
# CSS is compiled into the base image and cannot be pointed elsewhere. The
# theme colour underneath it still comes from registry_theme_values
# (primary_color_1), seeded by the extension.
#
# RP_VERSION pins the openg2p-registry base image tag. Keep it in lockstep with
# the other images and the openg2p-registry chart dependency in
# helm/openg2p-cropsown-registry/Chart.yaml — test/test_rp_pin_lockstep.py
# fails the build if they drift.
#
# Build context = repo root.
ARG RP_VERSION=1.2.1
FROM openg2p/openg2p-registry-staff-ui:${RP_VERSION}

# Browser-facing origin of the dashboard service. It is compiled into the client
# bundle by patch-dashboard-nav.js below, so changing it needs a rebuild.
ARG DASHBOARD_URL=http://dashboard.localtest.me:3021
ARG DASHBOARD_LABEL=Dashboard

# Keep the .png filename: it is hardcoded in the compiled CSS. The bytes are a
# JPEG; browsers sniff the content type, so it renders regardless of extension.
COPY docker/staff-ui/assets/bg_pattern.png /app/public/images/common/bg_pattern.png


# The compiled CSS expects a small tiling pattern and clips the page shell, so
# a photograph needs `cover` and the shell needs to scroll. Tailwind is compiled
# into the base image and cannot be re-run here, so the overrides are appended
# to every emitted stylesheet.
COPY --chown=nextjs docker/staff-ui/assets/brand.css /tmp/brand.css
RUN set -eux; \
    found=0; \
    for css in /app/.next/static/css/*.css; do \
        [ -f "$css" ] || continue; \
        cat /tmp/brand.css >> "$css"; \
        found=$((found+1)); \
    done; \
    [ "$found" -gt 0 ] || { echo "no stylesheets found to patch" >&2; exit 1; }; \
    echo "patched $found stylesheet(s)"

# ── Show every register tab ─────────────────────────────────────────────────
# The portal compiles in a five-tab limit and pushes the rest into a "More"
# dropdown. The crop sown register has six tabs, so Crop Production was hidden.
# The patch script rewrites that bound and fails the build if the expression is
# no longer present, rather than silently reverting to five.
COPY --chown=nextjs docker/staff-ui/assets/patch-tabs.js /tmp/patch-tabs.js
RUN node /tmp/patch-tabs.js

# ── (no geo patches needed) ─────────────────────────────────────────────────
# The portal's master-data proxy already wraps geo calls in the G2P envelope
# (request_header + request_body via the shared backend-request helper) and
# natively targets /geo/get_all_g2p_geo_levels and /geo/get_g2p_geo_level_values,
# which this stack's master-data service exposes. Earlier endpoint/envelope
# patches double-wrapped the body — the service then saw request_payload one
# level too deep and rejected it with "Invalid Input. Field required" — so they
# have been removed.

# ── Add the Dashboard header button ─────────────────────────────────────────
# Puts a Dashboard control immediately left of Configuration, pointing at the
# dashboard-ui service. The dashboard is a separate origin, so the target is
# baked in here rather than resolved as a portal route.
COPY --chown=nextjs docker/staff-ui/assets/patch-dashboard-nav.js /tmp/patch-dashboard-nav.js
RUN DASHBOARD_URL="${DASHBOARD_URL}" DASHBOARD_LABEL="${DASHBOARD_LABEL}" \
    node /tmp/patch-dashboard-nav.js

# ── Replace broken avatar text with default silhouette placeholder ─────────
COPY --chown=nextjs docker/staff-ui/assets/patch-avatar.js /tmp/patch-avatar.js
RUN node /tmp/patch-avatar.js

# ── Enable om (Afaan Oromoo) and ti (Tigrigna) locales ───────────────────────
COPY --chown=nextjs docker/staff-ui/assets/patch-locales.js /tmp/patch-locales.js
RUN node /tmp/patch-locales.js

# ── Remove upgrade-insecure-requests for local dev ──────────────────────────
# Next.js forces HTTPS by default through CSP, which breaks local HTTP loading.
RUN sed -i 's/upgrade-insecure-requests//g' /app/.next/server/middleware.js




