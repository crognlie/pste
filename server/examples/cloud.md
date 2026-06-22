# Cloud deployment

## GCP: Cloud Run + Cloud SQL + GCS

Complete walkthrough from zero to a running pste instance. All commands use
the `gcloud` and `gsutil` CLIs. The only step that requires the Cloud Console
UI is creating the service account and downloading its JSON key — everything
else runs from a terminal.

### Prerequisites

- Google Cloud project with billing enabled
- `gcloud` CLI installed (`gcloud auth login`, `gcloud config set project YOUR_PROJECT`)
- **In Cloud Console:** IAM & Admin → Service Accounts → Create Service Account
  named `pste` with no roles (we assign roles below) → Keys → Add Key → JSON.
  Save the downloaded file as `pste-sa-key.json`.

### 0. Set variables

```bash
export PROJECT_ID=$(gcloud config get-value project)
export REGION=us-central1
export SA_EMAIL="pste@${PROJECT_ID}.iam.gserviceaccount.com"
export SA_KEY=/path/to/pste-sa-key.json
export BUCKET="${PROJECT_ID}-pste-pastes"
```

### 1. Enable APIs

```bash
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  sql-component.googleapis.com
```

### 2. Create Cloud Storage bucket

```bash
gcloud storage buckets create gs://$BUCKET \
  --location=$REGION \
  --uniform-bucket-level-access
```

### 3. Create Cloud SQL (PostgreSQL) instance

```bash
gcloud sql instances create pste-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --edition=ENTERPRISE \
  --region=$REGION

gcloud sql databases create pste --instance=pste-db

DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')
gcloud sql users create pste --instance=pste-db --password="$DB_PASS"
```

> **Note:** `db-f1-micro` requires `--edition=ENTERPRISE`. New projects default
> to ENTERPRISE_PLUS, which only supports the `db-perf-optimized-N-*` tiers.

### 4. Grant service account permissions

```bash
# Read/write paste content in GCS
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin"

# Connect to Cloud SQL
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/cloudsql.client"

# Read secrets from Secret Manager
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"
```

### 5. Store database URL in Secret Manager

Cloud Run connects to Cloud SQL via a Unix socket at
`/cloudsql/PROJECT:REGION:INSTANCE`. The `@/pste` form (empty TCP host)
tells psycopg2 to use the `host` query parameter as the socket directory.

```bash
DB_URL="postgresql://pste:${DB_PASS}@/pste?host=/cloudsql/${PROJECT_ID}:${REGION}:pste-db"
printf '%s' "$DB_URL" | gcloud secrets create pste-db-url --data-file=-
```

### 6. Push image to Artifact Registry

Cloud Run only accepts images from GCR, Artifact Registry, or Docker Hub —
not GHCR directly. Push the image to Artifact Registry first:

```bash
# Enable Artifact Registry and create a repository
gcloud services enable artifactregistry.googleapis.com
gcloud artifacts repositories create pste \
  --repository-format=docker \
  --location=$REGION

# Configure docker auth and push
gcloud auth configure-docker ${REGION}-docker.pkg.dev --quiet
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/pste/pste-server:latest"
docker pull ghcr.io/crognlie/pste:latest
docker tag ghcr.io/crognlie/pste:latest $IMAGE
docker push $IMAGE
```

> If you're deploying from a machine without Docker, you can also build with
> Cloud Build: `gcloud builds submit --tag $IMAGE server/`

### 7. Deploy to Cloud Run

Cloud Run needs to know `BASE_URL` (for paste link generation), but you don't
know the URL until after the first deploy. Deploy once to get the URL, then
update the service.

```bash
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/pste/pste-server:latest"

gcloud run deploy pste-server \
  --image $IMAGE \
  --platform managed \
  --region $REGION \
  --service-account $SA_EMAIL \
  --set-env-vars "STORAGE_BACKEND=gcs,GCS_BUCKET=${BUCKET}" \
  --set-secrets "DATABASE_URL=pste-db-url:latest" \
  --add-cloudsql-instances "${PROJECT_ID}:${REGION}:pste-db" \
  --allow-unauthenticated \
  --port 8000

# Get the assigned URL and redeploy with BASE_URL set
BASE_URL=$(gcloud run services describe pste-server \
  --region $REGION --format 'value(status.url)')

gcloud run services update pste-server \
  --region $REGION \
  --update-env-vars "BASE_URL=${BASE_URL}"

echo "Service running at $BASE_URL"
```

