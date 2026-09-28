#!/usr/bin/env bash
# Deploy a Crop Sown Registry build from ECR into the dev cluster.
#
# This is the body of the Jenkinsfile's 'Deploy to Dev' stage, pulled out so the
# same deploy can be run by hand — after a build held at the ECR push with
# DEV_DEPLOY=false, or to roll the namespace back to an earlier build. Jenkins
# supplies the tag and the credentials; everything else defaults to what the
# pipeline uses, so a manual run lands exactly where a pipeline run would.
#
# It deploys whatever cluster KUBECONFIG points at. In Jenkins that is the
# staging-farmer-kubeconfig credential, used from the vpn-agent2 node (the
# cluster farmer-registry's far namespace is on); by hand
# it is yours, so the context is printed before anything is changed.
#
# The images must already be in ECR — this builds nothing.
#
# ci/deploy-staging.sh runs this same deploy under the staging release name, so a
# change here reaches both environments.
#
# Usage:
#   ./ci/deploy-dev.sh <tag>
#   TAG=<tag> ./ci/deploy-dev.sh
#
#   AWS_ACCOUNT_ID=123456789012 ./ci/deploy-dev.sh develop-42
#
# Prefer a build-numbered tag (develop-42) over the moving branch tag (develop).
# A release already on `develop` renders the same manifests again, so helm has
# nothing to roll and the pods keep running whatever image they last pulled.
#
# Env:
#   AWS_ACCOUNT_ID  account that owns the ECR registry (or set ECR_REGISTRY)
#   ECR_REGISTRY    full registry host; overrides AWS_ACCOUNT_ID + AWS_REGION
#   AWS_REGION      default ap-south-1
#   ECR_BASE        default gen2/cropsown-registry
#   NAMESPACE       default crop
#   RELEASE_NAME    default cropsown-registry
#   CHART_DIR       default helm/openg2p-cropsown-registry
#   KUBECONFIG      the cluster to deploy to
#   CREATE_NAMESPACE false to deploy without helm's --create-namespace, for an
#                   account that may not manage namespaces (default true)
#   HELM_VERSION    helm to fetch when none is on PATH (default v4.2.4)
#   KUBECTL_VERSION kubectl to fetch when none is on PATH (default v1.36.1)
#   TOOLS_DIR       where fetched tools are kept (default <repo>/.tools)
#
# Requires: helm and kubectl, or — on Linux — curl, tar and sha256sum to fetch
# them.
#
# Exit status:
#   0  deployed
#   2  usage
#   3  the API server could not be reached over the network — nothing was
#      touched. The Jenkinsfile turns this one into an UNSTABLE build rather
#      than a failed one: the images built and pushed, and what is missing is a
#      route from the agent to the cluster, not a fix to the code.
#   1  anything else — a refused login, a bad chart, a helm error
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TAG="${1:-${TAG:-}}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
ECR_BASE="${ECR_BASE:-gen2/cropsown-registry}"
NAMESPACE="${NAMESPACE:-crop}"
RELEASE_NAME="${RELEASE_NAME:-cropsown-registry}"
CHART_DIR="${CHART_DIR:-helm/openg2p-cropsown-registry}"
HELM_VERSION="${HELM_VERSION:-v4.2.4}"
KUBECTL_VERSION="${KUBECTL_VERSION:-v1.36.1}"
TOOLS_DIR="${TOOLS_DIR:-$REPO_ROOT/.tools}"

die()  { echo "ERROR: $*" >&2; exit 1; }
note() { echo "=== $* ==="; }

