#!/usr/bin/env bash
# Deploy ConsensusKill to Azure Container Apps.
#
#   ./deploy/azure/deploy.sh
#
# Creates (idempotently):
#   resource group            ck-rg
#   Container Apps environment ck-env     (Consumption plan, free grant)
#   container app             ck-app       (public ingress on :8000)
#
# ck-app is ONE revision with TWO containers:
#   app    - FastAPI gateway + embedded round engine + the built React bundle
#   redis  - the round engine's Redis, reachable on 127.0.0.1:6379 only
# Sidecars in a revision share a network namespace, so no extra container app,
# no Azure Cache for Redis and no password to manage. minReplicas=0 means an
# idle site costs nothing.
#
# Override anything through the environment, e.g.
#   LOCATION=westeurope ADMIN_TOKEN=$(openssl rand -hex 16) ./deploy/azure/deploy.sh
set -euo pipefail

RG="${RG:-ck-rg}"
ENV_NAME="${ENV_NAME:-ck-env}"
APP_NAME="${APP_NAME:-ck-app}"
LOCATION="${LOCATION:-eastus}"
# Public image published by .github/workflows/deploy.yml (anonymous GHCR pull).
# Point it at your own registry, or build it with deploy/azure/build.sh.
IMAGE="${IMAGE:-ghcr.io/himkarr/consensuskill/web-app:latest}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-*}"
# Guards POST /admin/kill. Empty leaves the demo endpoint open to the internet.
ADMIN_TOKEN="${ADMIN_TOKEN:-}"
# Only needed for a private registry (deploy/azure/build.sh acr). Public images
# are pulled anonymously and these stay empty.
REGISTRY_SERVER="${REGISTRY_SERVER:-}"
REGISTRY_USERNAME="${REGISTRY_USERNAME:-}"
REGISTRY_PASSWORD="${REGISTRY_PASSWORD:-}"

log() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v az >/dev/null 2>&1 || die "Azure CLI not found: https://aka.ms/InstallAzureCLI"
az account show >/dev/null 2>&1 || die "run 'az login' first"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$HERE/containerapp.json"
[ -f "$TEMPLATE" ] || die "missing template $TEMPLATE"

log "Using subscription $(az account show --query name -o tsv)"

# A brand-new subscription has never registered these. Container Apps wires
# Log Analytics into every managed environment, so `env create` dies on
# Microsoft.OperationalInsights if it is missing. Registering is idempotent
# and free; a wrong region on a provider is not, so the location is unset.
for provider in Microsoft.App Microsoft.OperationalInsights Microsoft.Insights; do
  state=$(az provider show --namespace "$provider" --query registrationState -o tsv 2>/dev/null || echo Unregistered)
  if [ "$state" != Registered ]; then
    log "Registering $provider"
    az provider register --namespace "$provider" --wait --only-show-errors
  fi
done

log "Resource group $RG ($LOCATION)"
az group create --name "$RG" --location "$LOCATION" --output none

env_exists() {
  az containerapp env show --name "$ENV_NAME" --resource-group "$RG" >/dev/null 2>&1
}

create_env() {
  # $1 = region. Returns non-zero (and explains) if this region is refused.
  local region="$1" err
  if ! err=$(az containerapp env create \
        --name "$ENV_NAME" --resource-group "$RG" --location "$region" \
        --output none 2>&1); then
    if printf '%s' "$err" | grep -qi 'RequestDisallowedByAzure\|not available\|not eligible\|restricted'; then
      echo "    $region is not available on this subscription"
    else
      echo "$err" | tail -5
    fi
    return 1
  fi
  echo "    created in $region"
  return 0
}

log "Container Apps environment $ENV_NAME"
if env_exists; then
  echo "already exists - keeping it"
elif ! create_env "$LOCATION"; then
  # Azure for Students pins a small set of regions per subscription. Fall back
  # to any region the subscription actually offers rather than dying.
  echo "retrying with a region this subscription allows..."
  found=""
  for candidate in $(az account list-locations \
      --query "[?not(contains(name, 'preview')) && not(contains(name,'edge'))].name" \
      -o tsv 2>/dev/null); do
    [ -n "$candidate" ] || continue
    printf '  trying %s\n' "$candidate"
    if create_env "$candidate"; then
      LOCATION="$candidate"
      found="$candidate"
      break
    fi
  done
  [ -n "$found" ] || die "no region available for Container Apps on this subscription.
    Check the list yourself with:
      az account list-locations --query \"[?not(contains(name,'preview'))].name\" -o tsv
    then re-run with: LOCATION=<one-of-them> ./deploy/azure/deploy.sh"
  log "Using $LOCATION"
  # The app and its Redis sidecar must live in the environment's region.
  az group create --name "$RG" --location "$LOCATION" --output none
fi
# The public edge closes idle HTTP requests after 4 minutes by default. Live
# sockets are safe (the gateway pings every 30s), but bump it anyway so a slow
# room is never killed by the platform.
# On a Consumption-only environment this switch needs a dedicated workload
# profile, so a refusal here is informational, not fatal.
az containerapp env update --name "$ENV_NAME" --resource-group "$RG" \
  --request-idle-timeout 30 --output none 2>/dev/null || \
  echo "note: kept the default 4-minute ingress idle timeout (sockets are pinged every 30s)"

log "Deploying $APP_NAME from $IMAGE"
# -o tsv --query prints just the FQDN; deployment errors still hit stderr.
if ! FQDN=$(az deployment group create \
  --resource-group "$RG" \
  --name "ck-$(date +%s)" \
  --template-file "$TEMPLATE" \
  --parameters \
      appName="$APP_NAME" \
      environmentName="$ENV_NAME" \
      location="$LOCATION" \
      image="$IMAGE" \
      allowedOrigins="$ALLOWED_ORIGINS" \
      adminToken="$ADMIN_TOKEN" \
      registryServer="$REGISTRY_SERVER" \
      registryUsername="$REGISTRY_USERNAME" \
      registryPassword="$REGISTRY_PASSWORD" \
  --query properties.outputs.fqdn.value \
  --output tsv); then
  die "deployment failed - re-run with: az deployment group create --resource-group $RG --template-file $TEMPLATE --verbose"
fi

[ -n "$FQDN" ] || die "deployment produced no FQDN"

URL="https://$FQDN"
log "Waiting for the first replica (scale-from-zero can take ~30s)"
for _ in $(seq 1 30); do
  if curl -fsS "$URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

log "Deployed: $URL"
echo "  health: $URL/health"
echo "  socket: wss://$FQDN/ws"
echo "  logs:   az containerapp logs show -g $RG -n $APP_NAME --tail 50"
echo
curl -fsS "$URL/health" && echo || echo "note: /health not answering yet - check the logs above"