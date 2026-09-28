#!/usr/bin/env bash
# Run this build's db-seed once on staging, as a plain Kubernetes Job, without
# helm upgrade: the release, its chart, values and hostnames are not changed.
#
# The Job is rendered from this repo's chart (helm template, which changes
# nothing on the cluster) with staging's OWN release values (helm get values), so
# it reaches the same databases, secrets and services the running release uses.
# Only the db-seed image is set, to this build's staging-<n>, so staging gets the
# seed data on this branch.
#
# The Jenkinsfile's "Update staging images" stage runs this after
# ci/staging-set-images.sh. By hand, from the repo root, with the staging kubeconfig:
#
#   KUBECONFIG=<staging kubeconfig> AWS_ACCOUNT_ID=<id> ./ci/staging-run-db-seed.sh staging-12            # dry run
#   KUBECONFIG=<staging kubeconfig> AWS_ACCOUNT_ID=<id> ./ci/staging-run-db-seed.sh staging-12 --apply
#
# What db-seed loads: the registry metadata SQL (rows that exist only report
# "duplicate key"), the AWE approval config and callback secret, and the
# Ethiopia geo hierarchy into master_data (the image always applies it; its
# mnemonics are unique). MinIO image/template uploads, the platform's generic geo
# loader and sample data stay off.
#
# Env:
#   KUBECONFIG       the staging cluster                       required
#   AWS_ACCOUNT_ID   owner of the ECR registry                 required
#   AWS_REGION       default ap-south-1
#   NS               default crop
#   RELEASE          default cropsown-registry
#   CHART            default helm/openg2p-cropsown-registry
#   PULL_SECRET      default cropsown-ecr
#   SEED_TIMEOUT     wait for the Job, default 900s
set -euo pipefail

TAG="${1:?usage: $0 <staging-N tag> [--apply]}"
APPLY=false; [ "${2:-}" = "--apply" ] && APPLY=true
: "${KUBECONFIG:?set KUBECONFIG to the STAGING kubeconfig}"
: "${AWS_ACCOUNT_ID:?set AWS_ACCOUNT_ID}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
NS="${NS:-crop}"
RELEASE="${RELEASE:-cropsown-registry}"
CHART="${CHART:-helm/openg2p-cropsown-registry}"
PULL_SECRET="${PULL_SECRET:-cropsown-ecr}"
SEED_TIMEOUT="${SEED_TIMEOUT:-900s}"
ECR_HOST="$(cat .ecr-registry 2>/dev/null || echo "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com")"
IMAGE_REPO="${ECR_HOST}/gen2/cropsown-registry/db-seed"
JOB="${RELEASE}-db-seed"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

say() { echo "=== $* ==="; }

[ -f "${CHART}/Chart.yaml" ] || { echo "ERROR: run from the repo root (no ${CHART}/Chart.yaml)" >&2; exit 1; }

say "Staging db-seed"
echo "cluster:   $(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
echo "namespace: ${NS}   release: ${RELEASE}   image: ${IMAGE_REPO}:${TAG}   apply: ${APPLY}"
if ! kubectl get --raw /version --request-timeout=15s >/dev/null 2>&1; then
  echo "ERROR: cannot reach the staging API server" >&2
  exit 3
fi

# Only a definite "not found" stops the run: the deploy node has the aws CLI but
# no AWS credentials, and every lookup there fails.
if command -v aws >/dev/null 2>&1; then
  if ! OUT="$(aws ecr describe-images --region "$AWS_REGION" --repository-name gen2/cropsown-registry/db-seed \
        --image-ids imageTag="$TAG" 2>&1 >/dev/null)"; then
    case "$OUT" in
      *ImageNotFoundException*|*RepositoryNotFoundException*)
        echo "ERROR: ${IMAGE_REPO}:${TAG} is not in ECR" >&2; exit 1 ;;
      *)
        echo "warning: could not check ${IMAGE_REPO}:${TAG} in ECR ($(echo "$OUT" | grep -m1 .)); continuing" >&2 ;;
    esac
  fi
fi

