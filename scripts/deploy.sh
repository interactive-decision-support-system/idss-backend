#!/bin/bash
# IDSS Deployment Script for DigitalOcean Droplet
# Usage: ./scripts/deploy.sh <droplet-ip> <ssh-key-path>

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
DROPLET_IP=${1:-""}
SSH_KEY=${2:-"~/.ssh/id_rsa"}
REMOTE_USER="root"
REMOTE_DIR="/opt/idss"
LOCAL_DATA_DIR="./data"

# Validate arguments
if [ -z "$DROPLET_IP" ]; then
    echo -e "${RED}Error: Please provide droplet IP address${NC}"
    echo "Usage: ./scripts/deploy.sh <droplet-ip> [ssh-key-path]"
    exit 1
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}IDSS Deployment Script${NC}"
echo -e "${GREEN}========================================${NC}"
echo "Target: $REMOTE_USER@$DROPLET_IP"
echo "Remote directory: $REMOTE_DIR"
echo ""

# Function to run remote commands
remote_exec() {
    ssh -i "$SSH_KEY" "$REMOTE_USER@$DROPLET_IP" "$@"
}

# Function to copy files
remote_copy() {
    scp -i "$SSH_KEY" -r "$1" "$REMOTE_USER@$DROPLET_IP:$2"
}

# Step 1: Setup server (first time only)
echo -e "${YELLOW}Step 1: Setting up server...${NC}"
remote_exec << 'SETUP_SCRIPT'
# Update system
apt-get update && apt-get upgrade -y

# Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
fi

# Install Docker Compose if not present
if ! command -v docker-compose &> /dev/null; then
    echo "Installing Docker Compose..."
    apt-get install -y docker-compose-plugin
    ln -sf /usr/libexec/docker/cli-plugins/docker-compose /usr/local/bin/docker-compose || true
fi

# Create app directory
mkdir -p /opt/idss/data

echo "Server setup complete!"
SETUP_SCRIPT

# Step 2: Copy application files
echo -e "${YELLOW}Step 2: Copying application files...${NC}"
remote_copy "Dockerfile" "$REMOTE_DIR/"
remote_copy "docker-compose.yml" "$REMOTE_DIR/"
remote_copy "requirements.txt" "$REMOTE_DIR/"
remote_copy "idss" "$REMOTE_DIR/"
remote_copy "config" "$REMOTE_DIR/"

# Step 3: Copy data files (this will take a while)
echo -e "${YELLOW}Step 3: Copying data files (this may take a while)...${NC}"
if [ -d "$LOCAL_DATA_DIR" ]; then
    echo "Syncing data directory..."
    rsync -avz --progress -e "ssh -i $SSH_KEY" \
        "$LOCAL_DATA_DIR/" "$REMOTE_USER@$DROPLET_IP:$REMOTE_DIR/data/"
else
    echo -e "${RED}Warning: Local data directory not found at $LOCAL_DATA_DIR${NC}"
    echo "You'll need to manually copy your data files."
fi

# Step 4: Create .env file on server
echo -e "${YELLOW}Step 4: Setting up environment...${NC}"
if [ -f ".env" ]; then
    remote_copy ".env" "$REMOTE_DIR/"
else
    echo -e "${YELLOW}No .env file found. Creating template...${NC}"
    remote_exec "cat > $REMOTE_DIR/.env << 'EOF'
OPENAI_API_KEY=your-openai-api-key-here
EOF"
    echo -e "${RED}Please update $REMOTE_DIR/.env with your OpenAI API key${NC}"
fi

# Step 5: Build and start
echo -e "${YELLOW}Step 5: Building and starting application...${NC}"
remote_exec << DEPLOY_SCRIPT
cd $REMOTE_DIR
docker-compose down || true
docker-compose build --no-cache
docker-compose up -d
DEPLOY_SCRIPT

# Step 6: Verify deployment
echo -e "${YELLOW}Step 6: Verifying deployment...${NC}"
sleep 10  # Wait for container to start
remote_exec "docker-compose -f $REMOTE_DIR/docker-compose.yml logs --tail=50"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Deployment complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Your API should be available at:"
echo "  http://$DROPLET_IP:8000"
echo ""
echo "Health check: http://$DROPLET_IP:8000/"
echo "API docs:     http://$DROPLET_IP:8000/docs"
echo "Status:       http://$DROPLET_IP:8000/status"
echo ""
echo "Useful commands:"
echo "  View logs:     ssh -i $SSH_KEY $REMOTE_USER@$DROPLET_IP 'cd $REMOTE_DIR && docker-compose logs -f'"
echo "  Restart:       ssh -i $SSH_KEY $REMOTE_USER@$DROPLET_IP 'cd $REMOTE_DIR && docker-compose restart'"
echo "  Stop:          ssh -i $SSH_KEY $REMOTE_USER@$DROPLET_IP 'cd $REMOTE_DIR && docker-compose down'"
