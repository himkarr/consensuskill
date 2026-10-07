#!/usr/bin/env bash
# Deploy ConsensusKill to Azure Container Apps.
#
#   ./deploy/azure/deploy.sh
#
# Creates (idempotently):
#   resource group            ck-rg
#   Log Analytics workspace   ck-log       (created only if the subscription
#                                          forces one; ~0 cost at this scale)
#   Container Apps environment ck-env     (Consumption plan, free grant)
#   container app             ck-app       (public ingress on :8000)
#
# Region note: Azure for Students pins each subscription to a subset of regions,
# and `az containerapp env create` otherwise auto-generates a Log Analytics
# workspace that can be refused (RequestDisallowedByAzure) even in a region
# Container Apps itself supports. So the script picks a region, then provisions
# the workspace there itself and hands it over with --logs-workspace-id.
# Pass LOCATION= to override, e.g. LOCATION=eastasia ./deploy/azure/deploy.sh
#
# ck-app is ONE revision with TWO containers:
#   app    - FastAPI gateway + embedded round engine + the built React bundle
#   redis  - the round engine's Redis, reachable on 127.0.0.1:6379 only
# Sidecars in a revision share a network namespace, so no extra container app,
# no Azure Cache for Redis and no password to manage. minReplicas=0 means an
# idle site costs nothing.
#
# Override anything through the environment, e.g.
#   ADMIN_TOKEN=$(openssl rand -hex 16) ./deploy/azure/deploy.sh
set -euo pipefail

RG="${RG:-ck-rg}"
ENV_NAME="${ENV_NAME:-ck-env}"
APP_NAME="${APP_NAME:-ck-app}"
# Regions to try, in order. Azure for Students subscriptions are restricted to a
# subset of these; the first that accepts a Log Analytics workspace wins.
# eastasia is early on purpose - it is commonly allowed where India regions are
# not, and it is the closest allowed region to Delhi.
# Order matters: koreacentral is the one region confirmed to accept a workspace on
# this subscription (the others return RequestDisallowedByAzure). An explicit
# LOCATION= is always tried first.
REGION_CANDIDATES="${REGION_CANDIDATES:-${LOCATION:-} koreacentral eastasia centralindia southindia southeastasia westus2 northeurope westeurope japaneast canadacentral ukwest}"
LOCATION=""
LAW_NAME="${LAW_NAME:-ck-log}"
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

env_exists() {
  az containerapp env show --name "$ENV_NAME" --resource-group "$RG" >/dev/null 2>&1
}

law_id() {
  az monitor log-analytics workspace show -g "$RG" -n "$LAW_NAME" --query customerId -o tsv 2>/dev/null || true
}

law_key() {
  az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$LAW_NAME" \
    --query primarySharedKey -o tsv 2>/dev/null || true
}

# Can this subscription create a Log Analytics workspace in $1? That is exactly
# the resource `env create` trips over, and it is the cheapest possible probe.
# The workspace lives in $RG, whose own location is only metadata - Azure lets
# resources sit in regions other than the group's.
region_works() {
  local region="$1" probe err
  probe="ck-probe-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  if ! err=$(az monitor log-analytics workspace create -g "$RG" -n "$probe" -l "$region" 2>&1); then
    # Surface why. "refused" with no reason has cost us three round trips.
    PROBE_ERROR="$(printf '%s' "$err" | grep -m1 -E '^(Code|[A-Za-z]+\(.*\)):|Message:' | tr -d '\r')"
    return 1
  fi
  az monitor log-analytics workspace delete -g "$RG" -n "$probe" --yes >/dev/null 2>&1 || true
  return 0
}

# Create the environment, reusing or provisioning the workspace in $1.
create_env() {
  local region="$1" id key
  id="$(law_id)"
  key="$(law_key)"

  if [ -z "$id" ] || [ -z "$key" ]; then
    log "Log Analytics workspace $LAW_NAME ($region)"
    # 7 days of retention, and a hard daily cap so a noisy demo can never turn
    # into a bill. Ingestion for this workload is a few MB/day at most.
    az monitor log-analytics workspace create -g "$RG" -n "$LAW_NAME" -l "$region" \
      --retention-in-days 7 --daily-quota-gb 0.1 --output none
    id="$(law_id)"
    key="$(law_key)"
    [ -n "$id" ] || return 1
  fi

  az containerapp env create \
    --name "$ENV_NAME" --resource-group "$RG" --location "$region" \
    --logs-workspace-id "$id" --logs-workspace-key "$key" \
    --output none 2>&1 | tail -5
  az containerapp env show --name "$ENV_NAME" --resource-group "$RG" \
    --query name -o tsv >/dev/null 2>&1
}

# Probing happens inside $RG, so it has to exist. Its location is metadata only:
# every resource below is created explicitly in the region we choose.
GROUP_LOCATION="${GROUP_LOCATION:-eastus}"
log "Resource group $RG ($GROUP_LOCATION)"
az group create --name "$RG" --location "$GROUP_LOCATION" --output none

if env_exists; then
  log "Container Apps environment $ENV_NAME"
  LOCATION="$(az containerapp env show -n "$ENV_NAME" -g "$RG" --query location -o tsv)"
  echo "already exists in $LOCATION - keeping it"
else
  log "Finding a region this subscription allows"
  LOCATION=""
  for candidate in $REGION_CANDIDATES; do
    if [ -z "$candidate" ]; then continue; fi
    PROBE_ERROR=""
    printf '  %-16s ' "$candidate"
    if region_works "$candidate"; then
      echo "available"
      LOCATION="$candidate"
      break
    fi
    echo "refused${PROBE_ERROR:+ - $PROBE_ERROR}"
  done
  [ -n "$LOCATION" ] || die "no region on this subscription accepts a Log Analytics workspace.
    Azure for Students restricts which regions you can deploy into, and the
    list is per-subscription. See the troubleshooting note in
    deploy/azure/README.md for how to probe it by hand."

  log "Container Apps environment $ENV_NAME in $LOCATION"
  if ! create_env "$LOCATION"; then
    die "the environment was refused in $LOCATION even though a workspace was
    accepted there. Container Apps may not be offered in that region - re-run
    with REGION_CANDIDATES=\"<other> <other>\" to try others."
  fi
  echo "    created"
fi

# The public edge closes idle HTTP requests after 4 minutes by default. Live
# sockets are safe (the gateway pings every 30s), but bump it anyway so a slow
# room is never killed by the platform. On a Consumption-only environment this
# needs a dedicated workload profile, so a refusal here is informational.
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