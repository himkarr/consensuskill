# Deploying to Azure (Container Apps)

The whole product runs as **one Azure Container App** with two containers:

| container | image | role |
|-----------|-------|------|
| `app` | `Dockerfile.aca` build | FastAPI gateway **+ embedded round engine** + the built React bundle, all on `:8000` |
| `redis` | `redis:7-alpine` | the round engine's Redis, reachable only on `127.0.0.1:6379` |

Why this shape:

* **One origin.** FastAPI serves the SPA (`STATIC_DIR`), answers `/health` for
  the platform probe and upgrades `/ws`. No nginx hop, no CORS, no second
  hostname to keep in sync — the browser just uses `wss://<fqdn>/ws`.
* **Redis is a sidecar.** Containers in a revision share a network namespace,
  so `REDIS_URL=redis://127.0.0.1:6379/0` works. No Azure Cache for Redis
  (which has no free tier), no password to rotate, nothing to expose.
* **Scale to zero.** `minReplicas: 0` means an idle site costs nothing. Live
  sockets count as active requests, so nobody is dropped mid-game.
* **Free grant covers it.** Consumption plan gives 180,000 vCPU-seconds and
  2M requests free per month per subscription. At 0.75 vCPU, that is ~2.7
  hours/day of an active site before anything is billed — and the student's
  $100 credit absorbs the rest.
* **Managed HTTPS.** You get `https://<app>.<region>.azurecontainerapps.io`
  with a Microsoft-managed certificate. Nothing to renew.

Trade-offs, stated plainly: the round engine is in-process, so `maxReplicas`
must stay `1` (the local `docker compose` profile is the one that fans out N
gateway replicas behind nginx); and Redis has no volume, so a scale-from-zero
restart discards rooms that were in progress.

## Prerequisites

1. An Azure for Students subscription (portal.azure.com → the student
   subscription in the top-left picker).
2. The Azure CLI: <https://aka.ms/InstallAzureCLI>, then `az login`.
   Check with `az account show`.
