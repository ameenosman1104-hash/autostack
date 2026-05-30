# FLOWSTACK - Google Cloud Deployment Guide

## Prerequisites

1. **Google Cloud Account** - [Create one here](https://console.cloud.google.com)
2. **Google Cloud SDK** - [Install here](https://cloud.google.com/sdk/docs/install)
3. **Custom Domain** - www.flowstack.co.za (already have it ✓)

---

## Step 1: Install Google Cloud SDK

If not already installed, run:
```bash
# Download from: https://cloud.google.com/sdk/docs/install
# Then authenticate
gcloud auth login
```

---

## Step 2: Create a Google Cloud Project

```bash
# Create new project
gcloud projects create flowstack-production --name="FLOWSTACK - Osbro Tyres Inventory"

# Set it as active
gcloud config set project flowstack-production

# Enable App Engine API
gcloud services enable appengine.googleapis.com
```

---

## Step 3: Initialize App Engine Region

```bash
# Choose region (recommended: us-central1 for latency)
gcloud app create --region=us-central1
```

---

## Step 4: Deploy to Google App Engine

From the `C:\Users\user\InventoryTrackerWeb` directory:

```bash
gcloud app deploy app.yaml --version 1
```

After deployment, you'll get a URL like: `https://flowstack-production.appspot.com`

---

## Step 5: Connect Your Custom Domain

### Option A: Using Google Domains (Recommended)

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Select your project: `flowstack-production`
3. Go to **App Engine > Settings > Custom Domains**
4. Click **Add Custom Domain**
5. Enter: `www.flowstack.co.za`
6. Choose **CNAME** or **A record** (follow Google's guidance)
7. Update your domain DNS records at your registrar

### Option B: Manual DNS Setup

If your domain is NOT registered with Google Domains:

1. Get the App Engine IP address:
```bash
gcloud app describe --format="value(defaultHostname)"
```

2. Update your DNS records at your domain registrar:
   - **CNAME record**: `www` → `flowstack-production.appspot.com`
   - OR **A record**: Point to the IP address Google provides

---

## Step 6: Enable HTTPS (Automatic)

Google Cloud handles SSL/TLS automatically. Your domain will have HTTPS enabled within 24 hours.

---

## Step 7: Verify Deployment

```bash
# Check deployment status
gcloud app versions list

# View logs
gcloud app logs read -n 100

# Monitor traffic
gcloud monitoring dashboards list
```

---

## Step 8: Update Flask App for Production

Make sure your `app.yaml` has:
- ✓ `runtime: python311`
- ✓ `entrypoint: gunicorn -b :$PORT app:app`
- ✓ `FLASK_ENV: production`

---

## Troubleshooting

### Domain not resolving?
- Wait 24-48 hours for DNS to propagate
- Check DNS records at your registrar
- Verify CNAME/A record is correct

### App failing to start?
```bash
gcloud app logs read -n 50 --limit=50
```

### Need to rollback?
```bash
gcloud app versions list
gcloud app versions delete [version-id]
```

---

## Next Steps

1. ✅ Install Google Cloud SDK
2. ✅ Authenticate with Google Account
3. ✅ Create project `flowstack-production`
4. ✅ Deploy with `gcloud app deploy`
5. ✅ Connect domain www.flowstack.co.za
6. ✅ Test at https://www.flowstack.co.za

---

## Database & Storage

If you need persistent storage (database, files):
- Cloud SQL for PostgreSQL/MySQL
- Cloud Firestore for NoSQL
- Cloud Storage for files

Contact if you need help setting these up!

---

**Ready to go live?** Run the deployment commands above and let me know if you hit any issues!
