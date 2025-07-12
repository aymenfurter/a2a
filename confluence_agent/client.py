import asyncio
from uuid import uuid4
import httpx

from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    Message, MessageSendConfiguration, MessageSendParams,
    SendMessageRequest, Task, TextPart,
)

async def main():
    """A simple test client for the Confluence A2A agent."""
    agent_url = "http://localhost:8002"
    print(f"Connecting to Confluence A2A agent at {agent_url}...")

    try:
        async with httpx.AsyncClient(timeout=30) as httpx_client:
            agent_card = await A2ACardResolver(httpx_client, agent_url).get_agent_card()
            print(f"Connected to: {agent_card.name}")
            
            client = A2AClient(httpx_client, agent_card=agent_card)
            context_id = f"test-session-{uuid4().hex}"
            
            # Test extracting todos from a Confluence page
            confluence_url = "https://aymenfurter.atlassian.net/wiki/spaces/~557058e4fa0cdeeab349c084c43e9310ea2ed3/pages/65706/2025-07-12+Besprechungsnotizen"
            test_message = f"Please analyze the Confluence page: {confluence_url} and extract any open todos or action items as a list."
            
            response = await client.send_message(SendMessageRequest(
                id=str(uuid4()),
                params=MessageSendParams(
                    message=Message(
                        role='user',
                        parts=[TextPart(text=test_message)],
                        messageId=str(uuid4()),
                        contextId=context_id,
                    ),
                    configuration=MessageSendConfiguration(acceptedOutputModes=['text']),
                )
            ))
            
            event = response.root.result
            if isinstance(event, Task):
                print(f"✓ Task created! ID: {event.id}")
            elif isinstance(event, Message) and event.parts:
                print("Extracted todos/action items:")
                for part in event.parts:
                    text = getattr(part, 'text', None) or getattr(getattr(part, 'root', None), 'text', None) or str(part)
                    if text and not text.startswith('<'):
                        print(text)
            else:
                print(f"Response: {event}")
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
