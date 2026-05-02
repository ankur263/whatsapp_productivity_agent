# Home OS — Deployment Guide

Whenever you make changes to your code and want to push them to your live bot, follow these exact steps.

## Phase 1: Package and Upload
**Run these commands on your local Mac terminal:**

```bash
# 1. Go to your project folder
cd ~/Desktop/whatsapp_productivity_agent

# 2. Compress the code into a zip file (ignoring hidden/junk files)
tar --exclude='venv' --exclude='venv311' --exclude='__pycache__' \
    --exclude='.git' --exclude='data' --exclude='workspaces' --exclude='.local' \
    -czf /tmp/app.tgz .

# 3. Securely upload the zip file to your Google Cloud VM
gcloud compute scp /tmp/app.tgz whatsapp-agent:~/app.tgz --zone=us-central1-a
```

---

## Phase 2: Build and Restart
**Run these commands on your local Mac terminal to log into the server and deploy:**

```bash
# 1. SSH into the Google Cloud VM
gcloud compute ssh whatsapp-agent --zone=us-central1-a 

# 2. Unzip the new code into your app folder
cd ~/app && tar -xzf ~/app.tgz

# 3. Rebuild the Docker image with your new code
docker build -t whatsapp-agent .

# 4. Stop and remove the old container
docker stop whatsapp && docker rm whatsapp

# 5. Start the new container (your database is safely preserved in the volume mount)
docker run -d \
  --name whatsapp \
  --restart unless-stopped \
  --env-file ~/app/.env \
  -v ~/app/data:/app/data \
  -p 127.0.0.1:8000:8000 \
  whatsapp-agent

# 6. Watch the live logs to verify it started successfully
docker logs -f whatsapp
```

---

## Phase 3: How to Exit the VM
Once you are done checking the logs and want to return to your normal Mac terminal, you can disconnect from the VM using either of these two methods:

* **Method 1:** Type the word `exit` and press **Enter**.
* **Method 2:** Press **`Ctrl + D`** on your keyboard.

*(Note: If you were watching the logs using `docker logs -f`, press `Ctrl + C` first to stop watching the logs, and then type `exit`!)*