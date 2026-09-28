#!/usr/bin/env bash
# Put a staging-<n> build's images on staging's running Deployments, and change
# nothing else. No helm upgrade: the release, its chart, its values and its
# hostnames (crop-registry.oanstaging.com, ...) stay exactly as they are, and no
# hook Job (db-seed, keycloak-init, sanity) runs.
#
# That is deliberate. A staging build once ran helm upgrade on the live release:
# it switched the chart, wrote the dev hostnames and ran db-seed against staging's
# data, and the site returned 404 until the release was rolled back.
#
# The Jenkinsfile's "Update staging images" stage runs this after every staging
# build. By hand, with the staging kubeconfig:
#
#   KUBECONFIG=<staging kubeconfig> AWS_ACCOUNT_ID=<id> ./ci/staging-set-images.sh staging-12            # dry run
#   KUBECONFIG=<staging kubeconfig> AWS_ACCOUNT_ID=<id> ./ci/staging-set-images.sh staging-12 --apply
#
# What --apply does:
#   1. Writes the ECR pull secret (cropsown-ecr) in the namespace, from the token
#      the build stashed (.ecr-token), or from `aws ecr get-login-password` by hand.
#   2. Adds that secret to each Deployment's imagePullSecrets and sets its MAIN
#      container to the ECR image at <tag>. Init containers are left alone.
#   3. Waits for every rollout. If any fails, it undoes every Deployment it
#      changed, so staging goes back to the images it had, and exits 1.
#
# The new API images run their database migrations when they start.
#
# Undo by hand: kubectl -n crop rollout undo deploy/<name>
#           or: helm -n crop rollback cropsown-registry <current revision>
#
# Env:
#   KUBECONFIG       the staging cluster                       required
#   AWS_ACCOUNT_ID   owner of the ECR registry                 required
#   AWS_REGION       default ap-south-1
#   NS               default crop
#   RELEASE          default cropsown-registry
#   PULL_SECRET      default cropsown-ecr
#   ROLLOUT_TIMEOUT  per Deployment, default 420s
set -euo pipefail

TAG="${1:?usage: $0 <staging-N tag> [--apply]}"
APPLY=false; [ "${2:-}" = "--apply" ] && APPLY=true
: "${KUBECONFIG:?set KUBECONFIG to the STAGING kubeconfig}"
: "${AWS_ACCOUNT_ID:?set AWS_ACCOUNT_ID}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
NS="${NS:-crop}"
RELEASE="${RELEASE:-cropsown-registry}"
PULL_SECRET="${PULL_SECRET:-cropsown-ecr}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-420s}"
ECR_HOST="$(cat .ecr-registry 2>/dev/null || echo "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com")"
ECR="${ECR_HOST}/gen2/cropsown-registry"

# Deployment suffix -> ECR image the Jenkinsfile builds.
MAP="staff-portal-api=staff-api
partner-api=partner-api
staff-portal-ui=staff-ui
celery-worker=celery
celery-beat-producer=celery"

say() { echo "=== $* ==="; }
run() { if $APPLY; then "$@"; else echo "  would run: $*"; fi; }

say "Staging image update"
echo "cluster:   $(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
echo "namespace: ${NS}   release: ${RELEASE}   tag: ${TAG}   apply: ${APPLY}"
if ! kubectl get --raw /version --request-timeout=15s >/dev/null 2>&1; then
  echo "ERROR: cannot reach the staging API server" >&2
  exit 3
fi

# Check every image exists before changing anything. Only a definite "not found"
# stops the run: the deploy node has the aws CLI but no AWS credentials, and every
# lookup there fails, though the build pushed the images moments before.
check_image() {  # <repo suffix>
  local OUT
  if OUT="$(aws ecr describe-images --region "$AWS_REGION" --repository-name "gen2/cropsown-registry/$1" \
        --image-ids imageTag="$TAG" 2>&1 >/dev/null)"; then
    return 0
  fi
  case "$OUT" in
    *ImageNotFoundException*|*RepositoryNotFoundException*)
      echo "ERROR: ${ECR}/$1:${TAG} is not in ECR" >&2; exit 1 ;;
    *)
      echo "warning: could not check ${ECR}/$1:${TAG} in ECR ($(echo "$OUT" | grep -m1 .)); continuing" >&2 ;;
  esac
}
if command -v aws >/dev/null 2>&1; then
  for PAIR in $MAP; do check_image "${PAIR#*=}"; done
