import os
import uvicorn
import asyncio
from dotenv import load_dotenv
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard
from a2a.utils import new_agent_text_message
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from oauth_auth import get_atlassian_bearer_token

load_dotenv()

class ConfluenceA2AExecutor(AgentExecutor):
    def __init__(self):
        self.conversations = {}
        self.atlassian_token = None
        self.client = AzureOpenAI(
            base_url=f"{os.environ['AZURE_OPENAI_ENDPOINT']}/openai/v1/",
            azure_ad_token_provider=get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"),
            api_version="preview"
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            if not self.atlassian_token:
                self.atlassian_token = await get_atlassian_bearer_token()
            
            conversation = self.conversations.get(context.context_id, {})
            mcp_config = {
                "type": "mcp", "server_url": os.environ["MCP_SERVER_URL"], "server_label": os.environ["MCP_SERVER_LABEL"],
                "require_approval": "never", "allowed_tools": ["getConfluencePage"], "headers": {"Authorization": f"Bearer {self.atlassian_token}"}
            }
            
            input_data = [{"role": "user", "content": context.get_user_input()}]
            if not conversation.get('last_response_id'):
                input_data.insert(0, {"role": "system", "content": "You are a Confluence assistant. Use MCP tools to search and analyze content."})
            
            response = await asyncio.to_thread(
                self.client.responses.create,
                model=os.environ["MODEL_DEPLOYMENT_NAME"],
                previous_response_id=conversation.get('last_response_id'),
                input=input_data,
                tools=[mcp_config]
            )
            
            self.conversations[context.context_id] = {'last_response_id': response.id}
            
            for output_item in response.output or []:
                if output_item.type == "message" and output_item.content:
                    for content in output_item.content:
                        if hasattr(content, 'text'):
                            await event_queue.enqueue_event(new_agent_text_message(content.text))
                            return
            
            await event_queue.enqueue_event(new_agent_text_message("Operation completed."))
                
        except Exception as e:
            if "401" in str(e):
                self.atlassian_token = None
            await event_queue.enqueue_event(new_agent_text_message(f"Error: {str(e)}"))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.conversations.pop(context.context_id, None)
        await event_queue.enqueue_event(new_agent_text_message("Cancelled."))

if __name__ == "__main__":
    agent_card = AgentCard(name='Confluence MCP Agent', description='AI agent for Confluence documentation using MCP tools.',
                          capabilities=AgentCapabilities(streaming=False), url='http://localhost:8002/', version='1.0.0',
                          defaultInputModes=['text'], defaultOutputModes=['text'], skills=[])
    
    executor = ConfluenceA2AExecutor()
    server = A2AStarletteApplication(agent_card=agent_card, http_handler=DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore()))
    
    async def start_server():
        try:
            executor.atlassian_token = await get_atlassian_bearer_token()
        except:
            pass
        await uvicorn.Server(uvicorn.Config(server.build(), host='0.0.0.0', port=8002)).serve()
    
    asyncio.run(start_server())