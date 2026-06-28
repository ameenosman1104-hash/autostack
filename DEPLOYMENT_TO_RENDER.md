# 🚀 Deploy AI Agent to Render

Complete guide to deploy Autostack with AI Agent integration to Render.

## 📋 Prerequisites

- Render account (https://render.com)
- GitHub repository with your code
- Domain name (autostack.co.za already configured)

---

## Phase 1: Deploy Flask + AI Agent Frontend

### Step 1: Push Latest Code to GitHub

```bash
cd C:\Users\user\InventoryTrackerWeb
git add .
git commit -m "Add AI Agent integration"
git push origin main
```

**Files changed:**
- `app/routes/ai_agent.py` - New AI Agent routes
- `app/templates/ai_agent.html` - Chat interface
- `app/__init__.py` - Blueprint registration
- `app/templates/base.html` - Navigation link

### Step 2: Create New Web Service on Render

1. Go to https://dashboard.render.com
2. Click **New +** → **Web Service**
3. Connect GitHub repository
4. Configure:
   - **Name:** autostack-api
   - **Environment:** Python 3
   - **Build Command:** 
     ```
     pip install -r requirements.txt
     ```
   - **Start Command:**
     ```
     gunicorn --bind 0.0.0.0:$PORT run:app
     ```

### Step 3: Set Environment Variables

In Render dashboard, add Environment:

```
SECRET_KEY=your-secret-key-here
FLASK_ENV=production
AI_AGENT_API=https://ai-agent-nextjs.onrender.com/api/ai-agent
```

**Important:** Change `AI_AGENT_API` to point to your Next.js deployment (see Phase 2)

### Step 4: Deploy

Click **Deploy** and wait for build to complete.

**Note:** After deploying Next.js (Phase 2), update the `AI_AGENT_API` environment variable.

---

## Phase 2: Deploy Next.js AI Agent

### Step 1: Push Next.js Code to GitHub

```bash
cd C:\Users\user\Documents\GitHub\nextjs
git add .
git commit -m "Add AI Agent with Gemini integration"
git push origin main
```

### Step 2: Create New Web Service on Render

1. Go to https://dashboard.render.com
2. Click **New +** → **Web Service**
3. Connect GitHub (nextjs repository)
4. Configure:
   - **Name:** ai-agent-nextjs
   - **Environment:** Node
   - **Build Command:**
     ```
     npm install && npm run build
     ```
   - **Start Command:**
     ```
     npm run start
     ```
   - **Node Version:** 18

### Step 3: Set Environment Variables

```
GOOGLE_GEMINI_API_KEY=your-gemini-key
NEXT_PUBLIC_API_URL=https://autostack-api.onrender.com
```

### Step 4: Deploy

Click **Deploy** and wait for completion.

**Your AI Agent will be at:** `https://ai-agent-nextjs.onrender.com`

---

## Phase 3: Update Flask Configuration

### Step 1: Update Environment Variable in Flask Service

Go to **autostack-api** service settings on Render:

Change:
```
AI_AGENT_API=https://ai-agent-nextjs.onrender.com/api/ai-agent
```

This tells Flask where to find the Next.js AI Agent on production.

### Step 2: Redeploy Flask

Click **Manual Deploy** in Render dashboard to apply the environment variable change.

---

## Phase 4: Connect Custom Domain

### For autostack.co.za

1. In Render dashboard, go to **autostack-api** service
2. Click **Settings** → **Custom Domain**
3. Add: `autostack.co.za`
4. Add CNAME record to your domain DNS:
   ```
   Name: autostack
   Type: CNAME
   Value: autostack-api.onrender.com
   ```

### For AI Agent (Optional)

If you want `ai.autostack.co.za`:

1. In Render, go to **ai-agent-nextjs** service
2. Add custom domain: `ai.autostack.co.za`
3. Add CNAME to DNS

---

## ✅ Verification Checklist

After deployment:

- [ ] Flask app loads: https://autostack.co.za
- [ ] Can login with credentials
- [ ] AI Agent link visible in sidebar
- [ ] AI Agent chat responds
- [ ] Responses are working
- [ ] No errors in Render logs

---

## 📊 Testing Deployment

### Test Flask + AI Integration

```bash
curl -X POST https://autostack.co.za/ai-agent/send \
  -H "Content-Type: application/json" \
  -d '{"message": "test"}'
```

(Requires login token in browser)

### Test Direct AI Agent API

```bash
curl -X POST https://ai-agent-nextjs.onrender.com/api/ai-agent \
  -H "Content-Type: application/json" \
  -d '{"message": "Do you have tyres in stock?"}'
```

---

## 🔧 Troubleshooting

### Issue: "Cannot connect to AI Agent"
- Verify Next.js is deployed and running
- Check `AI_AGENT_API` environment variable in Flask
- Check Render service logs

### Issue: "Invalid Gemini API Key"
- Verify `GOOGLE_GEMINI_API_KEY` is set in Next.js service
- Check API key is valid and has correct permissions

### Issue: Database not migrating
- Render uses ephemeral storage by default
- Add Render PostgreSQL database or use persistent volume
- Update `database_url` in Flask

### Issue: Static files not loading
- Ensure `static/` directory is committed to git
- Check Flask app is serving static files correctly

---

## 🚀 Production Checklist

Before going live:

- [ ] Environment variables set correctly
- [ ] Database migrations completed
- [ ] SSL/HTTPS working
- [ ] Custom domain configured
- [ ] Error logging enabled
- [ ] Backup strategy in place
- [ ] Performance monitoring enabled

---

## 📈 Next Steps

After deployment:

1. **Monitor:** Check Render logs daily
2. **Optimize:** Adjust Render plan if needed (free tier limited)
3. **Backup:** Enable Render database backups
4. **Scale:** Upgrade instance type if traffic increases
5. **Maintain:** Keep dependencies updated

---

## 💡 Pro Tips

**Free Tier Limitations:**
- Services spin down after 15 mins of inactivity
- Cold start time ~30 seconds
- Limited to shared CPU

**To Avoid Spin Down:**
- Upgrade to paid plan ($7/month minimum)
- Or set up Uptime Monitor to ping regularly

**For Better Performance:**
- Use Render's managed databases
- Enable caching headers
- Use CDN for static files

---

## Support

If you encounter issues:

1. Check Render service logs: https://dashboard.render.com
2. Verify environment variables are set
3. Test APIs with curl
4. Check GitHub Actions for deployment logs

---

**Version:** 1.0
**Status:** Ready for Production
**Last Updated:** 2025-06-29