# The Jenkins build agent carries docker and the aws CLI but neither helm nor
# kubectl, and the first deploy from it died on "helm not found on PATH" after
# the images had built and pushed. Nothing in this repo can install software on
# that node, so a missing tool is fetched instead: a pinned release, checked
# against its published sha256, into TOOLS_DIR. Each version gets its own
# directory, so the download happens once per workspace and a pin bump fetches
# afresh. A tool already on PATH always wins.
fetch_arch() {
  [ "$(uname -s)" = "Linux" ] || die "$1 not found on PATH; install it (fetching is Linux-only)"
  command -v curl >/dev/null || die "$1 not found on PATH, and no curl to fetch it"
  case "$(uname -m)" in
    x86_64|amd64)  echo amd64 ;;
    aarch64|arm64) echo arm64 ;;
    *) die "$1 not found on PATH, and no $1 build for $(uname -m)" ;;
  esac
}

ensure_helm() {
  command -v helm >/dev/null && return
  local dir="$TOOLS_DIR/helm-$HELM_VERSION"
  if [ ! -x "$dir/helm" ]; then
    local arch tgz tmp
    arch="$(fetch_arch helm)"
    tgz="helm-$HELM_VERSION-linux-$arch.tar.gz"
    note "helm not on PATH — fetching $HELM_VERSION"
    tmp="$(mktemp -d)"
    curl -fsSL --retry 3 -o "$tmp/$tgz" "https://get.helm.sh/$tgz"
    curl -fsSL --retry 3 -o "$tmp/$tgz.sha256sum" "https://get.helm.sh/$tgz.sha256sum"
    (cd "$tmp" && sha256sum -c --quiet "$tgz.sha256sum") || die "helm $HELM_VERSION failed its checksum"
    tar -xzf "$tmp/$tgz" -C "$tmp"
    mkdir -p "$dir"
    mv "$tmp/linux-$arch/helm" "$dir/helm"
    rm -rf "$tmp"
  fi
  PATH="$dir:$PATH"
}

ensure_kubectl() {
  command -v kubectl >/dev/null && return
  local dir="$TOOLS_DIR/kubectl-$KUBECTL_VERSION"
  if [ ! -x "$dir/kubectl" ]; then
    local arch url tmp
    arch="$(fetch_arch kubectl)"
    url="https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/$arch/kubectl"
    note "kubectl not on PATH — fetching $KUBECTL_VERSION"
    tmp="$(mktemp -d)"
    curl -fsSL --retry 3 -o "$tmp/kubectl" "$url"
    curl -fsSL --retry 3 -o "$tmp/kubectl.sha256" "$url.sha256"
    # The published file is the bare hash, without the filename sha256sum wants.
    (cd "$tmp" && echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c --quiet) \
      || die "kubectl $KUBECTL_VERSION failed its checksum"
    chmod +x "$tmp/kubectl"
    mkdir -p "$dir"
    mv "$tmp/kubectl" "$dir/kubectl"
    rm -rf "$tmp"
  fi
  PATH="$dir:$PATH"
}

if [ -z "$TAG" ]; then
  echo "usage: $0 <tag>" >&2
  echo "   or: TAG=<tag> $0" >&2
  exit 2
fi

if [ -z "${ECR_REGISTRY:-}" ]; then
  [ -n "${AWS_ACCOUNT_ID:-}" ] \
    || die "set AWS_ACCOUNT_ID (aws sts get-caller-identity --query Account --output text) or ECR_REGISTRY"
  ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
fi

# Settle the tools before touching the cluster, not halfway through.
ensure_helm
ensure_kubectl
[ -f "$CHART_DIR/Chart.yaml" ] || die "no chart at $CHART_DIR"

note "Deploying ${RELEASE_NAME} to namespace ${NAMESPACE}"
echo "helm:     $(helm version --short 2>/dev/null || echo '?') ($(command -v helm))"
# sed, not head: head exits after one line, kubectl dies of SIGPIPE writing its
# second, and pipefail turns that into a failure that appended a stray "?".
echo "kubectl:  $(kubectl version --client 2>/dev/null | sed -n 1p || echo '?') ($(command -v kubectl))"
echo "context:  $(kubectl config current-context 2>/dev/null || echo '(none)')"
echo "images:   ${ECR_REGISTRY}/${ECR_BASE}/*:${TAG}"

