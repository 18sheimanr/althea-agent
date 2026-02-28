import os
from datetime import datetime, timezone
from typing import Any, Dict

from flask import Flask, jsonify, request
from google.cloud import firestore


app = Flask(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
COLLECTION_NAME = os.getenv("FIRESTORE_COLLECTION", "poc_requests")

# The Firestore client uses Application Default Credentials in Cloud Run.
db = firestore.Client(project=PROJECT_ID) if PROJECT_ID else firestore.Client()


def _request_metadata() -> Dict[str, Any]:
    return {
        "method": request.method,
        "path": request.path,
        "remote_addr": request.remote_addr,
        "user_agent": request.headers.get("User-Agent", ""),
        "host": request.host,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok"})


@app.get("/")
def index() -> Any:
    return jsonify(
        {
            "message": "Flask + Cloud Run + Firestore POC is running.",
            "hint": "Call POST /track to write a tiny record to Firestore.",
        }
    )


@app.post("/track")
def track() -> Any:
    metadata = _request_metadata()
    doc_ref = db.collection(COLLECTION_NAME).document()
    doc_ref.set(metadata)
    return jsonify({"saved": True, "doc_id": doc_ref.id, "collection": COLLECTION_NAME})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
