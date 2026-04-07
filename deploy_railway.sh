#!/bin/bash

# 🚀 HELEN OS - Automatic Railway Deployment Script
# This script deploys HELEN OS to Railway with one command

set -e

echo "🧠 HELEN OS → Railway Deployment"
echo "=================================="
echo ""

# Check if Railway CLI is installed
if ! command -v railway &> /dev/null; then
    echo "📦 Installing Railway CLI..."
    npm install -g @railway/cli
fi

echo ""
echo "🔑 Railway Login Required"
echo "========================="
echo ""
echo "1. You'll be redirected to Railway login page"
echo "2. Sign in with your GitHub account"
echo "3. Copy your API token from: https://railway.app/account/tokens"
echo ""
read -p "Press Enter once you have your Railway token ready..."

echo ""
echo "📝 Enter your Railway API Token:"
read -s RAILWAY_TOKEN

export RAILWAY_TOKEN=$RAILWAY_TOKEN

echo ""
echo "🚀 Authenticating with Railway..."
railway login --token $RAILWAY_TOKEN

echo ""
echo "📂 Initializing Railway project..."
railway init --name helen-os

echo ""
echo "🐳 Building and deploying..."
railway up

echo ""
echo "✅ DEPLOYMENT COMPLETE!"
echo ""
echo "🎉 Your HELEN OS is now LIVE!"
echo ""
echo "📍 To get your Railway URL:"
echo "   1. Go to: https://railway.app/dashboard"
echo "   2. Click your helen-os project"
echo "   3. Go to 'Deployments' tab"
echo "   4. Copy the 'Railway Domain' URL"
echo ""
echo "🧪 Test your deployment:"
echo "   curl https://your-url/health"
echo ""
