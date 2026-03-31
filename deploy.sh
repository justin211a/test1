#!/bin/bash
# ============================================
# Marketing Data Pipeline - Cloud Run Deployment
# ============================================
# Usage:
#   ./deploy.sh                   # Deploy both job and service
#   ./deploy.sh job               # Deploy Cloud Run Job only
#   ./deploy.sh service           # Deploy Cloud Run Service only
#   ./deploy.sh scheduler         # Setup Cloud Scheduler only
# ============================================

set -euo pipefail

# Configuration - modify these for your environment
PROJECT_ID="${BQ_PROJECT_ID:?Set BQ_PROJECT_ID environment variable}"
REGION="${CLOUD_RUN_REGION:-asia-northeast3}"  # Seoul
JOB_NAME="marketing-pipeline-job"
SERVICE_NAME="marketing-pipeline-api"
SCHEDULER_NAME="marketing-daily-run"
IMAGE_NAME="gcr.io/${PROJECT_ID}/marketing-pipeline"

echo "=== Marketing Data Pipeline Deployment ==="
echo "Project: ${PROJECT_ID}"
echo "Region: ${REGION}"

deploy_job() {
    echo ""
    echo "--- Building Docker image ---"
    gcloud builds submit --tag "${IMAGE_NAME}" .

    echo ""
    echo "--- Deploying Cloud Run Job (daily ETL) ---"
    gcloud run jobs create "${JOB_NAME}" \
        --image "${IMAGE_NAME}" \
        --region "${REGION}" \
        --task-timeout 1800 \
        --max-retries 1 \
        --memory 1Gi \
        --cpu 1 \
        --set-env-vars "RUN_MODE=job" \
        --command "python" \
        --args "-m,src.main,run" \
        2>/dev/null || \
    gcloud run jobs update "${JOB_NAME}" \
        --image "${IMAGE_NAME}" \
        --region "${REGION}" \
        --task-timeout 1800 \
        --max-retries 1 \
        --memory 1Gi \
        --cpu 1 \
        --set-env-vars "RUN_MODE=job"

    echo "Cloud Run Job '${JOB_NAME}' deployed."
}

deploy_service() {
    echo ""
    echo "--- Building Docker image ---"
    gcloud builds submit --tag "${IMAGE_NAME}" .

    echo ""
    echo "--- Deploying Cloud Run Service (ingest API) ---"
    gcloud run deploy "${SERVICE_NAME}" \
        --image "${IMAGE_NAME}" \
        --region "${REGION}" \
        --port 8080 \
        --memory 512Mi \
        --cpu 1 \
        --min-instances 0 \
        --max-instances 3 \
        --set-env-vars "RUN_MODE=server" \
        --allow-unauthenticated

    SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --format="value(status.url)")
    echo "Cloud Run Service deployed: ${SERVICE_URL}"
    echo ""
    echo "Ingest endpoints:"
    echo "  POST ${SERVICE_URL}/api/ingest/gfa    (Chrome extension)"
    echo "  POST ${SERVICE_URL}/api/ingest/csv    (CSV upload)"
    echo "  GET  ${SERVICE_URL}/health"
}

setup_scheduler() {
    echo ""
    echo "--- Setting up Cloud Scheduler (daily 06:00 KST) ---"
    JOB_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"

    gcloud scheduler jobs create http "${SCHEDULER_NAME}" \
        --location "${REGION}" \
        --schedule "0 6 * * *" \
        --time-zone "Asia/Seoul" \
        --uri "${JOB_URI}" \
        --http-method POST \
        --oauth-service-account-email "${PROJECT_ID}@appspot.gserviceaccount.com" \
        2>/dev/null || \
    gcloud scheduler jobs update http "${SCHEDULER_NAME}" \
        --location "${REGION}" \
        --schedule "0 6 * * *" \
        --time-zone "Asia/Seoul" \
        --uri "${JOB_URI}" \
        --http-method POST \
        --oauth-service-account-email "${PROJECT_ID}@appspot.gserviceaccount.com"

    echo "Cloud Scheduler '${SCHEDULER_NAME}' set (daily 06:00 KST)."
}

# Main
case "${1:-all}" in
    job)       deploy_job ;;
    service)   deploy_service ;;
    scheduler) setup_scheduler ;;
    all)
        deploy_job
        deploy_service
        setup_scheduler
        echo ""
        echo "=== Deployment complete! ==="
        ;;
    *)
        echo "Usage: $0 {job|service|scheduler|all}"
        exit 1
        ;;
esac
