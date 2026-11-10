#!/bin/sh
# =============================================================================
# deploy_firebase_cloudrun.sh
# -----------------------------------------------------------------------------
# Deploys the prototype to Google Cloud Run + Cloud Scheduler + Firebase Hosting:
#
#   1. Builds & pushes the two Docker images (`chn-api` and `chn-parsers`)
#      to Google Artifact Registry / Cloud Build.
#   2. Deploys `chn-api` as a serverless Cloud Run Service (scales to 0 when
#      idle -> near-$0 cost).
#   3. Deploys `chn-parsers` as a Cloud Run Job (`python -m chn_parsers run`)
#      and schedules it every 6 hours via Cloud Scheduler.
#   4. Deploys Firebase Hosting (`firebase.json`), which rewrites `/api/**`
#      directly to the `chn-api` Cloud Run service and hosts the web preview.
#
# Prerequisites:
#   * `gcloud` and `firebase` CLIs installed and authenticated
#   * PostgreSQL URL in Secret Manager (`READER_DATABASE_URL`, `SCRAPER_DATABASE_URL`)
#     (works with Cloud SQL for PostgreSQL, Neon, or Supabase free tier)
#   * Migrations applied once: `DATABASE_URL=... ./database/migrate.sh`
# =============================================================================
set -eu

PROJECT_ID="${GCP_PROJECT_ID:-tum-chn-events-proto}"
REGION="${GCP_REGION:-europe-west3}"
REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)

echo "[deploy] Using GCP project=$PROJECT_ID region=$REGION"

# 1. Build container images with Cloud Build
gcloud builds submit "$REPO_ROOT/database" \
    --project "$PROJECT_ID" \
    --tag "gcr.io/$PROJECT_ID/chn-api:latest" \
    --file "$REPO_ROOT/database/Dockerfile.api"

gcloud builds submit "$REPO_ROOT/parsers" \
    --project "$PROJECT_ID" \
    --tag "gcr.io/$PROJECT_ID/chn-parsers:latest"

# 2. Deploy the Read API to Cloud Run (referenced by firebase.json rewrites)
gcloud run deploy chn-api \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --image "gcr.io/$PROJECT_ID/chn-api:latest" \
    --platform managed \
    --allow-unauthenticated \
    --min-instances 0 \
    --max-instances 3 \
    --set-secrets "DATABASE_URL=READER_DATABASE_URL:latest"

# 3. Deploy the deterministic Parser as a one-shot Cloud Run Job
gcloud run jobs deploy chn-parsers-job \
    --project "$PROJECT_ID" \
    --region "$REGION" \
    --image "gcr.io/$PROJECT_ID/chn-parsers:latest" \
    --command "python" \
    --args="-m,chn_parsers,run" \
    --set-secrets "DATABASE_URL=SCRAPER_DATABASE_URL:latest"

# 4. Deploy Firebase Hosting (rewrites /api/** -> Cloud Run service `chn-api`)
firebase deploy --project "$PROJECT_ID" --only hosting

