import asyncio
from uuid import uuid4
import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import Message, MessageSendConfiguration, MessageSendParams, SendMessageRequest, Task, TextPart

async def main():
    agent_url = "http://localhost:8002"
    
    async with httpx.AsyncClient(timeout=30) as httpx_client:
        agent_card = await A2ACardResolver(httpx_client, agent_url).get_agent_card()
        client = A2AClient(httpx_client, agent_card=agent_card)
        context_id = f"test-session-{uuid4().hex}"
        
        confluence_url = "https://aymenfurter.atlassian.net/wiki/spaces/~557058e4fa0cdeeab349c084c43e9310ea2ed3/pages/65706/2025-07-12+Besprechungsnotizen"
        test_message = f"Give me the content for page: {confluence_url} extract any open todos or action items as a list."
        
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
        if isinstance(event, Message) and event.parts:
            for part in event.parts:
                text = getattr(part, 'text', None) or str(part)
                if text and not text.startswith('<'):
                    print(text)
        else:
            print(f"Response: {event}")

if __name__ == "__main__":
    asyncio.run(main())