# Staging's own values: the same DBs, secrets and hosts as the running release.
helm get values "$RELEASE" -n "$NS" -o yaml > "$WORK/values.yaml"
helm repo add openg2p https://openg2p.github.io/openg2p-helm >/dev/null 2>&1 || true
helm repo update openg2p >/dev/null
helm dependency build "$CHART" >/dev/null

helm template "$RELEASE" "$CHART" -n "$NS" -f "$WORK/values.yaml" \
  --set registry.dbSeed.enabled=true \
  --set registry.dbSeed.image.repository="$IMAGE_REPO" \
  --set registry.dbSeed.image.tag="$TAG" \
  --set registry.dbSeed.loadGeoData=false \
  --set registry.dbSeed.loadSampleData=false \
  --set registry.dbSeed.loadImages=false \
  --set registry.dbSeed.loadTemplates=false \
  --show-only charts/registry/templates/db-seed/job.yaml > "$WORK/job.yaml"

# The Job has no imagePullSecrets of its own (the dev deploy relies on the
# namespace's default ServiceAccount); give it the ECR secret directly.
sed -i "s/^      restartPolicy: OnFailure$/      restartPolicy: OnFailure\n      imagePullSecrets:\n        - name: ${PULL_SECRET}/" "$WORK/job.yaml"
grep -q "name: ${PULL_SECRET}" "$WORK/job.yaml" || { echo "ERROR: could not add imagePullSecrets to the Job" >&2; exit 1; }

echo "--- what it connects to"
grep -E -A1 'name: (PGHOST|PGDATABASE|MD_PGDATABASE|AWE_PGDATABASE|AWE_CALLBACK_CALLER_SERVICE|LOAD_[A-Z_]+)$' "$WORK/job.yaml" \
  | grep -E 'name:|value:' | paste - - | sed 's/ *- name: /  /; s/ *value: / = /'

if ! $APPLY; then
  cp "$WORK/job.yaml" "./db-seed-job-${TAG}.yaml"
  echo; echo "dry run: nothing changed. Rendered Job: ./db-seed-job-${TAG}.yaml. Re-run with --apply."
  exit 0
fi

# The pull secret: ci/staging-set-images.sh has usually just written it. Write
# it here only when this runs on its own.
if [ -r .ecr-token ]; then
  kubectl -n "$NS" create secret docker-registry "$PULL_SECRET" \
    --docker-server="$ECR_HOST" --docker-username=AWS --docker-password="$(cat .ecr-token)" \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f -
elif ! kubectl -n "$NS" get secret "$PULL_SECRET" >/dev/null 2>&1; then
  kubectl -n "$NS" create secret docker-registry "$PULL_SECRET" \
    --docker-server="$ECR_HOST" --docker-username=AWS \
    --docker-password="$(aws ecr get-login-password --region "$AWS_REGION")" \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f -
fi

# A finished Job from an earlier run keeps the name; replace it.
kubectl -n "$NS" delete job "$JOB" --ignore-not-found --wait=true
kubectl -n "$NS" apply -f "$WORK/job.yaml"

say "Waiting for ${JOB} (its init containers wait for the APIs and AWE first)"
# Stop at whichever comes first: Complete, Failed (backoff limit), or the timeout.
RESULT=timeout
END=$(( $(date +%s) + ${SEED_TIMEOUT%s} ))
while [ "$(date +%s)" -lt "$END" ]; do
  C="$(kubectl -n "$NS" get job "$JOB" -o jsonpath='{range .status.conditions[?(@.status=="True")]}{.type}{" "}{end}' 2>/dev/null || true)"
  case " $C " in
    *" Complete "*) RESULT=complete; break ;;
    *" Failed "*)   RESULT=failed;   break ;;
  esac
  sleep 10
done
if [ "$RESULT" = complete ]; then
  kubectl -n "$NS" logs "job/${JOB}" -c db-seed --tail=60 || true
  say "db-seed completed"
else
  echo "ERROR: db-seed did not complete (${RESULT})" >&2
  kubectl -n "$NS" get job "$JOB" -o wide >&2 || true
  kubectl -n "$NS" get pods -l "job-name=${JOB}" >&2 || true
  kubectl -n "$NS" logs "job/${JOB}" --all-containers --prefix --tail=80 >&2 || true
  exit 1
fi
