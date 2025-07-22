import os
import logging
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

# Configure OTEL exclusions before any other imports
os.environ.setdefault(
    "OTEL_INSTRUMENTATION_HTTP_EXCLUDED_URLS",
    "https://.*\\.in\\.applicationinsights\\.azure\\.com/.*,https://.*\\.applicationinsights\\.azure\\.com/.*"
)

# Disable OTEL HTTP instrumentation for Azure Monitor to prevent loops
os.environ.setdefault("OTEL_PYTHON_DISABLED_INSTRUMENTATIONS", "urllib3,requests")

import uvicorn
import asyncio
import time
import uuid
import tiktoken

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard
from a2a.utils import new_agent_text_message
from semantic_kernel.agents import CopilotStudioAgent, CopilotStudioAgentThread

from tracing import setup_azure_monitor_tracing, get_tracer
from tracing_utils import SpanManager

# Try to import evaluation - graceful fallback if not available
try:
    from evaluation_sdk import SDKEvaluationManager
    EVALUATION_AVAILABLE = True
except ImportError as e:
    logger = logging.getLogger(__name__)
    logger.warning(f"Evaluation SDK not available: {e}. Running without evaluation.")
    EVALUATION_AVAILABLE = False
    
    class SDKEvaluationManager:
        def __init__(self, tracer):
            pass
        async def evaluate_conversation(self, *args, **kwargs):
            pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Reduce Azure HTTP logging verbosity
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.monitor.opentelemetry").setLevel(logging.WARNING)

# --- Tracing and Evaluation Setup ---
setup_azure_monitor_tracing()
tracer = get_tracer(__name__)

class AzureDevOpsA2AExecutor(AgentExecutor):
    def __init__(self):
        self.agent = CopilotStudioAgent(name="AzureDevOpsAssistant", instructions="Use the available tools to create or view work items in Azure DevOps.")
        self.threads: dict[str, CopilotStudioAgentThread] = {}
        self.evaluation_manager = SDKEvaluationManager(tracer)
        self.span_manager = SpanManager(tracer)
        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base") 
        except Exception:
            self.tokenizer = None
            logger.warning("Failed to initialize tiktoken, falling back to word count approximation")

    def _count_tokens(self, text: str) -> int:
        """Count tokens using tiktoken or fallback to word count approximation."""
        if self.tokenizer:
            try:
                return len(self.tokenizer.encode(text))
            except Exception:
                pass
        # Fallback to word count approximation
        return len(text.split())

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input().strip()
        input_tokens = self._count_tokens(user_input)
        
        with self.span_manager.create_inference_span(context.context_id, user_input) as span:
            self.span_manager.set_token_usage(span, input_tokens)
            
            try:
                start_time = time.time()
                
                # Generate unique IDs for tracing
                thread_id = f"thread_{context.context_id}"
                run_id = f"run_{uuid.uuid4().hex}"
                message_id = f"msg_{uuid.uuid4().hex}"
                
                self.span_manager.set_thread_attributes(span, thread_id, context.context_id not in self.threads)
                self.span_manager.set_run_attributes(span, run_id, "in_progress")
                
                thread = self.threads.get(context.context_id)
                response = await self.agent.get_response(messages=user_input, thread=thread)
                
                if response and response.thread:
                    self.threads[context.context_id] = response.thread

                result = self._extract_content(response) or "I processed your request but couldn't generate a response."
                
                # Set final attributes
                output_tokens = self._count_tokens(result)
                self.span_manager.set_execution_timing(span, start_time)
                self.span_manager.set_token_usage(span, input_tokens, output_tokens)
                self.span_manager.set_run_attributes(span, run_id, "completed")
                self.span_manager.set_success_result(span, result)
                
                await event_queue.enqueue_event(new_agent_text_message(result))
                
                # Create assistant message span
                with self.span_manager.create_assistant_message_span(thread_id, run_id, message_id) as msg_span:
                    logger.info(f"Assistant message logged for thread {thread_id}")
                
                # Trigger evaluation asynchronously if available
                if EVALUATION_AVAILABLE:
                    asyncio.create_task(
                        self.evaluation_manager.evaluate_conversation(
                            thread_id, run_id, user_input, result, input_tokens, output_tokens
                        )
                    )
                
            except Exception as e:
                error_message = "Authentication error: Check COPILOT_STUDIO_* environment variables." if "403" in str(e) else f"Error: {e}"
                await event_queue.enqueue_event(new_agent_text_message(error_message))
                self.span_manager.set_error_result(span, e, error_message)

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

    logger.info("Starting agent server on http://localhost:8000")
    uvicorn.run(server.build(), host='0.0.0.0', port=8000)