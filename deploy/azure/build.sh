#!/usr/bin/env bash
# Build the single-container image and push it somewhere Azure can pull it.
#
# Two routes, both ending in the same deploy step:
#
#   1. GHCR (free, no Azure cost, recommended when the repo is public)
#        ./deploy/azure/build.sh ghcr
#      -> ghcr.io/<owner>/<repo>/web-app:latest
#      Needs `docker login ghcr.io` with a GitHub PAT (scope: write:packages).
#
#   2. Azure Container Registry (no local Docker needed, ~$5/month Basic)
#        ./deploy/azure/build.sh acr
#      -> <registry>.azurecr.io/web-app:latest
#      ACR builds the context in the cloud, so no docker on your machine.
#      Pass ACR_NAME=myregistry to reuse an existing registry.
#
# Then:  IMAGE=<pushed-image> ./deploy/azure/deploy.sh
set -euo pipefail

MODE="${1:-ghcr}"
TAG="${TAG:-latest}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKERFILE="Dockerfile.aca"

command -v az >/dev/null 2>&1 || { echo "Azure CLI required: https://aka.ms/InstallAzureCLI" >&2; exit 1; }
az account show >/dev/null 2>&1 || { echo "run 'az login' first" >&2; exit 1; }

case "$MODE" in
ghcr)
    command -v docker >/dev/null 2>&1 || { echo "docker required" >&2; exit 1; }
    [ -n "${GHCR_IMAGE:-}" ] || {
        echo "set GHCR_IMAGE=ghcr.io/<owner>/<repo>/web-app" >&2
        exit 1
    }
    docker build -f "$REPO_ROOT/$DOCKERFILE" -t "$GHCR_IMAGE:$TAG" "$REPO_ROOT"
    docker push "$GHCR_IMAGE:$TAG"
    echo
    echo "IMAGE=$GHCR_IMAGE:$TAG ./deploy/azure/deploy.sh"
    ;;
acr)
    ACR_NAME="${ACR_NAME:-}"
    RG="${RG:-ck-rg}"
    if [ -z "$ACR_NAME" ]; then
        ACR_NAME="ck$(echo "$RG" | tr -cd 'a-z0-9')"
        az acr create --resource-group "$RG" --name "$ACR_NAME" --sku Basic \
          --output none
        az acr update -n "$ACR_NAME" --admin-enabled true --output none
    fi
    LOGIN_SERVER=$(az acr show -n "$ACR_NAME" --query loginServer -o tsv)
    # `az acr build` uploads the context and builds it in Azure - no local docker.
    az acr build --registry "$ACR_NAME" --image "$LOGIN_SERVER/web-app:$TAG" "$REPO_ROOT"
    echo
    echo "The container app needs credentials to pull a private ACR image."
    echo "Grant the environment's managed identity AcrPull, then:"
    echo "  IMAGE=$LOGIN_SERVER/web-app:$TAG ./deploy/azure/deploy.sh"
    ;;
*)
    echo "usage: $0 [ghcr|acr]" >&2
    exit 1
    ;;
esac