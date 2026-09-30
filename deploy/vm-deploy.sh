#!/bin/sh
# Deploy the CI-published images on a VM. GitHub Actions rsyncs the repo here
# and runs this script (deploy.yml); run it by hand the same way:
#
#   BACKEND_IMAGE=ghcr.io/<owner>/<repo>/backend:<sha> \
#   WEB_IMAGE=ghcr.io/<owner>/<repo>/web:<sha> \
#   GHCR_TOKEN=<pat-with-read:packages>  \
#   sh deploy/vm-deploy.sh
#
# Requires: docker + the compose plugin on the VM (any free-tier VM works:
# Oracle cloud, AWS/GCP/CX host free tier, a home box behind port-forwarding).
set -eu

cd "$(dirname "$0")/.."

BACKEND_IMAGE="${BACKEND_IMAGE:?set BACKEND_IMAGE (ghcr.io/.../backend:<tag>)}"
WEB_IMAGE="${WEB_IMAGE:?set WEB_IMAGE (ghcr.io/.../web:<tag>)}"
export BACKEND_IMAGE WEB_IMAGE

if [ -n "${GHCR_TOKEN:-}" ]; then
  # Private GHCR packages need auth on the VM; public ones pull fine without.
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io \
    --username "${GHCR_USER:-${GITHUB_ACTOR:-consensuskill}}" --password-stdin
fi

echo "pulling $BACKEND_IMAGE and $WEB_IMAGE ..."
docker compose pull redis gateway engine web

echo "starting stack ..."
docker compose up -d --no-build --wait --scale gateway="${GATEWAY_REPLICAS:-2}"

echo "waiting for the edge ..."
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -sf http://127.0.0.1:8080/health
echo

docker compose ps
echo "deploy ok: http://<vm-ip>:8080"