# Reach the API server before helm does. helm's own failure ("kubernetes cluster
# unreachable ... i/o timeout") only arrives after the repo and dependency steps,
# and says nothing about why — and for dev the why is nearly always the network:
# that API server answers only over the openg2p-Gen2 WireGuard VPN.
server="$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null || true)"
echo "server:   ${server:-(none in KUBECONFIG)}"
# Before the connect check: with no server, kubectl falls back to localhost:8080
# and reports a refused connection — which would pass for a network problem.
[ -n "$server" ] || die "KUBECONFIG names no cluster (KUBECONFIG=${KUBECONFIG:-unset})"
if ! err="$(kubectl get --raw /version --request-timeout=20s 2>&1 >/dev/null)"; then
  # Only a failure to CONNECT gets exit 3. An API server that answers and
  # refuses (Unauthorized, a bad certificate) is a broken credential, which is
  # a real failure and keeps exit 1.
  if grep -Eqi 'i/o timeout|deadline exceeded|Client\.Timeout|connection refused|was refused|no route to host|network is unreachable|no such host' <<<"$err"; then
    echo "ERROR: cannot reach the Kubernetes API at ${server:-<none>} from $(hostname): ${err}" >&2
    echo "  Nothing was deployed; the images are in ECR under :${TAG}. The dev cluster" >&2
    echo "  is reachable only over the openg2p-Gen2 WireGuard VPN — run this from a" >&2
    echo "  machine on it (in Jenkins, the vpn-agent2 node), then re-run the build." >&2
    exit 3
  fi
  die "the Kubernetes API at ${server:-<none>} rejected the request: ${err}"
fi

# The wrapper chart owns no templates; every manifest comes from the pinned
# openg2p-registry subchart, so the dependency must be present before install.
helm repo add openg2p https://openg2p.github.io/openg2p-helm || true
helm repo update openg2p || true
helm dependency build "./${CHART_DIR}"

# Point one chart component at an image built by this pipeline.
#
# NB every path is UNDER the subchart alias (registry.*), sanity included — see
# CHART_IMAGE_PATHS in .gitlab-ci.yml. A top-level `sanity.image.*` writes a key
# the chart never reads, so the sanity Job would silently keep the values.yaml
# default tag.
SETS=()
set_image() {
  SETS+=(--set "registry.$1.image.repository=${ECR_REGISTRY}/${ECR_BASE}/$2"
         --set "registry.$1.image.tag=${TAG}")
}
set_image staffApi     staff-api
set_image partnerApi   partner-api
set_image celeryWorker celery
set_image celeryBeat   celery
set_image dbSeed       db-seed
set_image sanity       sanity-tests

# An account scoped to one namespace (farmer-ci, see ci/k8s/crop-deploy-rbac.yaml)
# cannot create or look up namespaces, so CREATE_NAMESPACE=false leaves the flag
# off and relies on the namespace already existing.
NS_FLAG=()
[ "${CREATE_NAMESPACE:-true}" = "false" ] || NS_FLAG=(--create-namespace)

helm upgrade --install "${RELEASE_NAME}" "./${CHART_DIR}" \
  --namespace "${NAMESPACE}" \
  ${NS_FLAG[@]+"${NS_FLAG[@]}"} \
  --timeout 10m \
  "${SETS[@]}"

note "Waiting for rollout"
# staff-portal-api, not staff-api: the subchart sets
# staffApi.nameOverride=staff-portal-api, so that is the Deployment the chart
# actually creates. The old name matched nothing, and `|| true` swallowed the
# error — this step reported success without ever waiting for a rollout.
kubectl rollout status "deployment/${RELEASE_NAME}-staff-portal-api" \
  -n "${NAMESPACE}" --timeout=120s || true

note "Deployment status"
kubectl get pods -n "${NAMESPACE}" | grep "${RELEASE_NAME}" || true
