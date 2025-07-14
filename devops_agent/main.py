import logging
import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard
from a2a.utils import new_agent_text_message
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.agents.models import OpenApiTool, OpenApiAnonymousAuthDetails

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DevOpsA2AExecutor(AgentExecutor):
    def __init__(self):
        self.agent = None
        self.project_client = None
        self.agents_client = None
        self.threads = {}
        self._setup_azure_client()

    def _validate_environment(self):
        required_vars = ["PROJECT_ENDPOINT", "MODEL_DEPLOYMENT_NAME", "LOGIC_APP_URL"]
        missing = [var for var in required_vars if not os.environ.get(var)]
        if missing:
            raise ValueError(f"Missing env vars: {', '.join(missing)}")

    def _create_openapi_spec(self):
        url = os.environ["LOGIC_APP_URL"]
        base_url = url.split('/workflows/')[0]
        path = '/workflows/' + url.split('/workflows/')[1].split('?')[0]
        
        spec = {
            "openapi": "3.1.0",
            "info": {"title": "DevOps Work Item Creator", "version": "1.0.0"},
            "servers": [{"url": base_url}],
            "paths": {
                path: {
                    "post": {
                        "operationId": "createWorkItem",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "title": {"type": "string"},
                                            "description": {"type": "string"},
                                            "workItemType": {"type": "string", "enum": ["User Story", "Task", "Bug", "Feature", "Epic"], "default": "User Story"}
                                        },
                                        "required": ["title", "description"]
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "Success"}}
                    }
                }
            }
        }
        
        if '?' in url:
            params = []
            for param in url.split('?')[1].split('&'):
                if '=' in param:
                    name, value = param.split('=', 1)
                    params.append({"name": name, "in": "query", "required": True, "schema": {"type": "string", "default": value}})
            spec["paths"][path]["post"]["parameters"] = params
        
        return spec

    def _setup_azure_client(self):
        try:
            self._validate_environment()
            
            self.project_client = AIProjectClient(
                endpoint=os.environ["PROJECT_ENDPOINT"],
                credential=DefaultAzureCredential(),
            )
            self.agents_client = self.project_client.agents
            
            openapi_tool = OpenApiTool(
                name="create_work_item",
                spec=self._create_openapi_spec(),
                description="Create work items in Azure DevOps",
                auth=OpenApiAnonymousAuthDetails()
            )
            
            self.agent = self.agents_client.create_agent(
                model=os.environ["MODEL_DEPLOYMENT_NAME"],
                name="devops-logic-app-agent",
                instructions="You are an Azure DevOps assistant. Create work items using the create_work_item operation with title, description, and workItemType parameters.",
                tools=openapi_tool.definitions,
            )
            
        except Exception as e:
            logger.error(f"Setup failed: {e}")

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            if not self.agent:
                await event_queue.enqueue_event(new_agent_text_message("Agent not initialized"))
                return
                
            thread = self.threads.get(context.context_id)
            if not thread:
                thread = self.agents_client.threads.create()
                self.threads[context.context_id] = thread
            
            self.agents_client.messages.create(
                thread_id=thread.id,
                role="user",
                content=context.get_user_input(),
            )
            
            run = self.agents_client.runs.create_and_process(thread_id=thread.id, agent_id=self.agent.id)
            
            if run.status == "failed":
                await event_queue.enqueue_event(new_agent_text_message(f"Run failed: {run.last_error}"))
                return
                    
            messages = list(self.agents_client.messages.list(thread_id=thread.id))
            for msg in reversed(messages):
                if msg.role == "assistant" and msg.content:
                    for content_item in msg.content:
                        if hasattr(content_item, 'text') and content_item.text:
                            await event_queue.enqueue_event(new_agent_text_message(content_item.text.value))
                            return
            
            await event_queue.enqueue_event(new_agent_text_message("No response generated"))
                
        except Exception as e:
            await event_queue.enqueue_event(new_agent_text_message(f"Error: {e}"))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(new_agent_text_message("Operation cancelled"))

if __name__ == "__main__":
    agent_card = AgentCard(
        name='Azure DevOps Logic App Agent',
        description='Creates Azure DevOps work items using Logic Apps',
        capabilities=AgentCapabilities(streaming=False),
        url='http://localhost:8001/',
        version='1.0.0',
        defaultInputModes=['text'],
        defaultOutputModes=['text'],
        skills=[],
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=DefaultRequestHandler(agent_executor=DevOpsA2AExecutor(), task_store=InMemoryTaskStore()),
    )

    uvicorn.run(server.build(), host='0.0.0.0', port=8001)