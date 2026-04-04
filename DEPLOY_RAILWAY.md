# Deploy HELEN OS to Railway.app

**Gratuit, pas de CB requise** ✨

## Step 1: Create Railway Account (2 min)

1. Go to: https://railway.app
2. Click **"Start Project"**
3. Choose **"GitHub"** (you need GitHub account)
   - Or click **"Deploy from GitHub"** button
4. Authorize Railway to access your GitHub
5. Done! You have a free Railway account

**Free tier: $5/month credits** (MORE than enough for HELEN OS)

---

## Step 2: Deploy HELEN OS (3 min)

### Option A: Deploy from GitHub (Easiest)

1. Go to https://github.com/new
   - Create a new repo `helen-os`
   - Upload your files from ~/helen-os
   - Push to GitHub

2. Go to https://railway.app/dashboard
3. Click **"New Project"**
4. Select **"Deploy from GitHub"**
5. Find & select `helen-os` repo
6. Click **"Deploy"**

7. Railway will automatically:
   - Build the Docker image
   - Deploy to production
   - Assign a public URL

### Option B: Deploy from Git CLI (Advanced)

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login to Railway
railway login

# Initialize project
cd ~/helen-os
railway init

# Deploy
railway up
```

---

## Step 3: Set Environment Variables (1 min)

In Railway Dashboard:

1. Go to **"Variables"** tab
2. Add:
   ```
   GOOGLE_API_KEY=your-key-here
   ANTHROPIC_API_KEY=your-key-here
   OPENAI_API_KEY=your-key-here
   ```

3. Click **"Deploy"**

---

## Step 4: Get Your Public URL

1. Go to **"Deployments"** tab
2. Find the latest deployment with **"✓ Success"**
3. Click it to expand
4. Look for **"Railway Domain"** — that's your public URL!

Example: `https://helen-os-production.railway.app`

---

## Step 5: Test Your Deployment

```bash
# Replace with your Railway URL
curl https://your-helen-os-url.railway.app/health

curl https://your-helen-os-url.railway.app/models
```

If you see JSON responses → **SUCCESS!** 🎉

---

## Free Tier Limits

- $5/month free credits
- **Enough for:**
  - 100,000+ requests/month
  - 24/7 uptime
  - Automatic scaling

---

## Troubleshooting

**Deployment failed?**
- Check "Logs" tab in Railway Dashboard
- Look for build or startup errors

**URL not working?**
- Wait 1-2 minutes after deploy completes
- Check status shows "✓ Success"

**Need to restart?**
- Click "Redeploy" in Railway Dashboard

---

**That's it! Your HELEN OS is live!** 🚀
