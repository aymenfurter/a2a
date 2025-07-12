#!/bin/bash

set -e

echo "Starting Azure DevOps MCP server..."

# Load environment variables
ENV_FILE="/workspaces/a2a/devops_agent/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: Environment file not found at $ENV_FILE"
    echo "Please copy and configure devops_agent/.env.example to devops_agent/.env"
    exit 1
fi

# Export environment variables
export $(cat "$ENV_FILE" | grep -v '^#' | xargs)

# Check if required variables are set
if [ -z "$AZURE_DEVOPS_ORG" ] || [ -z "$AZURE_DEVOPS_PAT" ]; then
    echo "Error: Please configure AZURE_DEVOPS_ORG and AZURE_DEVOPS_PAT in $ENV_FILE"
    exit 1
fi

echo "Starting Azure DevOps MCP server on port 9000..."
echo "Organization: $AZURE_DEVOPS_ORG"
echo "Server will be available at: http://localhost:9000"

# Install Node.js and npm if not available
if ! command -v npm >/dev/null 2>&1; then
    echo "Node.js/npm not found. Installing..."
    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

# Start the MCP server
cd /workspaces/a2a/mcp-servers/azure-devops-mcp

# Check if it's a Node.js project and install dependencies if needed
if [ -f "package.json" ]; then
    if [ ! -d "node_modules" ]; then
        echo "Installing Node.js dependencies..."
        npm install
    fi
    echo "Starting Node.js MCP server..."
    # Use the correct command with organization parameter
    npm start -- "$AZURE_DEVOPS_ORG"
elif [ -f "src/index.js" ]; then
    node src/index.js "$AZURE_DEVOPS_ORG"
elif [ -f "index.js" ]; then
    node index.js "$AZURE_DEVOPS_ORG"
else
    echo "Error: Could not find Azure DevOps MCP server entry point"
    echo "Available files:"
    ls -la
    exit 1
fi
