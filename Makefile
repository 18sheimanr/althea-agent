SHELL := /bin/bash

PROJECT_ID ?= your-gcp-project-id
REGION ?= us-central1
SERVICE ?= athena-api
QUEUE ?= athena-reminders
SERVICE_URL ?= https://your-service-xxxxx-uc.a.run.app

.PHONY: help watch-deploy prod-logs local-run local-test verify-reminders

help:
	@echo "Useful targets:"
	@echo "  make watch-deploy  # watch latest GitHub Actions deploy run"
	@echo "  make prod-logs     # show recent Cloud Run error logs"
	@echo "  make local-run     # run app locally with .env loaded"
	@echo "  make local-test    # run local pytest suite"
	@echo "  make verify-reminders # check prod reminders + Cloud Tasks queue"

watch-deploy:
	@run_id=$$(gh run list --limit 1 --json databaseId --jq '.[0].databaseId'); \
	if [[ -z "$$run_id" ]]; then \
	  echo "No GitHub Actions runs found."; \
	  exit 1; \
	fi; \
	echo "Watching run $$run_id"; \
	gh run watch "$$run_id" --exit-status

prod-logs:
	gcloud logging read \
	  "resource.type=cloud_run_revision AND resource.labels.service_name=$(SERVICE) AND severity>=ERROR" \
	  --project "$(PROJECT_ID)" \
	  --limit 20 \
	  --format "value(timestamp,textPayload)"

local-run:
	@set -a; source .env; set +a; .venv/bin/python app.py

local-test:
	.venv/bin/pytest -q

verify-reminders:
	@echo "==> GET $(SERVICE_URL)/events/reminders"
	@curl -sS -i "$(SERVICE_URL)/events/reminders"
	@echo ""
	@echo "==> Cloud Tasks queue status"
	@gcloud tasks queues describe "$(QUEUE)" \
	  --location "$(REGION)" \
	  --project "$(PROJECT_ID)" \
	  --format "table(name,state,rateLimits.maxDispatchesPerSecond,retryConfig.maxAttempts)"
