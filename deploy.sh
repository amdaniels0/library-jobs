#!/bin/bash
# Deploy job feed to GitHub Pages

set -e

echo "========================================="
echo "   Deploying Job Feed to GitHub Pages"
echo "========================================="

# Copy latest files from aggregator output
echo "📋 Copying latest job feed files..."
cp /home/bowser/job-aggregator/output/index.html .
cp /home/bowser/job-aggregator/output/consolidated-jobs.xml .
cp /home/bowser/job-aggregator/output/jobs.json .

# Check if we have changes
if [[ -z $(git status -s) ]]; then
    echo "✓ No changes to deploy"
    exit 0
fi

# Stage files
echo "📦 Staging files..."
git add index.html consolidated-jobs.xml jobs.json

# Commit with timestamp
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
echo "💾 Committing changes..."
git commit -m "Update job feed - $TIMESTAMP"

# Push to GitHub
echo "🚀 Pushing to GitHub..."
if git remote get-url origin > /dev/null 2>&1; then
    git push origin main
    echo "✅ Deployment complete!"
    echo "🌐 Your site will be live at: https://YOUR-USERNAME.github.io/YOUR-REPO/"
else
    echo "❌ No remote 'origin' configured!"
    echo ""
    echo "Please set up your GitHub remote first:"
    echo "  git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git"
    echo ""
    echo "Or use SSH:"
    echo "  git remote add origin git@github.com:YOUR-USERNAME/YOUR-REPO.git"
    exit 1
fi