### 8. Add your first API key

Use the [Cloud SQL Auth Proxy](https://cloud.google.com/sql/docs/postgres/sql-proxy)
to open a local tunnel, then run `pste-admin` locally against it.
**Set `STORAGE_BACKEND=gcs`** so pste-admin connects to PostgreSQL instead of
defaulting to a local SQLite file.

```bash
# Download the proxy (Linux x86-64; see docs for other platforms)
curl -Lo cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.1/cloud-sql-proxy.linux.amd64
chmod +x cloud-sql-proxy

# Start the proxy in the background
GOOGLE_APPLICATION_CREDENTIALS=$SA_KEY \
  ./cloud-sql-proxy "${PROJECT_ID}:${REGION}:pste-db" --port 5432 &
PROXY_PID=$!

# Run pste-admin key add (STORAGE_BACKEND=gcs selects the postgresql engine)
STORAGE_BACKEND=gcs \
  DATABASE_URL="postgresql://pste:${DB_PASS}@localhost:5432/pste" \
  GOOGLE_APPLICATION_CREDENTIALS=$SA_KEY \
  pste-admin key add --user admin
# -> http://localhost:8000/?key=AbCd1234...
# (Replace localhost:8000 with $BASE_URL to get your bookmark URL)

kill $PROXY_PID
```

### 9. Test with the CLI

```bash
export PSTE_URL="${BASE_URL}/?key=AbCd1234..."
echo "hello from GCP" | pste
pste AB1234
```

---

## Testing GCS storage locally

To run the server test suite against a real GCS bucket (covers `storage.py`
GCS paths that can't be tested with mocks):

```bash
# Start Cloud SQL Auth Proxy (see step 8 above)
GOOGLE_APPLICATION_CREDENTIALS=$SA_KEY \
  ./cloud-sql-proxy "${PROJECT_ID}:${REGION}:pste-db" --port 5432 &

cd server
pip install -e ".[postgresql,gcs]"
STORAGE_BACKEND=gcs \
  GCS_BUCKET=$BUCKET \
  DATABASE_URL="postgresql://pste:${DB_PASS}@localhost:5432/pste" \
  GOOGLE_APPLICATION_CREDENTIALS=$SA_KEY \
  python3 -m pytest
```

---

## AWS: ECS Fargate + RDS + S3

### Infrastructure overview

| Component | AWS service |
|---|---|
| Container | ECS Fargate |
| Database | RDS PostgreSQL |
| Blob storage | S3 (via `[s3]` extra — see note) |
| Secrets | AWS Secrets Manager |
| DNS/TLS | ACM + Application Load Balancer |

> **Note:** The `[s3]` storage backend is not yet implemented. Use `postgresql`
> backend (store content in RDS) for AWS deployments.

### Example task definition environment

```json
{
  "environment": [
    {"name": "STORAGE_BACKEND", "value": "postgresql"},
    {"name": "BASE_URL", "value": "https://pste.example.com"}
  ],
  "secrets": [
    {"name": "DATABASE_URL", "valueFrom": "arn:aws:secretsmanager:..."}
  ]
}
```

---

## Azure: Container Apps + Azure Database for PostgreSQL + Azure Blob Storage

> **Note:** The Azure Blob Storage backend is not yet implemented. Use
> `postgresql` backend with Azure Database for PostgreSQL.

### Example

```bash
az containerapp create \
  --name pste-server \
  --resource-group pste-rg \
  --image ghcr.io/crognlie/pste:latest \
  --env-vars \
    STORAGE_BACKEND=postgresql \
    BASE_URL=https://pste.example.com \
    DATABASE_URL=secretref:db-url \
  --ingress external --target-port 8000
```
