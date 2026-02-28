# althea-agent

Flask API deployed to Cloud Run with GitHub Actions.  
The app includes a Twilio SMS agent powered by Google ADK + Gemini, and stores
conversation/event state in Firestore.

## Endpoints

- `GET /health`
- `GET /`
- `POST /track`
- `POST /receive-sms`
- `GET /events/reminders`
- `POST /internal/events/agent-hook`

## Local Development

```bash
cp example.env .env
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
gcloud auth application-default login
set -a && source .env && set +a
python app.py
```

Test locally:

```bash
curl http://localhost:8080/health
curl http://localhost:8080/
curl -X POST http://localhost:8080/track
curl http://localhost:8080/events/reminders
```

Run tests locally:

```bash
pytest -q
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
- `CONVERSATIONS_COLLECTION`
- `EVENTS_COLLECTION`
- `TWILIO_ALLOWED_FROM`
- `GEMINI_MODEL`
- `INTERNAL_HOOK_AUDIENCE`
- `TASKS_CALLER_SERVICE_ACCOUNT`

Repository secrets:

- `WIF_PROVIDER`
- `WIF_SERVICE_ACCOUNT`
- `RUNTIME_SERVICE_ACCOUNT`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_ACCOUNT_SID`
- `GOOGLE_API_KEY`
- `EVENT_HOOK_TOKEN` (optional but recommended)
- `GOOGLE_SEARCH_API_KEY` (optional, for Google search tool)
- `GOOGLE_SEARCH_CX` (optional, for Google search tool)

If any of these are missing, ask a repo/GCP admin to set them.

## Internal Hook Security

`POST /internal/events/agent-hook` validates Google OIDC bearer tokens and only
accepts calls from the configured `TASKS_CALLER_SERVICE_ACCOUNT`.

For Cloud Tasks, configure an OIDC token on the HTTP target:
- audience = `INTERNAL_HOOK_AUDIENCE`
- service account = `TASKS_CALLER_SERVICE_ACCOUNT`
