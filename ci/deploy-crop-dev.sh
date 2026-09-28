#!/usr/bin/env bash
# Deploy cropsown-registry to the dev cluster's `crop` namespace with helm.
#
# The Jenkinsfile's Deploy to Dev stage runs this on the vpn-agent2 node with the
# gen2-dev-kubeconfig credential (the crop-ci service account of the RKE2 cluster
# at https://10.0.1.166:6443). It is a script, not inline Groovy, so a deploy by
# hand is the same command:
#
#   KUBECONFIG=<kubeconfig> AWS_ACCOUNT_ID=<id> ./ci/deploy-crop-dev.sh develop-42
#
# What it does, in order:
#   1. Preflight: print the API server and identity; stop at once if the server
#      cannot be reached or the account cannot deploy the namespace.
#   2. helm dependency build.
#   3. Write the values this build owns: image tags, and every public host on
#      <namespace>.openg2p.test (the subchart defaults them to .openg2p.org
#      placeholders, which broke Keycloak, MinIO and IAM).
#   4. helm upgrade --install, layering those values over the live release's own
#      values, so settings made on the release are kept.
#   5. While helm waits on the post-upgrade hooks, print the release's Jobs every
#      minute and save hook pod logs (hook pods are deleted when they fail).
#   6. On failure print those logs, the API pod logs, release history and warning
#      events. On success wait for every Deployment to roll out.
#
# Env:
#   TAG              image tag to deploy (or first argument)       required
#   AWS_ACCOUNT_ID   owner of the ECR registry                      required
#   KUBECONFIG       kubeconfig for the dev cluster                 required
#   AWS_REGION       default ap-south-1
#   ECR_BASE         default gen2/cropsown-registry
#   DASHBOARD_API_ECR default openg2p/cropsown-registry/dashboard-api
#   HELM_RELEASE     default cropsown-registry
#   HELM_NAMESPACE   default crop
#   HELM_CHART_DIR   default helm/openg2p-cropsown-registry
#   BASE_DOMAIN      default <namespace>.openg2p.test (as a helm template)
#   RUN_DB_SEED      true|false, default true   run the db-seed hook Job
#   RUN_SANITY       true|false, default false  run the sanity seed + e2e hook Jobs
#   RUN_IAM_REGISTER true|false, default false  run the IAM registration hook Job
#   SEED_MINIO_ASSETS true|false, default false db-seed also uploads images/templates to MinIO
#   DASHBOARD_API    true|false, default true   deploy the cs_rpt_* reporting views and
#                                               cropsown-registry-dashboard-api (image
#                                               ${DASHBOARD_API_ECR}:<tag>)
#   HELM_TIMEOUT     default 40m
#   ROLLOUT_TIMEOUT  per-Deployment rollout wait, default 420s
#   PULL_SECRET      ECR imagePullSecret to write/use, default cropsown-ecr
set -euo pipefail

TAG="${1:-${TAG:-}}"
[ -n "$TAG" ] || { echo "usage: $0 <image-tag>" >&2; exit 2; }
: "${AWS_ACCOUNT_ID:?set AWS_ACCOUNT_ID}"
: "${KUBECONFIG:?set KUBECONFIG}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
ECR_BASE="${ECR_BASE:-gen2/cropsown-registry}"
DASHBOARD_API_ECR="${DASHBOARD_API_ECR:-openg2p/cropsown-registry/dashboard-api}"
HELM_RELEASE="${HELM_RELEASE:-cropsown-registry}"
HELM_NAMESPACE="${HELM_NAMESPACE:-crop}"
HELM_CHART_DIR="${HELM_CHART_DIR:-helm/openg2p-cropsown-registry}"
# Unset means the dev default; set but EMPTY means "do not touch the release's
# hosts" (staging, whose hosts are not <namespace>.openg2p.test).
BASE_DOMAIN="${BASE_DOMAIN-{{ .Release.Namespace \}\}.openg2p.test}"
RUN_DB_SEED="${RUN_DB_SEED:-true}"
RUN_SANITY="${RUN_SANITY:-false}"
# iam-register takes its token from the PUBLIC Keycloak URL
# (https://keycloak.<namespace>.openg2p.test), which pods cannot reach here, so
# it retries 30 times and holds the deploy. The app is registered already.
RUN_IAM_REGISTER="${RUN_IAM_REGISTER:-false}"
# db-seed's image and template loaders upload to MinIO at global.minioHost, the
# PUBLIC host (minio-api.<namespace>.openg2p.test, https), which pods cannot
# reach here either — locally the same loaders use minio:9000. The host has to
# stay public for the APIs' browser pre-signed URLs, so db-seed skips those two
# loaders by default and loads only SQL metadata, geo data and AWE config.
SEED_MINIO_ASSETS="${SEED_MINIO_ASSETS:-false}"
# The reporting views the dashboard service reads, and the service itself
# (templates/reporting-views*.yaml, templates/dashboard-api.yaml).
DASHBOARD_API="${DASHBOARD_API:-true}"
HELM_TIMEOUT="${HELM_TIMEOUT:-40m}"
ECR="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_BASE}"

