# ⚡ Quick Deploy to Render (5 Minutes)

## 🚀 Step-by-Step

### Step 1: Push Code to GitHub (2 min)

```bash
cd C:\Users\user\InventoryTrackerWeb
git add .
git commit -m "Add AI Agent - ready for production"
git push origin main
```

Also push Next.js:
```bash
cd C:\Users\user\Documents\GitHub\nextjs
git add .
git commit -m "AI Agent API - ready for production"
git push origin main
```

### Step 2: Deploy Flask to Render (2 min)

1. Go: https://dashboard.render.com
2. Click: **New +** → **Web Service**
3. Connect your InventoryTrackerWeb GitHub repo
4. Fill in:
   - **Name:** `autostack-api`
   - **Region:** Closest to you
   - **Branch:** `main`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn run:app`

5. Add Environment Variables:
   ```
   SECRET_KEY=generate-random-string-here
   FLASK_ENV=production
   AI_AGENT_API=https://ai-agent-nextjs.onrender.com/api/ai-agent
   ```

6. Click **Create Web Service** → Wait for deployment (2-3 min)

### Step 3: Deploy Next.js to Render (2 min)

1. Click: **New +** → **Web Service**
2. Connect your nextjs GitHub repo
3. Fill in:
   - **Name:** `ai-agent-nextjs`
   - **Region:** Same as Flask
   - **Branch:** `main`
   - **Build Command:** `npm install && npm run build`
   - **Start Command:** `npm run start`
   - **Node Version:** 18

4. Add Environment Variables:
   ```
   GOOGLE_GEMINI_API_KEY=your-gemini-key-here
   NEXT_PUBLIC_API_URL=https://autostack-api.onrender.com
   ```

5. Click **Create Web Service** → Wait for deployment

### Step 4: Connect Domain (1 min)

1. Go to **autostack-api** service
2. Click **Settings** → **Custom Domain**
3. Add: `autostack.co.za`
4. Add CNAME to your DNS provider:
   ```
   Name: autostack
   Type: CNAME
   Value: autostack-api.onrender.com
   ```

---

## ✅ Verify It Works

**Go to:** https://autostack.co.za

- [ ] Login page loads
- [ ] Can login
- [ ] See sidebar with "AI Agent"
- [ ] Click AI Agent
- [ ] Chat works
- [ ] Get responses

---

## 🎉 Done!

Your AI Agent is now live on production! 🚀

---

## ⚠️ Important Notes

**Free Tier:**
- Services may sleep after 15 min of inactivity
- Takes 30 seconds to wake up
- Upgrade to paid ($7/month) for always-on

**Database:**
- Currently using SQLite (stored in container)
- Data will be lost on redeploy
- Upgrade to Render PostgreSQL for persistent data

**Environment Variables:**
- Update Flask's `AI_AGENT_API` after Next.js deploys
- Double-check Gemini API key is correct
- Keep SECRET_KEY secure

---

## 📞 Troubleshooting

**"Service is starting..."**
- Wait 2-3 minutes for initial build

**"Cannot connect to AI Agent"**
- Check Next.js deployed successfully
- Verify `AI_AGENT_API` environment variable

**"Invalid API Key"**
- Check Gemini key in Next.js service environment

**Still issues?**
- Check Render logs: Dashboard → Service → Logs
- Look for error messages
- Check GitHub Actions deployment logs

---

**Total Time: ~10-15 minutes** ⏱️
