#!/bin/bash

set -e

echo "Setting up MCP servers for local development..."

# Install Node.js and npm if not available
if ! command -v npm >/dev/null 2>&1; then
    echo "Installing Node.js and npm..."
    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

# Create MCP servers directory
mkdir -p /workspaces/a2a/mcp-servers

cd /workspaces/a2a/mcp-servers

# Clean up existing directories
echo "Cleaning up existing installations..."
rm -rf azure-devops-mcp azure-devops-mcp-main
rm -rf mcp-atlassian mcp-atlassian-main

# Download Azure DevOps MCP server
echo "Downloading Azure DevOps MCP server..."
curl -L https://github.com/microsoft/azure-devops-mcp/archive/refs/heads/main.zip -o azure-devops-mcp.zip
unzip -q azure-devops-mcp.zip
mv azure-devops-mcp-main azure-devops-mcp
rm azure-devops-mcp.zip

# Download Atlassian MCP server
echo "Downloading Atlassian MCP server..."
curl -L https://github.com/sooperset/mcp-atlassian/archive/refs/heads/main.zip -o mcp-atlassian.zip
unzip -q mcp-atlassian.zip
mv mcp-atlassian-main mcp-atlassian
rm mcp-atlassian.zip

# Set up Azure DevOps MCP
echo "Setting up Azure DevOps MCP server..."
cd azure-devops-mcp
if [ -f package.json ]; then
    echo "Installing Node.js dependencies..."
    npm install
elif [ -f requirements.txt ]; then
    echo "Installing Python dependencies..."
    pip3 install -r requirements.txt
fi

# Set up Atlassian MCP
echo "Setting up Atlassian MCP server..."
cd ../mcp-atlassian
if [ -f uv.lock ]; then
    echo "Installing dependencies with uv..."
    if ! command -v uv >/dev/null 2>&1; then
        echo "Installing uv..."
        pip3 install uv
    fi
    uv sync
elif [ -f requirements.txt ]; then
    echo "Installing Python dependencies with pip..."
    pip3 install -r requirements.txt
fi
# Install the package in development mode
pip3 install -e .

# Create example environment files with local defaults
cd /workspaces/a2a
echo "Creating local environment files..."

# Create devops .env with local defaults
cat > devops_agent/.env << 'EOF'
# Azure AI Project Configuration
PROJECT_ENDPOINT=https://your-project.cognitiveservices.azure.com/
MODEL_DEPLOYMENT_NAME=gpt-4

# Azure DevOps Configuration (Required for MCP server)
AZURE_DEVOPS_ORG=your-org-name
AZURE_DEVOPS_PAT=your-personal-access-token

# Local MCP Server Configuration (Default: Local server)
MCP_SERVER_URL=http://localhost:9000
MCP_SERVER_LABEL=azuredevops
EOF

# Create confluence .env with local defaults
cat > confluence_agent/.env << 'EOF'
# Azure AI Project Configuration
PROJECT_ENDPOINT=https://your-project.cognitiveservices.azure.com/
MODEL_DEPLOYMENT_NAME=gpt-4

# Confluence Configuration (Required for MCP server)
CONFLUENCE_URL=https://your-company.atlassian.net/wiki
CONFLUENCE_USERNAME=your.email@company.com
CONFLUENCE_API_TOKEN=your-confluence-api-token

# Optional: Jira Configuration (if using both Confluence and Jira)
JIRA_URL=https://your-company.atlassian.net
JIRA_USERNAME=your.email@company.com
JIRA_API_TOKEN=your-jira-api-token

# Local MCP Server Configuration (Default: Local Python server)
MCP_SERVER_URL=http://localhost:9001/mcp
MCP_SERVER_LABEL=atlassian

# Optional: Filter specific Confluence spaces
CONFLUENCE_SPACES_FILTER=DEV,TEAM,DOC

# Optional: Enable read-only mode
READ_ONLY_MODE=false

# Optional: Enable verbose logging
MCP_VERBOSE=true
MCP_LOGGING_STDOUT=true
EOF

echo "MCP servers setup complete!"
echo ""
echo "Next steps:"
echo "1. Configure your environment variables in:"
echo "   - devops_agent/.env"
echo "   - confluence_agent/.env"
echo ""
echo "2. Start MCP servers using the provided scripts:"
echo "   - ./scripts/start-devops-mcp.sh"
echo "   - ./scripts/start-atlassian-mcp.sh"
echo ""
echo "3. Start your agents:"
echo "   - cd devops_agent && python main.py"
echo "   - cd confluence_agent && python main.py"
