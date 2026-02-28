# Athena Flask + Cloud Run + Firestore POC

Minimal Flask API deployed to Cloud Run using only GitHub Actions (no Terraform).

## What this includes

- Flask app with `/health`, `/`, and `/track` (writes to Firestore)
- GitHub Actions workflow at `.github/workflows/deploy-cloud-run.yml`
- Cloud Run deploy with low-cost defaults:
  - `min-instances=0`
  - `max-instances=1`
  - `memory=256Mi`

## Prerequisites

1. A GitHub repository with this code pushed.
2. A GCP project with billing enabled.
3. `gcloud` installed locally for one-time setup.

## One-time GCP setup for GitHub Actions (OIDC)

Run this locally once (replace placeholder values first):

```bash
PROJECT_ID="your-gcp-project-id"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
REGION="us-central1"
GITHUB_ORG="your-github-org-or-username"
GITHUB_REPO="your-repo-name"
POOL_ID="github-pool"
PROVIDER_ID="github-provider"
CI_SA="github-cloudrun-deployer"
RUNTIME_SA="athena-cloudrun-runtime"

gcloud config set project "$PROJECT_ID"

# Required APIs
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  iamcredentials.googleapis.com \
  firestore.googleapis.com

# Runtime service account (used by Cloud Run container)
gcloud iam service-accounts create "$RUNTIME_SA" \
  --display-name="Athena Cloud Run Runtime" || true
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

# CI deployer service account (impersonated by GitHub OIDC)
gcloud iam service-accounts create "$CI_SA" \
  --display-name="GitHub Cloud Run Deployer" || true

# Least required deploy perms for CI SA
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CI_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.admin"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CI_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.editor"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${CI_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.admin"
gcloud iam service-accounts add-iam-policy-binding \
  "${RUNTIME_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --member="serviceAccount:${CI_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

# Workload Identity Federation pool + provider
gcloud iam workload-identity-pools create "$POOL_ID" \
  --project="$PROJECT_ID" \
  --location="global" \
  --display-name="GitHub Actions Pool" || true

gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --project="$PROJECT_ID" \
  --location="global" \
  --workload-identity-pool="$POOL_ID" \
  --display-name="GitHub Provider" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='${GITHUB_ORG}/${GITHUB_REPO}'" || true

gcloud iam service-accounts add-iam-policy-binding \
  "${CI_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_ORG}/${GITHUB_REPO}"
```

## Initialize Firestore (one-time)

If Firestore is not already initialized in this project, create it once in the Google Cloud Console (Firestore in Native mode), then continue.

## Configure GitHub repository settings

Set these **Repository Variables**:

- `GCP_PROJECT_ID` = your project id
- `GCP_REGION` = `us-central1` (or your region)
- `CLOUD_RUN_SERVICE_NAME` = `athena-poc-api`
- `FIRESTORE_COLLECTION` = `poc_requests`

Set these **Repository Secrets**:

- `WIF_PROVIDER` = `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/POOL_ID/providers/PROVIDER_ID`
- `WIF_SERVICE_ACCOUNT` = `github-cloudrun-deployer@PROJECT_ID.iam.gserviceaccount.com`
- `RUNTIME_SERVICE_ACCOUNT` = `athena-cloudrun-runtime@PROJECT_ID.iam.gserviceaccount.com`

## Deploy

- Push to `main`, or run the workflow manually via GitHub Actions (`workflow_dispatch`).
- Workflow will build from source and deploy to Cloud Run.

## Test after deploy

```bash
SERVICE_URL="https://your-service-url"
curl "${SERVICE_URL}/health"
curl "${SERVICE_URL}/"
curl -X POST "${SERVICE_URL}/track"
```

## Optional local run

```bash
cp example.env .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a && source .env && set +a
python app.py
```

## Firestore free-tier note

This POC writes one small document for each `/track` call. Keep usage low to stay within free-tier quotas.
# althea-agent