NS=(-n "$HELM_NAMESPACE")
WORK="$(mktemp -d)"
BG_PIDS=()
cleanup() {
  [ "${#BG_PIDS[@]}" -eq 0 ] || kill "${BG_PIDS[@]}" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

say() { echo "=== $* ==="; }

# ── 1. Preflight ───────────────────────────────────────────────────────────────
say "Deploy target"
echo "server:    $(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
echo "namespace: ${HELM_NAMESPACE}"
echo "release:   ${HELM_RELEASE}"
echo "images:    ${ECR}/*:${TAG}"

if ! CONN="$(kubectl get --raw /version --request-timeout=15s 2>&1)"; then
  echo "ERROR: cannot connect to the API server:" >&2
  echo "$CONN" | tail -3 >&2
  echo "  timeout = no route on TCP 6443; x509 = wrong CA/address; Unauthorized = bad token." >&2
  exit 1
fi
kubectl auth whoami 2>/dev/null || true

MISSING=""
# Every kind the chart and this script touch. A hand-written Role that covers
# only some of them fails deep inside helm instead — the crop namespace's role
# allowed secrets and deployments but not jobs, then not networkpolicies
# ("cannot get resource networkpolicies", four minutes into an upgrade). A
# namespace-scoped bind to the built-in `admin` ClusterRole covers the lot; see
# ci/k8s/crop-deploy-rbac.yaml.
for CHECK in "list secrets" "create secrets" "create deployments" "create statefulsets" \
             "create jobs" "create cronjobs" "create services" "create configmaps" \
             "create persistentvolumeclaims" "create serviceaccounts" \
             "get networkpolicies" "create networkpolicies" "create pods/exec" \
             "create virtualservices.networking.istio.io"; do
  # can-i exits 1 on "no"; compare the printed answer instead.
  ANSWER="$(kubectl auth can-i ${CHECK} "${NS[@]}" 2>/dev/null || true)"
  echo "can-i ${CHECK}: ${ANSWER:-error}"
  [ "$ANSWER" = "yes" ] || MISSING="${MISSING} '${CHECK}'"
done
if [ -n "$MISSING" ]; then
  echo "ERROR: this account may not${MISSING} in namespace ${HELM_NAMESPACE}." >&2
  echo "  Bind it to the admin ClusterRole in that namespace (see ci/k8s/crop-deploy-rbac.yaml)." >&2
  exit 1
fi

# ── 1b. ECR pull secret ────────────────────────────────────────────────────────
# ECR tokens last 12 hours. The cluster refreshes its own with an ecr-refresh
# CronJob, and when that broke (its ecr-refresh-aws secret was missing) the token
# expired and every pod in the release went ImagePullBackOff — including db-seed,
# which helm then reported as BackoffLimitExceeded. So each deploy writes a fresh
# one: the build agent mints the token (only it has AWS credentials) and stashes
# it as .ecr-token, and the chart is pointed at the secret through
# global.imagePullSecrets. Without the file (a manual run), an existing secret is
# left as it is.
PULL_SECRET="${PULL_SECRET:-cropsown-ecr}"
if [ -r .ecr-token ]; then
  say "Refreshing the ${PULL_SECRET} pull secret"
  ECR_HOST="$(cat .ecr-registry 2>/dev/null || echo "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com")"
  kubectl create secret docker-registry "$PULL_SECRET" "${NS[@]}" \
    --docker-server="$ECR_HOST" --docker-username=AWS \
    --docker-password="$(cat .ecr-token)" \
    --dry-run=client -o yaml | kubectl apply "${NS[@]}" -f -
  rm -f .ecr-token .ecr-registry
  USE_PULL_SECRET=true
  PULL_SECRET_APPLIED=true
elif kubectl get secret "$PULL_SECRET" "${NS[@]}" >/dev/null 2>&1; then
  echo "No .ecr-token in this workspace; keeping the existing ${PULL_SECRET} secret."
  USE_PULL_SECRET=true
else
  echo "No .ecr-token and no ${PULL_SECRET} secret: pods must pull ECR images by node credentials."
  USE_PULL_SECRET=false
fi

# ── 2. Chart dependencies ──────────────────────────────────────────────────────
say "helm dependency build"
helm repo add openg2p https://openg2p.github.io/openg2p-helm >/dev/null 2>&1 || true
helm repo update openg2p >/dev/null
helm dependency build "$HELM_CHART_DIR"

# ── 3. Values this build owns ──────────────────────────────────────────────────
VALUE_FILES=()
if [ -n "$BASE_DOMAIN" ]; then
  cat > "$WORK/ci-hosts.yaml" <<EOF
global:
  registryHostname: '{{ .Release.Name }}.${BASE_DOMAIN}'
  keycloakBaseUrl: 'https://keycloak.${BASE_DOMAIN}'
  minioHost: 'minio-api.${BASE_DOMAIN}'
  idGeneratorHostname: 'idgenerator-{{ .Release.Name }}.${BASE_DOMAIN}'
  aweHostname: 'awe.${BASE_DOMAIN}'
registry:
  staffUi:
    iamPublicUrl: 'https://staff-iam.${BASE_DOMAIN}'
    envVars:
      COOKIE_DOMAIN: '.${BASE_DOMAIN}'
EOF
  VALUE_FILES+=(-f "$WORK/ci-hosts.yaml")
else
  # An environment whose hosts are not <namespace>.openg2p.test (staging) keeps
  # whatever the release already carries.
  echo "BASE_DOMAIN is empty: leaving the release's host values untouched."
fi
# The pull secret goes on the namespace's `default` ServiceAccount, which every
# pod in this release uses, NOT through global.imagePullSecrets: with that value
# set, the idgenerator subchart (0.0.0-develop.42) renders
#   imagePullSecrets:
#     - name: cropsown-ecraffinity:
# because its template appends the next key without a newline, and helm stops on
# "YAML parse error ... mapping values are not allowed in this context". The
# service account route needs no chart support and covers hook Jobs too.
if [ "$USE_PULL_SECRET" = "true" ]; then
  kubectl patch serviceaccount default "${NS[@]}" \
    -p "{\"imagePullSecrets\":[{\"name\":\"${PULL_SECRET}\"}]}" >/dev/null
  echo "default ServiceAccount now pulls with ${PULL_SECRET}"
fi

cat > "$WORK/ci-values.yaml" <<EOF
registry:
  staffApi:
    image: {repository: '${ECR}/staff-api', tag: '${TAG}'}
  staffUi:
    image: {repository: '${ECR}/staff-ui', tag: '${TAG}'}
  partnerApi:
    image: {repository: '${ECR}/partner-api', tag: '${TAG}'}
  celeryWorker:
    image: {repository: '${ECR}/celery', tag: '${TAG}'}
  celeryBeat:
    image: {repository: '${ECR}/celery', tag: '${TAG}'}
  dbSeed:
    enabled: ${RUN_DB_SEED}
    loadImages: ${SEED_MINIO_ASSETS}
    loadTemplates: ${SEED_MINIO_ASSETS}
    image: {repository: '${ECR}/db-seed', tag: '${TAG}'}
  sanity:
    enabled: ${RUN_SANITY}
    image: {repository: '${ECR}/sanity-tests', tag: '${TAG}'}
  iamRegister:
    enabled: ${RUN_IAM_REGISTER}
reporting:
  views:
    enabled: ${DASHBOARD_API}
dashboardApi:
  enabled: ${DASHBOARD_API}
  image: {repository: '${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${DASHBOARD_API_ECR}', tag: '${TAG}'}
EOF
if [ "$DASHBOARD_API" = "true" ] && [ -n "$BASE_DOMAIN" ]; then
  # A private route for developers and tools, on the namespace's internal
  # gateway (the service has no authentication; never a public gateway).
  cat >> "$WORK/ci-values.yaml" <<EOF
  virtualService:
    enabled: true
    host: 'dashboard-api.${BASE_DOMAIN}'
    gateway: internal
EOF
fi
echo "hooks: db-seed=${RUN_DB_SEED} (images/templates=${SEED_MINIO_ASSETS})  sanity=${RUN_SANITY}  iam-register=${RUN_IAM_REGISTER}  reporting-views/dashboard-api=${DASHBOARD_API}"

# The live release's values (hostnames, Keycloak/IAM wiring set on the release).
# Only a missing release — a first install — may go ahead without them.
if ! helm get values "$HELM_RELEASE" "${NS[@]}" -o yaml > "$WORK/current-values.yaml" 2> "$WORK/current.err"; then
  grep -q 'release: not found' "$WORK/current.err" || { cat "$WORK/current.err" >&2; exit 1; }
  echo "No ${HELM_RELEASE} release yet: installing with chart defaults."
  : > "$WORK/current-values.yaml"
fi

# A release left pending by an interrupted run blocks every upgrade with
# "another operation is in progress"; say so plainly instead.
STATUS="$(helm status "$HELM_RELEASE" "${NS[@]}" -o json 2>/dev/null | sed -n 's/.*"status":"\([a-z-]*\)".*/\1/p' | head -1 || true)"
case "$STATUS" in
  pending-*)
    echo "ERROR: release ${HELM_RELEASE} is '${STATUS}' from an interrupted deploy." >&2
    echo "  Roll back to the last good revision: helm -n ${HELM_NAMESPACE} history ${HELM_RELEASE}; helm -n ${HELM_NAMESPACE} rollback ${HELM_RELEASE} <rev>" >&2
    exit 1 ;;
