#!/bin/bash

set -e

echo "Starting Atlassian MCP server..."

# Load environment variables
ENV_FILE="/workspaces/a2a/confluence_agent/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: Environment file not found at $ENV_FILE"
    echo "Please copy and configure confluence_agent/.env.example to confluence_agent/.env"
    exit 1
fi

# Export environment variables
export $(cat "$ENV_FILE" | grep -v '^#' | xargs)

# Check if required variables are set
if [ -z "$CONFLUENCE_URL" ] || [ -z "$CONFLUENCE_USERNAME" ] || [ -z "$CONFLUENCE_API_TOKEN" ]; then
    echo "Error: Please configure CONFLUENCE_URL, CONFLUENCE_USERNAME, and CONFLUENCE_API_TOKEN in $ENV_FILE"
    exit 1
fi

echo "Starting Atlassian MCP server on port 9001..."
echo "Confluence URL: $CONFLUENCE_URL"
echo "Server will be available at: http://localhost:9001"

# Start the Python MCP server
cd /workspaces/a2a/mcp-servers/mcp-atlassian

# Use the mcp-atlassian command with stdio transport (default)
echo "Starting Atlassian MCP server with stdio transport..."
mcp-atlassian --verbose --verbose
    # Look for console scripts in pyproject.toml
    if grep -q "\[project.scripts\]" pyproject.toml; then
        SCRIPT_NAME=$(grep -A 5 "\[project.scripts\]" pyproject.toml | grep "=" | head -1 | cut -d'=' -f1 | tr -d ' ')
        if [ -n "$SCRIPT_NAME" ]; then
            echo "Starting with console script: $SCRIPT_NAME"
            $SCRIPT_NAME --transport stdio --verbose
        else
            echo "No console scripts found, trying mcp-atlassian command..."
            mcp-atlassian --transport stdio --verbose
        fi
    else
        echo "Starting with installed package entry point..."
        python3 -c "import mcp_atlassian; print('Package imported successfully')"
        # Try common MCP entry patterns
        python3 -m mcp_atlassian.server --transport stdio --verbose || \
        python3 -m mcp_atlassian --transport stdio --verbose || \
        mcp-atlassian --transport stdio --verbose
    fi
elif [ -f "src/mcp_atlassian/__main__.py" ]; then
    echo "Starting with __main__.py entry point..."
    python3 -m src.mcp_atlassian --transport stdio --verbose
else
    echo "Error: Could not find MCP Atlassian server entry point"
    echo "Let's check the project structure:"
    echo "Source directory contents:"
    ls -la src/ 2>/dev/null || echo "No src directory found"
    echo "Available Python modules in src/mcp_atlassian/:"
    ls -la src/mcp_atlassian/ 2>/dev/null || echo "No src/mcp_atlassian directory found"
    echo "Pyproject.toml entry points:"
    grep -A 10 "\[project.scripts\]" pyproject.toml 2>/dev/null || echo "No scripts found in pyproject.toml"
    exit 1
fi
