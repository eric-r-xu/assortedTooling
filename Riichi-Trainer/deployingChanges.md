  ### 1. Review the local changes

  On your Mac:

  cd /path/to/GitHub/Riichi-Trainer

  git status
  git diff
  git branch --show-current

  Your working branch should normally be a dev branch like 'ericrxu_dev'

  ### 2. Install dependencies and build

  npm ci
  npm run build

  The build must complete successfully before continuing. Warnings are acceptable unless they relate directly to the
  change.

  Confirm the output exists:

  test -f build/index.html && echo "Production build is ready"

  ### 3. Commit the source changes to Git

  Review the files that will be committed:

  git status

  Stage the intended changes:

  git add -A
  git diff --cached

  Commit them with a descriptive message:

  git commit -m "Describe the Riichi Trainer changes"

  Push the current branch to your GitHub fork:

  git push origin HEAD

  Because the current branch tracks origin/ericrxu_dev, this should update that branch on GitHub.

  Confirm everything is committed:

  git status

  It should report a clean working tree.

  ### 4. Preview the DigitalOcean upload

  The --dry-run command shows what will change without uploading anything:

  rsync -avn --delete \
    build/ \
    root@app.ericrxu.com:/srv/riichi-trainer/

  Review the output carefully. --delete removes obsolete production files, which is useful because Create React App
  generates new hashed filenames on every build.

  ### 5. Deploy to DigitalOcean

  Run the real upload:

  rsync -av --delete \
    build/ \
    root@app.ericrxu.com:/srv/riichi-trainer/

  For ordinary React source changes, you do not need to restart Flask, Gunicorn, or Nginx.

  ### 6. Verify the deployment

  curl -I https://app.ericrxu.com/riichi-trainer/
  curl -I https://app.ericrxu.com/riichi-trainer/manifest.json
  curl -I https://app.ericrxu.com/riichi-trainer/service-worker.js

  Each should return 200.

  Open the application:

  https://app.ericrxu.com/riichi-trainer/ (https://app.ericrxu.com/riichi-trainer/)

  Test the feature that changed. If an older version appears, use a private window or clear the service worker/site data
  because this app caches production assets.

  ### 7. Only when Nginx configuration changes

  React-only deployments do not require this. If /etc/nginx/sites-available/myproject was modified, run on the server:

  ssh root@app.ericrxu.com
  sudo nginx -t
  sudo systemctl reload nginx
  exit

  Only reload Nginx after nginx -t reports that the configuration is valid.
