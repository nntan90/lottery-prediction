# GitHub Secrets Configuration Guide

## ❌ Current Error
```
supabase._sync.client.SupabaseException: Invalid API key
```

This error occurs because GitHub Actions cannot access Supabase credentials.

## ✅ Solution: Set GitHub Secrets

### Step 1: Get Supabase Credentials

1. Go to [Supabase Dashboard](https://supabase.com/dashboard)
2. Select your project: `islcxaqdqhwgcqkdozeq`
3. Go to **Settings** → **API**
4. Copy these values:
   - **Project URL**: `https://islcxaqdqhwgcqkdozeq.supabase.co`
   - **service_role key** (NOT anon key!)

### Step 2: Add Secrets to GitHub

1. Go to your GitHub repository: `https://github.com/nntan90/lottery-prediction`
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add these 2 secrets:

#### Secret 1: SUPABASE_URL
- **Name**: `SUPABASE_URL`
- **Value**: `https://islcxaqdqhwgcqkdozeq.supabase.co`

#### Secret 2: SUPABASE_SERVICE_KEY
- **Name**: `SUPABASE_SERVICE_KEY`  
- **Value**: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlzbGN4YXFkcWh3Z2Nxa2RvemVxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MDkwNjUwMCwiZXhwIjoyMDg2NDgyNTAwfQ.K9oBxjv77u-Rz1LBfy1UfPGRnxrRYvpdux3p8ChFpNU`

### Step 3: Verify

After adding secrets:
1. Go to **Actions** tab
2. Click on the failed workflow run
3. Click **Re-run all jobs**
4. Check if it succeeds

## 📝 Notes

- **NEVER commit** these secrets to Git
- Use **service_role key**, not **anon key**
- Secrets are encrypted and only accessible to GitHub Actions

## 🔍 How to Check if Secrets are Set

1. Go to **Settings** → **Secrets and variables** → **Actions**
2. You should see:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
3. You cannot view the values, only update them

## ✅ Expected Result

After setting secrets correctly, the workflow should output:
```
🎯 Generating XSMN prediction...
📊 Loaded 90 historical records
✅ Prediction saved for 2026-02-15
```
