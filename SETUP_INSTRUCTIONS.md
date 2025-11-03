# GitHub Pages Setup Instructions

Your local repository is ready! Follow these steps to deploy it to GitHub Pages.

## Step 1: Create GitHub Repository

1. Go to https://github.com/new
2. Repository name: `library-jobs` (or any name you prefer)
3. Description: "Consolidated library and archives job feed"
4. Choose **Public** (required for free GitHub Pages)
5. **DO NOT** initialize with README, .gitignore, or license
6. Click "Create repository"

## Step 2: Connect Your Local Repository

After creating the repo, GitHub will show you commands. Run this on your Pi:

### Option A: HTTPS (easier, uses Personal Access Token)
```bash
cd /home/bowser/job-feed-site
git remote add origin https://github.com/YOUR-USERNAME/library-jobs.git
git push -u origin main
```

**Note**: When prompted for password, use a Personal Access Token (not your GitHub password)
- Create token at: https://github.com/settings/tokens
- Select scope: `repo` (full control of private repositories)
- Save the token securely - you'll need it for pushes

### Option B: SSH (more secure, requires SSH key setup)
```bash
cd /home/bowser/job-feed-site
git remote add origin git@github.com:YOUR-USERNAME/library-jobs.git
git push -u origin main
```

**SSH Setup** (if you haven't done this):
```bash
# Generate SSH key
ssh-keygen -t ed25519 -C "your_email@example.com"

# Copy public key
cat ~/.ssh/id_ed25519.pub

# Add to GitHub at: https://github.com/settings/keys
```

## Step 3: Enable GitHub Pages

1. Go to your repository on GitHub
2. Click **Settings** → **Pages**
3. Under "Build and deployment":
   - Source: **Deploy from a branch**
   - Branch: **main** / (root)
4. Click **Save**
5. Wait 1-2 minutes for deployment

Your site will be live at:
**https://YOUR-USERNAME.github.io/library-jobs/**

## Step 4: Set Up Automatic Updates

Add to your crontab to deploy after each aggregation:

```bash
crontab -e
```

Add this line (runs every 6 hours, 5 minutes after aggregation):
```
5 */6 * * * cd /home/bowser/job-feed-site && ./deploy.sh >> /home/bowser/job-aggregator/logs/deploy.log 2>&1
```

Or manually deploy anytime:
```bash
cd /home/bowser/job-feed-site
./deploy.sh
```

## Step 5: Custom Domain (Optional)

To use your own domain (e.g., jobs.yourdomain.com):

1. Create a CNAME file:
```bash
echo "jobs.yourdomain.com" > /home/bowser/job-feed-site/CNAME
git add CNAME
git commit -m "Add custom domain"
git push
```

2. Add DNS records at your domain provider:
   - Type: `CNAME`
   - Name: `jobs`
   - Value: `YOUR-USERNAME.github.io`

3. In GitHub repo settings → Pages, add your custom domain
4. Enable "Enforce HTTPS" (wait a few minutes for cert)

## Testing

After deployment, check:
- Main site: `https://YOUR-USERNAME.github.io/library-jobs/`
- RSS feed: `https://YOUR-USERNAME.github.io/library-jobs/consolidated-jobs.xml`
- JSON API: `https://YOUR-USERNAME.github.io/library-jobs/jobs.json`

## Updating README Links

Don't forget to update the placeholder URLs in README.md:
```bash
cd /home/bowser/job-feed-site
# Edit README.md and replace:
# - YOUR-USERNAME with your actual GitHub username
# - YOUR-REPO with your actual repo name
git add README.md
git commit -m "Update README links"
git push
```

## Troubleshooting

**"fatal: remote origin already exists"**
```bash
git remote remove origin
# Then add it again
```

**"Permission denied (publickey)"**
- You need to set up SSH keys (see Option B above)
- Or use HTTPS with a Personal Access Token

**"404 Page Not Found after deployment"**
- Wait 2-3 minutes after enabling Pages
- Check Settings → Pages shows "Your site is live at..."
- Verify main branch has index.html

**Deploy script says "No remote 'origin' configured"**
- Complete Step 2 first to connect to GitHub

## Support

For issues, check:
- GitHub Pages documentation: https://docs.github.com/pages
- Your deployment logs: `/home/bowser/job-aggregator/logs/deploy.log`

---

🎉 Once setup is complete, your job feed will be publicly accessible and automatically updated every 6 hours!