fi

say "Pull secret ${PULL_SECRET}"
if [ -r .ecr-token ]; then
  TOKEN_CMD="cat .ecr-token"
else
  TOKEN_CMD="aws ecr get-login-password --region ${AWS_REGION}"
fi
if $APPLY; then
  kubectl -n "$NS" create secret docker-registry "$PULL_SECRET" \
    --docker-server="$ECR_HOST" --docker-username=AWS \
    --docker-password="$($TOKEN_CMD)" \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f -
  rm -f .ecr-token .ecr-registry
else
  echo "  would write secret ${PULL_SECRET} for ${ECR_HOST} (token from: ${TOKEN_CMD})"
fi

CHANGED=()
for PAIR in $MAP; do
  D="${RELEASE}-${PAIR%%=*}"
  IMG="${ECR}/${PAIR#*=}:${TAG}"
  if ! kubectl -n "$NS" get deploy "$D" >/dev/null 2>&1; then
    echo "--- ${D}: not in ${NS}, skipped"
    continue
  fi
  C="$(kubectl -n "$NS" get deploy "$D" -o jsonpath='{.spec.template.spec.containers[0].name}')"
  OLD="$(kubectl -n "$NS" get deploy "$D" -o jsonpath='{.spec.template.spec.containers[0].image}')"
  echo "--- ${D} [${C}]"
  echo "  from ${OLD}"
  echo "  to   ${IMG}"
  if [ "$OLD" = "$IMG" ]; then
    echo "  already on ${TAG}"
    continue
  fi
  HAS="$(kubectl -n "$NS" get deploy "$D" -o jsonpath="{.spec.template.spec.imagePullSecrets[?(@.name=='${PULL_SECRET}')].name}")"
  if [ -z "$HAS" ]; then
    # Append to an existing list; create the list only when there is none.
    if [ -n "$(kubectl -n "$NS" get deploy "$D" -o jsonpath='{.spec.template.spec.imagePullSecrets}')" ]; then
      PATCH="[{\"op\":\"add\",\"path\":\"/spec/template/spec/imagePullSecrets/-\",\"value\":{\"name\":\"${PULL_SECRET}\"}}]"
    else
      PATCH="[{\"op\":\"add\",\"path\":\"/spec/template/spec/imagePullSecrets\",\"value\":[{\"name\":\"${PULL_SECRET}\"}]}]"
    fi
    run kubectl -n "$NS" patch deploy "$D" --type=json -p "$PATCH"
  fi
  run kubectl -n "$NS" set image "deploy/${D}" "${C}=${IMG}"
  CHANGED+=("$D")
done

if ! $APPLY; then
  echo; echo "dry run: nothing changed. Re-run with --apply."
  exit 0
fi

if [ "${#CHANGED[@]}" -eq 0 ]; then
  say "Nothing to change: staging already runs ${TAG}"
  exit 0
fi

say "Waiting for rollouts"
FAILED=""
for D in "${CHANGED[@]}"; do
  kubectl -n "$NS" rollout status "deploy/${D}" --timeout="$ROLLOUT_TIMEOUT" || FAILED="${FAILED} ${D}"
done

if [ -n "$FAILED" ]; then
  echo "ERROR: did not roll out:${FAILED}" >&2
  for D in $FAILED; do
    echo "--- ${D} pod logs (last 40 lines)" >&2
    kubectl -n "$NS" logs "deploy/${D}" --all-containers --prefix --tail=40 >&2 || true
  done
  kubectl -n "$NS" get events --field-selector type=Warning --sort-by=.lastTimestamp 2>/dev/null | tail -10 >&2 || true
  say "Undoing every Deployment this run changed" >&2
  for D in "${CHANGED[@]}"; do
    kubectl -n "$NS" rollout undo "deploy/${D}" >&2 || true
  done
  for D in "${CHANGED[@]}"; do
    kubectl -n "$NS" rollout status "deploy/${D}" --timeout="$ROLLOUT_TIMEOUT" >&2 || true
  done
  exit 1
fi

say "Staging runs ${TAG}"
kubectl -n "$NS" get deploy -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image,READY:.status.readyReplicas \
  | grep -E "^NAME|^${RELEASE}-" || true
