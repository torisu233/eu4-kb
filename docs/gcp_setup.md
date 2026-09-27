# One-time GCP setup for eu4-kb

This guide lists the **one-time** GCP setup that `.github/workflows/ci.yml` needs before it can
deploy. The commands need your own GCP permissions. Run them in order on a machine where the
`gcloud` CLI is installed and signed in. They are not part of the CI/CD pipeline, and you only
need to run them again if you change the GCP project or the repository.

The design goal is **no long-lived credentials anywhere**. GitHub Actions exchanges its own OIDC
token for short-lived GCP credentials through Workload Identity Federation (step 5). The only
secret, the Claude token, lives in Secret Manager and is read only by the runtime service
account (step 6).

## 0. Variables (fill these in first, then copy and paste the rest)

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="asia-northeast1"                     # change as needed
export REPO="torisuorg/eu4-kb"                      # GitHub repo, owner/name
export AR_REPO="eu4-kb"                             # Artifact Registry repository name
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$AR_REPO/eu4-kb"
export KB_BUCKET="eu4-kb-data-$PROJECT_ID"          # bucket names are globally unique
export DEPLOY_SA="eu4-kb-deploy"                    # identity GitHub Actions uses to build/push/deploy
export RUN_SA="eu4-kb-run"                          # identity the Cloud Run service runs as
export OAUTH_SECRET="claude-code-oauth-token"       # Secret Manager secret name
export WIF_POOL="github-pool"
export WIF_PROVIDER="github-provider"

gcloud config set project "$PROJECT_ID"
```

## 1. Enable the required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  sts.googleapis.com
```

## 2. Artifact Registry (Docker images)

```bash
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="eu4-kb service images"
```

## 3. GCS bucket (knowledge-base data)

The built knowledge base (`kbs/eu4/`) is **not** baked into the image and is **not** in git. It
can only be rebuilt on a machine that has the game installed, and the build takes 40–50 minutes.
So it lives in a bucket that Cloud Run mounts as a volume.

```bash
gcloud storage buckets create "gs://$KB_BUCKET" --location="$REGION"

# First upload: run a full kb.build_all locally, then from the repo root.
# emb/ is excluded: it is an intermediate artefact between embed and lance and
# is not needed for serving.
gcloud storage rsync -r kbs/eu4 "gs://$KB_BUCKET/eu4" --exclude=".*emb/.*"
```

After each local rebuild, rerun the same `rsync`. **Updating the data does not require
redeploying the code.**

## 4. Two service accounts

Two identities keep permissions narrow. The deploy account can push images and deploy, but it
cannot read the data or the secret. The runtime account can read the data and the secret, but it
cannot deploy anything.

```bash
# deploy-sa: used by GitHub Actions to build/push images and deploy Cloud Run
gcloud iam service-accounts create "$DEPLOY_SA" --display-name="eu4-kb GitHub Actions deployer"

# run-sa: the identity the Cloud Run service runs as (reads the bucket and the secret)
gcloud iam service-accounts create "$RUN_SA" --display-name="eu4-kb Cloud Run runtime"

DEPLOY_SA_EMAIL="$DEPLOY_SA@$PROJECT_ID.iam.gserviceaccount.com"
RUN_SA_EMAIL="$RUN_SA@$PROJECT_ID.iam.gserviceaccount.com"

# deploy-sa: push images, deploy Cloud Run, and run the service as run-sa
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$DEPLOY_SA_EMAIL" --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$DEPLOY_SA_EMAIL" --role="roles/run.admin"
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA_EMAIL" \
  --member="serviceAccount:$DEPLOY_SA_EMAIL" --role="roles/iam.serviceAccountUser"

# run-sa: read the bucket (mounted volume); the secret binding is in step 6
gcloud storage buckets add-iam-policy-binding "gs://$KB_BUCKET" \
  --member="serviceAccount:$RUN_SA_EMAIL" --role="roles/storage.objectViewer"
```

## 5. Workload Identity Federation (keyless auth for GitHub Actions)

```bash
gcloud iam workload-identity-pools create "$WIF_POOL" \
  --location="global" --display-name="GitHub Actions Pool"

gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" \
  --location="global" --workload-identity-pool="$WIF_POOL" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='$REPO'"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
WIF_PROVIDER_FULL="projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$WIF_POOL/providers/$WIF_PROVIDER"

# Only workflows from this one GitHub repo may impersonate deploy-sa
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA_EMAIL" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$WIF_POOL/attribute.repository/$REPO"

echo "GCP_WIF_PROVIDER = $WIF_PROVIDER_FULL"   # note this down for the GitHub variables below
```

The trust is limited in two places: the provider's `attribute-condition` rejects tokens from any
other repository, and the `principalSet` binding only lets that repository impersonate the
deploy account.

## 6. Secret Manager (`CLAUDE_CODE_OAUTH_TOKEN`)

```bash
# Get a token locally with `claude setup-token`, then:
echo -n "<your-token>" | gcloud secrets create "$OAUTH_SECRET" --data-file=-

gcloud secrets add-iam-policy-binding "$OAUTH_SECRET" \
  --member="serviceAccount:$RUN_SA_EMAIL" --role="roles/secretmanager.secretAccessor"
```

## 7. GitHub repository settings (Settings → Secrets and variables → Actions)

**Variables** (read by `ci.yml` as `${{ vars.* }}`). None of these are secrets:

| Variable | Value |
|---|---|
| `GCP_IMAGE` | `$IMAGE` (e.g. `asia-northeast1-docker.pkg.dev/<project>/eu4-kb/eu4-kb`) |
| `GCP_REGION` | `$REGION` |
| `GCP_WIF_PROVIDER` | `$WIF_PROVIDER_FULL` printed in step 5 |
| `GCP_DEPLOY_SA` | `$DEPLOY_SA_EMAIL` |
| `GCP_RUN_SA` | `$RUN_SA_EMAIL` |
| `GCP_KB_BUCKET` | `$KB_BUCKET` |
| `GCP_OAUTH_SECRET_NAME` | `$OAUTH_SECRET`, the **bare secret ID** such as `claude-code-oauth-token`. `--set-secrets` rejects the full `projects/<project-id>/secrets/...` path with `is not a valid secret name`; within the same project, use the short ID. |

The token itself is **not** stored in GitHub. It is already in Secret Manager, and the deploy
step references it by name, so it never appears in GitHub Actions logs or environment.

## 8. Verify

```bash
git push origin <your-branch>      # triggers the test job
git push origin master             # after the tests pass, the deploy job runs
```

Before the first `deploy` run, make sure steps 1–7 are done. Otherwise the job will fail at
`auth` (WIF not matched) or at `gcloud run deploy` (bucket or secret permissions missing). Both
are expected; follow the error message back to the matching IAM binding.