3. A container image Azure can pull — see [Step 2](#step-2-publish-the-image).

## Step 1: one command

```bash
git clone https://github.com/himkarr/consensuskill
cd consensuskill
bash deploy/azure/deploy.sh
```

**Azure Cloud Shell** is the easiest place to run this: `az` is already
installed and already logged in. Cloud Shell starts empty every session, so
clone first. Pick the **Bash** shell (the bash → icon), not PowerShell:

```bash
git clone https://github.com/himkarr/consensuskill
cd consensuskill
bash deploy/azure/deploy.sh
```

> Use `bash deploy/azure/deploy.sh`, not `./deploy/azure/deploy.sh`. A Windows
> clone strips the executable bit, and you get `Permission denied`.

That creates the resource group, the Container Apps environment and the app,
then waits for `/health`. It prints the URL:

```
==> Deployed: https://ck-app.happysky-1234.eastus.azurecontainerapps.io
  health: https://ck-app.happysky-1234.eastus.azurecontainerapps.io/health
  socket: wss://ck-app.happysky-1234.eastus.azurecontainerapps.io/ws
```

Useful overrides:

```bash
LOCATION=westeurope \
IMAGE=ghcr.io/you/consensuskill/web-app:latest \
ADMIN_TOKEN=$(openssl rand -hex 16) \
  bash deploy/azure/deploy.sh
```

`deploy.sh` is idempotent — re-run it after a new image is published and it
rolls a new revision. It touches nothing else in the subscription, and because
the resource group is self-contained, `az group delete -n ck-rg` removes
everything the script created (this is how you stop paying).

## Step 2: publish the image

The script defaults to `ghcr.io/himkarr/consensuskill/web-app:latest`, which
CI publishes on every push to `main`.

**Your own fork.** Push to `main` and let CI build it, then use the tag it
published:

```
https://github.com/<you>/consensuskill/pkgs/container/web-app
```

GitHub packages are **private by default** and Azure pulls anonymously, so open
that package page → *Settings* → *Change visibility* → **Public**. Then:

```bash
IMAGE=ghcr.io/<you>/consensuskill/web-app:latest bash deploy/azure/deploy.sh
```

**Or build it yourself.** No CI involved:

```bash
# a) GHCR (free), needs `docker login ghcr.io` with a PAT (scope write:packages)
GHCR_IMAGE=ghcr.io/<you>/consensuskill/web-app bash deploy/azure/build.sh ghcr

# b) Azure Container Registry - ACR builds in the cloud, no local docker
bash deploy/azure/build.sh acr          # ~$5/month for the Basic registry
```

With an ACR the image is private, so give the app credentials:

```bash
ACR=ckacrrg                        # or your existing registry
IMAGE=$(az acr show -n $ACR --query loginServer -o tsv)/web-app:latest
REGISTRY_SERVER=$(az acr show -n $ACR --query loginServer -o tsv) \
REGISTRY_USERNAME=$ACR \
REGISTRY_PASSWORD=$(az acr credential show -n $ACR --query passwords[0].value -o tsv) \
  bash deploy/azure/deploy.sh
```

Overriding the region:

```bash
LOCATION=eastasia bash deploy/azure/deploy.sh
```

If the script reports `refused` for every region, the subscription is pinned
somewhere unusual. Azure for Students restricts which regions you can deploy
into, and the list is per-subscription — ask Azure which ones you have, or probe
by hand (this is what the script automates; put the workspaces in an existing
group so only the *workspace* location is under test):

```bash
az group create -n probe -l eastus
for r in eastasia centralindia southindia southeastasia westus2 northeurope; do
  echo -n "$r: "; az monitor log-analytics workspace create -g probe -n w-$r -l $r -o none >/dev/null 2>&1 && echo ok || echo no
done
az group delete -n probe --yes
```

Then pass the winner as `LOCATION`.

## Step 3: play

Open the URL. Create a room, then open the same URL on your phone (the room QR
code carries the join link). Everything is `wss://` on the same origin, so
there is nothing else to configure — `VITE_WS_URL` stays empty on purpose.

## Operating it

```bash
# live logs from both containers
az containerapp logs show -g ck-rg -n ck-app --tail 50 --follow

# what the platform thinks is happening
az containerapp show -g ck-rg -n ck-app -o table

# cost watch: consumption meters in the portal under Cost Management
# stop everything (deletes the app, the environment and the image reference)
az group delete -n ck-rg
```

Because `minReplicas` is 0, a site nobody visits costs nothing but the
environment's idle presence. A cold start after ~15 idle minutes takes 20-40
seconds; a warm one is instant.

## Continuous deployment (optional)

Add a service principal (App registration → Certificates & secrets → *New
client secret*; grant it **Container Apps Contributor** on `ck-rg`) and store
three repository secrets:

| secret | value |
|--------|-------|
| `AZURE_CLIENT_ID` | application (client) ID |
| `AZURE_TENANT_ID` | directory (tenant) ID |
| `AZURE_CLIENT_SECRET` | the secret's value |

Pushes to `main` then build, push and roll the new image automatically
(`.github/workflows/deploy.yml`, job `deploy-azure`, behind the `azure`
environment). Without the secrets the job prints a skip notice and nothing is
deployed.

## Why not the other Azure options

| option | verdict |
|--------|---------|
| App Service **F1 free** | capped at **5 concurrent WebSocket connections**. A room of 5 players plus the host would be refused with HTTP 429. Not viable. |
| App Service **B1 for containers** | works, but B1 (~$15/mo each) cannot run Redis, so you need Azure Cache for Redis (~$15/mo) or a second app. ~3x the cost of this setup for the same result. |
| Container Apps (this doc) | one app, Redis included, sits inside the free grant, scales to zero. |
| AKS / Container Instances | billed by the minute with no free grant; far too much for this. |

## Troubleshooting

| symptom | cause / fix |
|---------|-------------|
| `ImagePullBackOff` | private registry without credentials (Step 2), or a typo in `IMAGE`. Check `az containerapp logs show -g ck-rg -n ck-app --type system`. |
| Site loads, socket never connects | the browser console shows the `wss://` URL failing: confirm ingress `transport` is `auto` (never `http2`, which refuses WebSocket upgrades) and that `allowInsecure` is false so you are on `wss://`. |
| `/health` returns `"redis": false` | the sidecar is not up: `az containerapp logs show -g ck-rg -n ck-app --container redis --tail 50`. |
| Room dies after a few idle minutes | a proxy in front is closing silent sockets; the gateway already pings every 30s (`WS_PING_SECONDS`), so raise the environment idle timeout: `az containerapp env update -g ck-rg -n ck-env --request-idle-timeout 30`. |
| `ExpressEnvironmentFeatureNotSupported` | the environment came out as **Express** mode, which forbids sidecar containers. Express also has no TCP ingress and no internal service discovery, so neither a Redis sidecar nor a separate Redis app works there. The script deletes the environment and recreates it in a non-Express mode by itself; to do it by hand: `az containerapp env delete -g ck-rg -n ck-env --yes`, then re-run. |
| Environment created but still Express afterwards | the `environmentMode` property only exists from apiVersion `2026-07-01`. `deploy/azure/environment.json` pins that version on purpose - do not downgrade it, older versions silently drop the property and Azure builds an Express environment. |
| First request after a break takes 30s | normal: the app scaled to zero. Lower nothing; raise `minReplicas` to 1 only if you want instant cold starts (and accept the cost). |

## Presenting

Scale-to-zero means a 20-40s cold start, and you do not want to stand in front
of a class waiting for it. Warm the app first:

```bash
MIN_REPLICAS=1 bash deploy/azure/deploy.sh
```

That keeps one replica (and its Redis sidecar) running, so the URL opens
instantly. Afterwards, `MIN_REPLICAS=0 bash deploy/azure/deploy.sh` goes back to
scale-to-zero.