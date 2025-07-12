# Group Chat with A2A and Azure AI Agents

This example demonstrates a multi-agent group chat orchestrating a remote A2A agent and an Azure AI Agent to collaboratively create and populate a support ticket.

## Agents

1.  **FormatterAgent (Remote A2A Agent)**: A standalone agent running on `http://localhost:9999`. Its skill is to format a user's request into a structured markdown ticket.
2.  **TicketPopulator (Azure AI Agent)**: An agent created using the Azure AI SDK. It has a (mock) tool to populate ticket details like `Assignee` and `Priority`.
3.  **UserProxyAgent (Local Agent)**: A simple agent that initiates the conversation and can terminate it.

## How to Run

### 1. Setup Environment

First, install the required dependencies and set up your environment variables.

```bash
# Install dependencies
pip install -r requirements.txt

# Create a .env file in this directory with your Azure AI details
# You can copy from the parent directory's .env if you have one
cp ../.env .
```

Your `.env` file should contain:
```
AZURE_AI_AGENT_ENDPOINT="your_endpoint"
AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME="your_deployment_name"
```

### 2. Start the A2A Formatter Agent

Open a new terminal and run the A2A agent server. This agent will listen for requests on port 9999.

```bash
python group_chat/a2a_formatter_agent.py
```

You should see output indicating the Uvicorn server is running. Keep this terminal open.

### 3. Run the Main Orchestration

In your original terminal, run the main script. This will start the group chat, which will connect to the A2A agent you just started.

```bash
python group_chat/main.py
```

## Expected Outcome

The `main.py` script will orchestrate the conversation:
1.  The `UserProxyAgent` will ask to create a ticket.
2.  The `TicketPopulator` will ask the `FormatterAgent` to format the ticket.
3.  The `FormatterAgent` (running in the other terminal) will receive the request, format the ticket, and send it back.
4.  The `TicketPopulator` will receive the formatted ticket and use its tool to populate the `Assignee` and `Priority` fields.
5.  The `UserProxyAgent` will receive the final, populated ticket and terminate the chat.

You will see the conversation flow printed to the console, with panels indicating which agent is speaking.