esac

# ── 5. Watchers while helm runs ────────────────────────────────────────────────
# Hook pods are deleted as soon as their Job fails, so save their logs as we go.
# Only this release's own hook pods: <release>-<job>-<5 chars>.
HOOK_POD_RE="^pod/${HELM_RELEASE}-(db-seed|keycloak-init-[0-9]+|sanity(-[a-z]+)*|iam-register|cs-reporting-views)-[a-z0-9]{5}$"
mkdir -p "$WORK/logs"
(
  set +x
  while :; do
    for P in $(kubectl get pods "${NS[@]}" -o name 2>/dev/null | grep -E "$HOOK_POD_RE" || true); do
      N="${P#pod/}"
      kubectl logs "$N" "${NS[@]}" --all-containers --prefix --tail=200 > "$WORK/logs/$N.tmp" 2>/dev/null \
        && mv "$WORK/logs/$N.tmp" "$WORK/logs/$N.log" || rm -f "$WORK/logs/$N.tmp"
      kubectl logs "$N" "${NS[@]}" --all-containers --prefix --tail=200 --previous > "$WORK/logs/$N.prev" 2>/dev/null \
        && mv "$WORK/logs/$N.prev" "$WORK/logs/$N.previous.log" || rm -f "$WORK/logs/$N.prev"
    done
    sleep 10
  done
) &
BG_PIDS+=("$!")

