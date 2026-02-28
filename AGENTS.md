# Agent Notes

- You are working on a personal AI assistant hosted on GCP that uses
- ALWAYS START by looking in the ./docs/ folder. ONLY read docs if they are helpful for the task at hand. But ALWAYS check.
- Infra is simple: Cloud Run + Firestore + GitHub Actions OIDC.
- Basic deploy commands:
  - Push trigger deploy: `git push origin main`
  - Watch latest run: `gh run list --limit 1` then `gh run watch <run-id> --exit-status`
  - Check Cloud Run URL: `gcloud run services describe athena-api --region us-central1 --format='value(status.url)'`
