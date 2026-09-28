// Crop Sown Registry — build, push to ECR, deploy to Kubernetes.
//
//   develop  → build + push → deploy to dev      (release cropsown-registry, namespace crop)
//   staging  → build + push → move staging's app Deployments to the new images
//              (kubectl set image; no helm upgrade, hostnames untouched)
//   other    → build + push only
//
// Shaped after farmer-registry's Jenkinsfile: one linear pipeline, one Deploy
// stage pinned to the `vpn-agent2` node — the only node that reaches either API
// server — and no build parameters. What the deploy does is chosen by the branch
// below and by ci/deploy-crop-dev.sh's own defaults, so there is nothing to tick
// before a build, and a deploy by hand is the same command:
//
//   KUBECONFIG=<kubeconfig> AWS_ACCOUNT_ID=<id> ./ci/deploy-crop-dev.sh develop-42
//
// Two deliberate differences from farmer-registry's file, both because crop is
// built differently:
//
//   - There is no root Dockerfile with per-component targets here. Each image
//     builds from docker/<name>/Dockerfile with the repo root as context, which
//     is what docker-compose.yml and .gitlab-ci.yml build too.
//   - RP_VERSION is read from docker/staff-api/Dockerfile rather than pinned in
//     this file. The pin already lives in the Dockerfiles, the chart dependency
//     and local/.env, and test/test_rp_pin_lockstep.py guards that those agree —
//     a copy here would be a fourth place to update and the only one no test
//     watches.