# helm prints nothing while it waits on hooks; show which Job it is waiting on.
(
  set +x
  while :; do
    sleep 60
    echo "--- $(date -u +%H:%M:%S) UTC: ${HELM_RELEASE} jobs (hooks run: db-seed, sanity seeds, iam-register, sanity e2e) ---"
    kubectl get jobs "${NS[@]}" --no-headers 2>/dev/null | grep -E "^${HELM_RELEASE}-" || true
  done
) &
BG_PIDS+=("$!")

# ── 4. Upgrade ─────────────────────────────────────────────────────────────────
say "helm upgrade started $(date -u +%H:%M:%S) UTC (timeout ${HELM_TIMEOUT})"
VALUES=("-f" "$WORK/current-values.yaml" ${VALUE_FILES[@]+"${VALUE_FILES[@]}"} "-f" "$WORK/ci-values.yaml")

# Render exactly what the upgrade applies, as farmer-registry's pipeline does: a
# chart or values mistake then fails here, before anything is changed.
helm template "$HELM_RELEASE" "$HELM_CHART_DIR" "${NS[@]}" "${VALUES[@]}" > "$WORK/rendered.yaml"
echo "rendered $(wc -l < "$WORK/rendered.yaml") lines of manifests"

if ! helm upgrade --install "$HELM_RELEASE" "$HELM_CHART_DIR" "${NS[@]}" \
      "${VALUES[@]}" --timeout "$HELM_TIMEOUT"; then
  kill "${BG_PIDS[@]}" 2>/dev/null || true
  BG_PIDS=()
  for F in "$WORK"/logs/*.log; do
    [ -f "$F" ] || continue
    say "hook pod log: $(basename "$F" .log)" >&2
    cat "$F" >&2
  done
  for D in staff-portal-api partner-api; do
    say "${HELM_RELEASE}-${D} pod log" >&2
    kubectl logs "deploy/${HELM_RELEASE}-${D}" "${NS[@]}" --all-containers --prefix --tail=60 >&2 || true
  done
  say "release history" >&2
  helm history "$HELM_RELEASE" "${NS[@]}" --max 5 >&2 || true
  say "recent warning events" >&2
  kubectl get events "${NS[@]}" --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null | tail -20 >&2 || true
  exit 1
fi
kill "${BG_PIDS[@]}" 2>/dev/null || true
BG_PIDS=()
say "helm upgrade finished $(date -u +%H:%M:%S) UTC"

# ── 6. Rollout ─────────────────────────────────────────────────────────────────
# 180s was not enough for staff-portal-api: its pod runs `migrate` before it
# serves, and the old pod has to finish terminating first ("1 old replicas are
# pending termination" until `timed out waiting for the condition`). On a
# timeout, say which pods are in the way and why, instead of that one line.
DEPLOYMENTS="staff-portal-api staff-portal-ui partner-api celery-worker celery-beat-producer"
[ "$DASHBOARD_API" = "true" ] && DEPLOYMENTS="${DEPLOYMENTS} dashboard-api"
for D in $DEPLOYMENTS; do
  if ! kubectl rollout status "deployment/${HELM_RELEASE}-${D}" "${NS[@]}" --timeout="${ROLLOUT_TIMEOUT:-420s}"; then
    echo "ERROR: ${HELM_RELEASE}-${D} did not roll out within ${ROLLOUT_TIMEOUT:-420s}." >&2
    kubectl get pods "${NS[@]}" -l "app.kubernetes.io/instance=${HELM_RELEASE}" -o wide >&2 || true
    kubectl describe deploy "${HELM_RELEASE}-${D}" "${NS[@]}" 2>&1 | sed -n '/Conditions:/,$p' | head -20 >&2 || true
    echo "--- ${D} pod logs (last 60 lines) ---" >&2
    kubectl logs "deploy/${HELM_RELEASE}-${D}" "${NS[@]}" --all-containers --prefix --tail=60 >&2 || true
    kubectl get events "${NS[@]}" --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null | tail -10 >&2 || true
    exit 1
  fi
done

# ── 7. Schema drift gate ───────────────────────────────────────────────────────
# The API pods run `migrate` on start, which now adds model columns an existing
# table lacks (schema_sync). Check from inside the new staff API pod that every
# model column is really in the database. A green deploy with a missing column
# otherwise shows up days later as a blank page and a SQL error in the pod log.
say "Schema drift check"
if ! kubectl exec "deploy/${HELM_RELEASE}-staff-portal-api" -c staff-portal-api "${NS[@]}" -- \
      python -m openg2p_registry_cropsown_extension.schema_sync --check; then
  echo "ERROR: the database is missing columns the deployed models use (listed above)." >&2
  echo "  The staff-portal-api pod log shows why migrate did not add them." >&2
  exit 1
fi

# ── 8. Dashboard service smoke test ────────────────────────────────────────────
# The reporting hook created the views and the service answers: a 500 from a
# missing cs_rpt_* view fails this deploy, not the dashboards.
if [ "$DASHBOARD_API" = "true" ]; then
  say "dashboard-api smoke test"
  kubectl exec "deploy/${HELM_RELEASE}-dashboard-api" "${NS[@]}" -- python -c "import json, urllib.request as u; base = 'http://127.0.0.1:8000'; [print(p, 'OK', len(json.load(u.urlopen(base + p, timeout=30)))) for p in ('/health', '/api/v1/charts/cropKpis', '/api/v1/charts/cropAreaByCrop', '/api/v1/charts/cropAreaByRegion')]"
fi

say "Deployed ${TAG} to ${HELM_NAMESPACE}"
kubectl get deploy "${NS[@]}" -o custom-columns=NAME:.metadata.name,READY:.status.readyReplicas,IMAGE:.spec.template.spec.containers[0].image \
  | grep -E "^NAME|^${HELM_RELEASE}-" || true

# The hook Jobs' own outcome, as farmer-registry's pipeline prints it: helm only
# says the release succeeded, not whether a Job it waited on had to retry.
for JOB in db-seed sanity iam-register cs-reporting-views; do
  kubectl get job "${HELM_RELEASE}-${JOB}" "${NS[@]}" >/dev/null 2>&1 || continue
  echo "${HELM_RELEASE}-${JOB}: $(kubectl get job "${HELM_RELEASE}-${JOB}" "${NS[@]}" \
    -o jsonpath='{.status.succeeded} succeeded / {.status.failed} failed' 2>/dev/null)"
done
