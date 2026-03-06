# Infrastructure One-Pager

This document covers the stable infrastructure for `althea-agent`.
Application code can evolve freely; this baseline infra should stay mostly unchanged.

## Platform

- **Runtime:** Google Cloud Run (public HTTPS service)
- **Database:** Cloud Firestore (Native mode, default database)
- **Container Registry:** Artifact Registry (Docker repo)
- **CI/CD:** GitHub Actions with GCP Workload Identity Federation (OIDC, no static keys)

## Deployed Resources

- **GCP Project:** `your-gcp-project-id`
- **Region:** `us-central1`
- **Cloud Run Service:** `athena-api`
- **Cloud Run URL:** `https://your-service-xxxxx-uc.a.run.app`
- **Artifact Registry Repo:** `athena`
- **Firestore Collection (app default):** `athena-agent`

## Cloud Run Runtime Settings

- `max instances = 1`
- `min instances = 0` (scales to zero)
- `cpu = 1`
- `memory = 256Mi`
- Public invoker enabled (service is internet-accessible via URL)

## Identity and Access

### Runtime Service Account

- **Account:** `athena-cloudrun-runtime@your-gcp-project-id.iam.gserviceaccount.com`
- **Purpose:** Identity used by running container
- **Roles:**
  - `roles/datastore.user` (Firestore read/write)
  - `roles/aiplatform.user` (Vertex AI model access)

### GitHub Deployer Service Account

- **Account:** `github-cloudrun-deployer@your-gcp-project-id.iam.gserviceaccount.com`
- **Purpose:** CI deploy identity (via OIDC)
- **Project Roles:**
  - `roles/run.admin`
  - `roles/artifactregistry.writer`
  - `roles/serviceusage.serviceUsageConsumer`
- **Impersonation Permission:**
  - `roles/iam.serviceAccountUser` on runtime service account

### Workload Identity Federation

- **Pool:** `github-pool`
- **Provider:** `github-provider`
- Trusted GitHub repo: `your-github-username/althea-agent`

## GitHub Actions Deploy Contract

Workflow: `.github/workflows/deploy-cloud-run.yml`

Deploy model:

1. Build Docker image in GitHub Actions runner
2. Push image to Artifact Registry
3. Deploy Cloud Run by image reference

Required GitHub repository variables:

- `GCP_PROJECT_ID`
- `GCP_REGION`
- `CLOUD_RUN_SERVICE_NAME`
- `ARTIFACT_REPO`
- `IMAGE_NAME`

Required GitHub repository secrets:

- `WIF_PROVIDER`
- `WIF_SERVICE_ACCOUNT`
- `RUNTIME_SERVICE_ACCOUNT`

## Cost Posture

- Designed for very low cost:
  - Cloud Run scales to zero
  - Single instance max
  - Small memory footprint
- Firestore usage expected to remain in free tier for low traffic.
- Artifact Registry costs are minimal (image storage only).

## Things That Should Not Change Frequently

- OIDC trust model (WIF pool/provider + repo trust)
- Service account split (deployer vs runtime)
- Cloud Run low-cost limits (`min=0`, `max=1`, `256Mi`)
- Artifact Registry repo naming and region

## Safe Change Areas

- Flask routes and app logic
- Firestore document schema/content
- Cloud Run service env vars consumed by app