pipeline {
    agent any

    environment {
        AWS_REGION     = 'ap-south-1'
        ECR_BASE       = 'gen2/cropsown-registry'
        // The dashboard service follows the openg2p/<registry>/<image> convention.
        DASHBOARD_API_ECR = 'openg2p/cropsown-registry/dashboard-api'

        HELM_NAMESPACE = 'crop'
        HELM_CHART_DIR = 'helm/openg2p-cropsown-registry'

        // Built from docker/<name>/Dockerfile. dashboard-ui is left out: the
        // chart deploys no dashboard, so its image would be published for nothing.
        SERVICES = 'staff-api staff-ui partner-api celery db-seed sanity-tests'

        // cropsown-registry-dashboard-api lives in its own (public) repository and
        // is built here beside the registry images, as dashboard-api. Each branch
        // builds the dashboard-api branch of the same name and falls back to
        // develop where there is none. Set DASHBOARD_API_REF on the job (a branch
        // or tag) to pin one instead.
        DASHBOARD_API_REPO = 'https://github.com/Centre-for-Open-Societal-Systems/cropsown-registry-dashboard-api.git'

        // How long helm may wait on the chart's post-upgrade hook Jobs, kept WELL
        // inside the Deploy stage's own 60-minute timeout. At the script's 40m
        // default the two nearly met: a build whose hooks failed spent 40 minutes
        // in helm, began printing why, and was cut off mid-diagnostic by the stage
        // timeout — which ends a build ABORTED, the same word Jenkins prints when
        // a person presses Stop, and skips post{failure}, so nobody was told.
        // 20m leaves the script half an hour to roll out, or to explain itself.
        HELM_TIMEOUT = '20m'

        DEVOPS_EMAILS = 'simretyibeltal@gmail.com, pavanns.ns@gmail.com'
    }

    options {
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '30'))
        timeout(time: 150, unit: 'MINUTES')
        // Two deploys at once leave the release locked ("another operation is in
        // progress"); a second build waits for the first instead.
        disableConcurrentBuilds()
    }

    // develop and staging poll for new commits; every other branch registers no
    // trigger, so a topic branch builds only when someone asks for it.
    triggers {
        pollSCM(['develop', 'staging'].contains(env.BRANCH_NAME) ? 'H/5 * * * *' : '')
    }

    stages {
        stage('Checkout') {
            steps { checkout scm }
        }

        stage('Checkout dashboard-api') {
            steps {
                script {
                    // A PR build (BRANCH_NAME PR-<n>) matches on its source branch.
                    def ref = env.DASHBOARD_API_REF
                    if (!ref) {
                        def wanted = env.CHANGE_BRANCH ?: env.BRANCH_NAME
                        def found = wanted && sh(returnStatus: true,
                            script: "git ls-remote --exit-code --heads ${DASHBOARD_API_REPO} 'refs/heads/${wanted}' > /dev/null") == 0
                        ref = found ? wanted : 'develop'
                    }
                    // .build/ is git-ignored, so the clone never enters a commit.
                    sh "rm -rf .build/dashboard-api && git clone --quiet --depth 1 --branch '${ref}' ${DASHBOARD_API_REPO} .build/dashboard-api"
                    if (!fileExists('.build/dashboard-api/Dockerfile')) {
                        error("dashboard-api ${ref} has no Dockerfile: merge the service into that branch of ${DASHBOARD_API_REPO}, or set DASHBOARD_API_REF")
                    }
                    env.DASHBOARD_API_REF_USED = ref
                    env.DASHBOARD_API_SHA = sh(returnStdout: true, script: 'git -C .build/dashboard-api rev-parse --short=12 HEAD').trim()
                    echo "dashboard-api: ${ref} @ ${env.DASHBOARD_API_SHA}"
                }
            }
        }

        stage('Guard: openg2p-registry pin lockstep') {
            // Images built FROM one platform version while the chart pulls a
            // subchart expecting another is invisible until the deploy fails.
            // The check is stdlib-only, so it needs no pip on the agent.
            steps {
                sh '''
                    set -eu

                    # The pin check is stdlib-only by design, so a missing pytest is
                    # not a reason to fail the build — run it directly instead.
                    #
                    # This stage used to bootstrap pytest and send the install error
                    # to /dev/null, which turned an agent without python3-pip into a
                    # build failure reading "No module named pytest": the symptom,
                    # never the cause. Ubuntu 22.04 ships python3 with no pip and
                    # ensurepip stripped out, so that agent could not self-heal.
                    if python3 -c 'import pytest' 2>/dev/null; then
                        python3 -m pytest test/test_rp_pin_lockstep.py -q
                    else
                        echo "note: pytest unavailable on this agent — running the guard directly"
                        python3 test/test_rp_pin_lockstep.py
                    fi
                '''
            }
        }

        stage('Resolve version') {
            steps {
                script {
                    env.RP_VERSION = sh(
                        script: "sed -n 's/^ARG RP_VERSION=//p' docker/staff-api/Dockerfile | head -1",
                        returnStdout: true
                    ).trim()
                    if (!env.RP_VERSION) {
                        error 'could not read ARG RP_VERSION from docker/staff-api/Dockerfile'
                    }
                    env.IMAGE_TAG = "${env.BRANCH_NAME}-${env.BUILD_NUMBER}"
                    echo "openg2p-registry ${env.RP_VERSION}, image tag ${env.IMAGE_TAG}"
                }
            }
        }

        stage('Build & Push') {
            steps {
                // AWS_ACCOUNT_ID stays inside this block: a bound credential is
                // masked in the log only where it is in scope.
                withCredentials([string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID')]) {
                    sh '''
                        set -eu
                        ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

                        aws ecr get-login-password --region "${AWS_REGION}" \
                            | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

                        for SVC in ${SERVICES}; do
                            IMAGE="${ECR_REGISTRY}/${ECR_BASE}/${SVC}"
                            echo "--- ${SVC} -> ${IMAGE}:${IMAGE_TAG} ---"

                            # --pull, not --no-cache: unchanged layers are reused,
                            # but a moved base tag is still refreshed.
                            docker build --pull \
                                --build-arg RP_VERSION="${RP_VERSION}" \
                                -f "docker/${SVC}/Dockerfile" \
                                -t "${IMAGE}:${IMAGE_TAG}" -t "${IMAGE}:${BRANCH_NAME}" .

                            # ECR does not create repositories on push.
                            aws ecr describe-repositories --region "${AWS_REGION}" \
                                --repository-names "${ECR_BASE}/${SVC}" >/dev/null 2>&1 \
                              || aws ecr create-repository --region "${AWS_REGION}" \
                                    --repository-name "${ECR_BASE}/${SVC}" >/dev/null

                            docker push "${IMAGE}:${IMAGE_TAG}"
                            docker push "${IMAGE}:${BRANCH_NAME}"

                            # Drop the build tag, keep the branch tag: it is the
                            # next build's cache.
                            docker rmi "${IMAGE}:${IMAGE_TAG}" || true
                        done

                        # The dashboard service, from its own repository (cloned by
                        # 'Checkout dashboard-api'), with its own build context.
                        IMAGE="${ECR_REGISTRY}/${DASHBOARD_API_ECR}"
                        echo "--- dashboard-api (${DASHBOARD_API_REF_USED} @ ${DASHBOARD_API_SHA}) -> ${IMAGE}:${IMAGE_TAG} ---"
                        docker build --pull \
                            --label org.opencontainers.image.source="${DASHBOARD_API_REPO}" \
                            --label org.opencontainers.image.revision="${DASHBOARD_API_SHA}" \
                            --label org.opencontainers.image.ref.name="${DASHBOARD_API_REF_USED}" \
                            -f .build/dashboard-api/Dockerfile \
                            -t "${IMAGE}:${IMAGE_TAG}" -t "${IMAGE}:${BRANCH_NAME}" .build/dashboard-api
                        docker push "${IMAGE}:${IMAGE_TAG}"
                        docker push "${IMAGE}:${BRANCH_NAME}"
                        docker rmi "${IMAGE}:${IMAGE_TAG}" || true

                        # An ECR pull token for the deploy node to write into the
                        # namespace's imagePullSecret. The cluster's own
                        # ecr-refresh CronJob is broken, its 12-hour token expired,
                        # and every pod in the release went ImagePullBackOff —
                        # which helm reported only as the db-seed hook failing with
                        # BackoffLimitExceeded. This agent is the one with AWS
                        # credentials, so the token is minted here and carried over
                        # in the stash below.
                        umask 077
                        aws ecr get-login-password --region "${AWS_REGION}" > .ecr-token
                        echo "${ECR_REGISTRY}" > .ecr-registry
                    '''
                }
            }
        }

        stage('Stash deploy') {
            // The deploy node needs the chart, the scripts and the pull token —
            // not the build context, and not a checkout of its own: it deploys
            // exactly this commit without needing GitHub access.
            steps {
                stash name: 'deploy', includes: "ci/**,${HELM_CHART_DIR}/**,.ecr-token,.ecr-registry"
                // Stashed; keep the credential out of the build agent's workspace.
                sh 'rm -f .ecr-token .ecr-registry'
            }
        }

        stage('Deploy') {
            // develop only. Staging is never helm-upgraded by CI: its automatic
            // helm upgrade replaced staging's live release (chart, hosts, a
            // db-seed against its data) and took the site down. A staging build
            // updates images only, in the next stage.
            //
            // beforeAgent: decide before asking for vpn-agent2, so a build of any
            // other branch never waits on that node.
            when {
                beforeAgent true
                branch 'develop'
            }
            agent { label 'vpn-agent2' }
            options {
                // Bounded, because an offline vpn-agent2 does not fail a build —
                // it QUEUES it, to the 150-minute pipeline timeout. 60 covers the
                // wait for the node, helm's 20 minutes of hooks, the rollout waits
                // and the diagnostics on failure.
                timeout(time: 60, unit: 'MINUTES')
            }
            environment {
                // The dev cluster (RKE2 at 10.0.1.166), as its crop-ci service
                // account.
                KUBECONFIG_CREDENTIAL = 'gen2-dev-kubeconfig'
                // The subchart rejects release names over 18 characters, and
                // cropsown-registry is 17.
                HELM_RELEASE          = 'cropsown-registry'
                // Dev's public hosts are <namespace>.openg2p.test; the subchart
                // defaults them to .openg2p.org placeholders, which breaks
                // Keycloak, MinIO and IAM.
                BASE_DOMAIN           = '{{ .Release.Namespace }}.openg2p.test'
            }
            steps {
                // The workspace outlives builds, and nothing cleans a file this
                // commit deleted out of a stash-only checkout.
                sh 'rm -rf ci helm'
                unstash 'deploy'
                withCredentials([
                    string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID'),
                    file(credentialsId: env.KUBECONFIG_CREDENTIAL, variable: 'KUBECONFIG')
                ]) {
                    // catchInterruptions: if the deploy outlives the stage timeout
                    // above, FAIL the build instead of letting the interrupt
                    // propagate. An uncaught one ends the build ABORTED, which
                    // reads as "a person pressed Stop" and skips post{failure}, so
                    // the hang is never mailed — that is how a run ends with
                    // nothing in Post Actions but `docker image prune`. The cost:
                    // a real Stop pressed DURING a deploy now reads FAILED. A Stop
                    // at any other point still reads ABORTED.
                    catchError(buildResult: 'FAILURE', stageResult: 'FAILURE', catchInterruptions: true) {
                        sh 'bash ci/deploy-crop-dev.sh "${IMAGE_TAG}"'
                    }
                }
            }
        }

        stage('Update staging images') {
            // staging only, and never helm upgrade, so staging's release, chart,
            // values and hostnames are untouched:
            //   1. ci/staging-set-images.sh moves each app Deployment to this
            //      build's staging-<n> image (kubectl set image), and undoes them
            //      all if a rollout fails;
            //   2. ci/staging-run-db-seed.sh then runs this build's db-seed as a
            //      plain Job, rendered with staging's own release values, so the
            //      seed data on this branch reaches staging's databases.
            // A db-seed failure fails the build; the new images stay.
            when {
                beforeAgent true
                branch 'staging'
            }
            agent { label 'vpn-agent2' }
            options {
                // Five rollouts at up to 7 minutes each, db-seed's 15, and the wait
                // for the node.
                timeout(time: 60, unit: 'MINUTES')
            }
            steps {
                sh 'rm -rf ci helm'
                unstash 'deploy'
                withCredentials([
                    string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID'),
                    file(credentialsId: 'staging-rke2-kubeconfig', variable: 'KUBECONFIG')
                ]) {
                    // As in Deploy: a timeout FAILS the build rather than ending it
                    // ABORTED, so the failure mail goes out.
                    catchError(buildResult: 'FAILURE', stageResult: 'FAILURE', catchInterruptions: true) {
                        sh 'bash ci/staging-set-images.sh "${IMAGE_TAG}" --apply'
                        sh 'bash ci/staging-run-db-seed.sh "${IMAGE_TAG}" --apply'
                    }
                }
            }
        }
    }

    post {
        success {
            script {
                mailQuietly(
                    to: notifyList(),
                    subject: "✅ Build SUCCESS: ${env.JOB_NAME} #${env.BUILD_NUMBER}",
                    body: """
Crop Sown Registry build succeeded.

Job:        ${env.JOB_NAME}
Branch:     ${env.BRANCH_NAME}
Build:      #${env.BUILD_NUMBER}
Image tag:  ${env.IMAGE_TAG}
Platform:   openg2p-registry ${env.RP_VERSION}
URL:        ${env.BUILD_URL}

Regards,
Jenkins
"""
                )
            }
        }
        failure {
            script {
                mailQuietly(
                    to: notifyList(),
                    subject: "❌ Build FAILED: ${env.JOB_NAME} #${env.BUILD_NUMBER}",
                    body: """
Crop Sown Registry build or deploy failed.

Job:        ${env.JOB_NAME}
Branch:     ${env.BRANCH_NAME}
Build:      #${env.BUILD_NUMBER}
Image tag:  ${env.IMAGE_TAG}
URL:        ${env.BUILD_URL}

Console:    ${env.BUILD_URL}console

If the images built and pushed they are in ECR under
gen2/cropsown-registry/*:${env.IMAGE_TAG}, and the deploy can be re-run by hand
from vpn-agent2 with ci/deploy-crop-dev.sh.

Regards,
Jenkins
"""
                )
            }
        }
        always {
            sh 'docker image prune -f || true'
            withCredentials([string(credentialsId: 'AWS_ACCOUNT_ID', variable: 'AWS_ACCOUNT_ID')]) {
                sh 'docker logout "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com" || true'
            }
        }
    }
}

// The DevOps list, plus the commit author unless it is a noreply address.
def notifyList() {
    def email = sh(script: "git log -1 --pretty=format:'%ae'", returnStdout: true).trim()
    def recipients = env.DEVOPS_EMAILS.split(',').collect { it.trim() }
    if (email && !email.contains('noreply')) {
        recipients = [email] + recipients
    }
    return recipients.unique().join(', ')
}

// A controller without SMTP must not turn a green build red.
def mailQuietly(Map args) {
    try {
        mail(args)
    } catch (err) {
        echo "notification not sent: ${err.message}"
    }
}
