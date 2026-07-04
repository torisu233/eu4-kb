# EU4-KB 部署一次性 GCP 設置

這份文件列出讓 `.github/workflows/ci.yml` 能跑通所需的**一次性**GCP資源設置。這些指令需要你自己
的GCP帳號權限，請自行在本機（已裝 `gcloud` CLI 並登入）依序執行——不是CI/CD管線的一部分，只需做一次
（除非要換GCP專案/repo）。

## 0. 變數（先填好，後面直接複製貼上）

```bash
export PROJECT_ID="你的GCP專案ID"
export REGION="asia-northeast1"                    # 按你需求改
export REPO="torisuorg/eu4-kb"                      # GitHub repo，owner/name
export AR_REPO="eu4-kb"                             # Artifact Registry 倉庫名
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$AR_REPO/eu4-kb"
export KB_BUCKET="eu4-kb-data-$PROJECT_ID"          # 桶名全域唯一，按需改
export DEPLOY_SA="eu4-kb-deploy"                     # GitHub Actions 用來 build/push/deploy 的身份
export RUN_SA="eu4-kb-run"                           # Cloud Run 服務實際運行時的身份
export OAUTH_SECRET="claude-code-oauth-token"        # Secret Manager 密鑰名稱
export WIF_POOL="github-pool"
export WIF_PROVIDER="github-provider"

gcloud config set project "$PROJECT_ID"
```

## 1. 啟用必要 API

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

## 2. Artifact Registry（存 Docker 鏡像）

```bash
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="eu4-kb 服務鏡像"
```

## 3. GCS bucket（存知識庫數據，`kbs/eu4/` 的持久化落腳點）

```bash
gcloud storage buckets create "gs://$KB_BUCKET" --location="$REGION"

# 首次上傳：本地先跑完 kb.build_all 全量重建，再從 eu4-kb 專案根目錄執行
# (排除 emb/：serving 階段不需要，是 embed→lance 之間的中間產物)
gcloud storage rsync -r kbs/eu4 "gs://$KB_BUCKET/eu4" --exclude=".*emb/.*"
```

之後每次本地重建完知識庫，重跑上面這條 `rsync` 命令同步即可，**不需要重新部署代碼**。

## 4. 兩個 Service Account

```bash
# deploy-sa：GitHub Actions 拿去 build/push 鏡像、部署 Cloud Run
gcloud iam service-accounts create "$DEPLOY_SA" --display-name="eu4-kb GitHub Actions 部署身份"

# run-sa：Cloud Run 服務實際運行時的身份（讀 GCS bucket、讀 Secret Manager）
gcloud iam service-accounts create "$RUN_SA" --display-name="eu4-kb Cloud Run 運行身份"

DEPLOY_SA_EMAIL="$DEPLOY_SA@$PROJECT_ID.iam.gserviceaccount.com"
RUN_SA_EMAIL="$RUN_SA@$PROJECT_ID.iam.gserviceaccount.com"

# deploy-sa 權限：推鏡像、部署 Cloud Run、以 run-sa 身份運行服務
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$DEPLOY_SA_EMAIL" --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$DEPLOY_SA_EMAIL" --role="roles/run.admin"
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA_EMAIL" \
  --member="serviceAccount:$DEPLOY_SA_EMAIL" --role="roles/iam.serviceAccountUser"

# run-sa 權限：讀 GCS bucket(掛載卷)、讀 Secret Manager 密鑰
gcloud storage buckets add-iam-policy-binding "gs://$KB_BUCKET" \
  --member="serviceAccount:$RUN_SA_EMAIL" --role="roles/storage.objectViewer"
```

## 5. Workload Identity Federation（讓 GitHub Actions 免長期密鑰換取 GCP 憑證）

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

# 只允許來自這個 GitHub repo 的 workflow 冒充 deploy-sa
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA_EMAIL" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$WIF_POOL/attribute.repository/$REPO"

echo "GCP_WIF_PROVIDER = $WIF_PROVIDER_FULL"   # 記下來，等下填進 GitHub repo variables
```

## 6. Secret Manager（存 `CLAUDE_CODE_OAUTH_TOKEN`）

```bash
# 用 claude setup-token 在本機取得訂閱 OAuth token(sk-ant-oat01-開頭)後：
echo -n "sk-ant-oat01-你的token" | gcloud secrets create "$OAUTH_SECRET" --data-file=-

gcloud secrets add-iam-policy-binding "$OAUTH_SECRET" \
  --member="serviceAccount:$RUN_SA_EMAIL" --role="roles/secretmanager.secretAccessor"
```

## 7. 填進 GitHub 倉庫設置（Settings → Secrets and variables → Actions）

**Variables**（`ci.yml` 用 `${{ vars.* }}` 讀取）：

| 變數名 | 值 |
|---|---|
| `GCP_IMAGE` | `$IMAGE`（如 `asia-northeast1-docker.pkg.dev/xxx/eu4-kb/eu4-kb`） |
| `GCP_REGION` | `$REGION` |
| `GCP_WIF_PROVIDER` | 上一步印出的 `$WIF_PROVIDER_FULL` |
| `GCP_DEPLOY_SA` | `$DEPLOY_SA_EMAIL` |
| `GCP_RUN_SA` | `$RUN_SA_EMAIL` |
| `GCP_KB_BUCKET` | `$KB_BUCKET` |
| `GCP_OAUTH_SECRET_NAME` | `projects/$PROJECT_ID/secrets/$OAUTH_SECRET`（Cloud Run `--set-secrets` 引用格式） |

不需要在 GitHub 存 `CLAUDE_CODE_OAUTH_TOKEN` 本身——它已經進了 Secret Manager，Cloud Run 部署時
直接用 `GCP_OAUTH_SECRET_NAME` 引用，不會出現在GitHub Actions的日誌或環境裡。

## 8. 驗證

```bash
git push origin <你的分支>          # 觸發 test job
git push origin master             # test 過了會接著跑 deploy job
```

`deploy` job 第一次跑之前，確保第1-7步都做完，否則會卡在 `auth`（WIF沒配對）或 `gcloud run deploy`
（bucket/secret權限沒給對）這兩步，屬預期內——照報錯訊息回頭檢查對應的IAM綁定即可。
