# althea-agent

Minimal Flask API deployed to Cloud Run with GitHub Actions.  
The app writes simple request metadata to Firestore.

## Endpoints

- `GET /health`
- `GET /`
- `POST /track`

## Local Development

```bash
cp example.env .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a && source .env && set +a
python app.py
```

Test locally:

```bash
curl http://localhost:8080/health
curl http://localhost:8080/
curl -X POST http://localhost:8080/track
```

## Deploy

Deployment is fully automated by `.github/workflows/deploy-cloud-run.yml`.

1. Commit changes.
2. Push to `main`.
3. Wait for the `Deploy to Cloud Run` workflow to pass.

The workflow builds a Docker image, pushes it to Artifact Registry, and deploys to Cloud Run.

## Required GitHub Repo Configuration

Repository variables:

- `GCP_PROJECT_ID`
- `GCP_REGION`
- `CLOUD_RUN_SERVICE_NAME`
- `FIRESTORE_COLLECTION`
- `ARTIFACT_REPO`
- `IMAGE_NAME`

Repository secrets:

- `WIF_PROVIDER`
- `WIF_SERVICE_ACCOUNT`
- `RUNTIME_SERVICE_ACCOUNT`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_ACCOUNT_SID`

If any of these are missing, ask a repo/GCP admin to set them.
