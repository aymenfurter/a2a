import logging
import uvicorn

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard
from a2a.utils import new_agent_text_message
from semantic_kernel.agents import CopilotStudioAgent, CopilotStudioAgentThread

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AzureDevOpsA2AExecutor(AgentExecutor):
    def __init__(self):
        self.agent = CopilotStudioAgent(name="AzureDevOpsAssistant", instructions="Use the available tools to create or view work items in Azure DevOps.")
        self.threads: dict[str, CopilotStudioAgentThread] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            thread = self.threads.get(context.context_id)
            response = await self.agent.get_response(messages=context.get_user_input(), thread=thread)
            
            if response and response.thread:
                self.threads[context.context_id] = response.thread

            result = self._extract_content(response) or "I processed your request but couldn't generate a response."
            await event_queue.enqueue_event(new_agent_text_message(result))
        except Exception as e:
            error = "Authentication error: Check COPILOT_STUDIO_* environment variables." if "403" in str(e) else f"Error: {e}"
            await event_queue.enqueue_event(new_agent_text_message(error))

    def _extract_content(self, response) -> str:
        if not response:
            return ""
        
        # Try message items, then content, then response content
        for obj in [getattr(response, 'message', None), getattr(response, 'content', None)]:
            if obj:
                if hasattr(obj, 'items') and obj.items:
                    for item in obj.items:
                        if hasattr(item, 'text') and item.text:
                            return item.text
                if hasattr(obj, 'content') and obj.content:
                    return str(obj.content)
        return ""

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("Cancel operation is not supported.")

if __name__ == "__main__":
    agent_card = AgentCard(
        name='User Story Formating Agent',
        description='A specialized Azure DevOps assistant that helps structure and organize work items effectively. Create well-formatted user stories, tasks, bugs, and epics with proper acceptance criteria, clear descriptions, and appropriate field values through natural language interaction.',
        capabilities=AgentCapabilities(streaming=False),
        url='http://localhost:8000/',
        version='1.0.0',
        defaultInputModes=['text'],
        defaultOutputModes=['text'],
        skills=[],
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=DefaultRequestHandler(agent_executor=AzureDevOpsA2AExecutor(), task_store=InMemoryTaskStore()),
    )

    logger.info("Starting Azure DevOps A2A Agent server on http://localhost:8000")
    uvicorn.run(server.build(), host='0.0.0.0', port=8000)