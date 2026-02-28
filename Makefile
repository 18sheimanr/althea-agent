SHELL := /bin/bash

PROJECT_ID ?= your-gcp-project-id
REGION ?= us-central1
SERVICE ?= athena-api

.PHONY: help watch-deploy prod-logs local-run local-test

help:
	@echo "Useful targets:"
	@echo "  make watch-deploy  # watch latest GitHub Actions deploy run"
	@echo "  make prod-logs     # show recent Cloud Run error logs"
	@echo "  make local-run     # run app locally with .env loaded"
	@echo "  make local-test    # run local pytest suite"

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
