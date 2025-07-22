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
import tiktoken
from dotenv import load_dotenv

# A2A Imports
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard
from a2a.utils import new_agent_text_message

# Azure AI Imports
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.agents.models import OpenApiTool, OpenApiAnonymousAuthDetails, RunStatus

from tracing import setup_azure_monitor_tracing, get_tracer
from evaluation import EvaluationManager
from tracing_utils import SpanManager

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)



# Reduce Azure HTTP logging verbosity
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.monitor.opentelemetry").setLevel(logging.WARNING)

# --- Tracing and Evaluation Setup ---
setup_azure_monitor_tracing()
tracer = get_tracer(__name__)

class DevOpsA2AExecutor(AgentExecutor):
    def __init__(self):
        self._validate_environment()
        self.project_client = AIProjectClient(
            endpoint=os.environ["PROJECT_ENDPOINT"],
            credential=DefaultAzureCredential(),
        )
        self.agents_client = self.project_client.agents
        self.threads = {}
        self.agent = None 
        self.evaluation_manager = EvaluationManager(self.project_client, tracer)
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

    async def initialize_agent(self):
        """Asynchronously creates and initializes the agent if it doesn't exist."""
        if self.agent:
            return
        try:
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
            logger.info(f"DevOps agent created successfully with ID: {self.agent.id}")
        except Exception as e:
            logger.error(f"Agent initialization failed: {e}", exc_info=True)
            raise

    def _validate_environment(self):
        """Ensure all required environment variables are set."""
        required_vars = ["PROJECT_ENDPOINT", "MODEL_DEPLOYMENT_NAME", "LOGIC_APP_URL"]
        if not all(os.getenv(var) for var in required_vars):
            raise ValueError(f"Missing required environment variables. Please check your .env file.")

    def _create_openapi_spec(self):
        """Generates the OpenAPI specification for the Logic App trigger."""
        url = os.environ["LOGIC_APP_URL"]
        base_url = url.split('/workflows/')[0]
        path = '/workflows/' + url.split('/workflows/')[1].split('?')[0]
        spec = { "openapi": "3.1.0", "info": {"title": "DevOps Work Item Creator", "version": "1.0.0"}, "servers": [{"url": base_url}], "paths": { path: { "post": { "operationId": "createWorkItem", "requestBody": { "required": True, "content": { "application/json": { "schema": { "type": "object", "properties": { "title": {"type": "string"}, "description": {"type": "string"}, "workItemType": {"type": "string", "enum": ["User Story", "Task", "Bug", "Feature", "Epic"], "default": "User Story"} }, "required": ["title", "description"] } } } }, "responses": {"200": {"description": "Success"}} } } } }
        if '?' in url:
            params = [{"name": p.split('=')[0], "in": "query", "required": True, "schema": {"type": "string", "default": p.split('=')[1]}} for p in url.split('?')[1].split('&') if '=' in p]
            spec["paths"][path]["post"]["parameters"] = params
        return spec

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Handles an incoming request, runs the agent, and triggers evaluation."""
        input_text = context.get_user_input()
        input_tokens = self._count_tokens(input_text)
        
        with self.span_manager.create_inference_span(context.context_id, input_text) as span:
            self.span_manager.set_token_usage(span, input_tokens)
            
            try:
                start_time = time.time()
                
                await self.initialize_agent()
                if not self.agent:
                    await event_queue.enqueue_event(new_agent_text_message("Agent not initialized. Check logs for errors."))
                    self.span_manager.set_failure_result(span, "Agent not initialized")
                    return

                # Handle thread creation/retrieval
                thread = self.threads.get(context.context_id)
                is_new_thread = thread is None
                if is_new_thread:
                    thread = self.agents_client.threads.create()
                    self.threads[context.context_id] = thread
                
                self.span_manager.set_thread_attributes(span, thread.id, is_new_thread)

                # Create message and run
                self.agents_client.messages.create(thread_id=thread.id, role="user", content=input_text)
                run = self.agents_client.runs.create_and_process(thread_id=thread.id, agent_id=self.agent.id)
                
                self.span_manager.set_run_attributes(span, run.id, run.status.value)

                if run.status == RunStatus.FAILED:
                    error_message = f"Run failed: {run.last_error}"
                    await event_queue.enqueue_event(new_agent_text_message(error_message))
                    self.span_manager.set_failure_result(span, error_message)
                    return
                
                # Extract response
                response_text = "No response generated."
                messages = self.agents_client.messages.list(thread_id=thread.id, order="desc")
                for msg in messages:
                    if msg.role == "assistant" and msg.text_messages:
                        response_text = msg.text_messages[0].text.value
                        break
                
                # Set final attributes and send response
                output_tokens = self._count_tokens(response_text)
                self.span_manager.set_execution_timing(span, start_time)
                self.span_manager.set_token_usage(span, input_tokens, output_tokens)
                self.span_manager.set_success_result(span, response_text)
                
                await event_queue.enqueue_event(new_agent_text_message(response_text))

                # Trigger evaluation asynchronously
                asyncio.create_task(self.evaluation_manager.submit_continuous_evaluation(thread.id, run.id, self.agent))

            except Exception as e:
                logger.error(f"An error occurred during execution: {e}", exc_info=True)
                await event_queue.enqueue_event(new_agent_text_message(f"An internal error occurred: {e}"))
                self.span_manager.set_error_result(span, e, str(e))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Handles a cancellation request."""
        await event_queue.enqueue_event(new_agent_text_message("Operation cancelled"))

async def start_server():
    """Initializes the executor and starts the Uvicorn server."""
    agent_card = AgentCard(
        name='Azure DevOps Logic App Agent',
        description='Creates Azure DevOps work items using Logic Apps and includes advanced monitoring.',
        capabilities=AgentCapabilities(streaming=False),
        url='http://localhost:8001/',
        version='1.0.0',
        defaultInputModes=['text'],
        defaultOutputModes=['text'],
        skills=[],
    )
    try:
        executor = DevOpsA2AExecutor()
        server = A2AStarletteApplication(
            agent_card=agent_card,
            http_handler=DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore()),
        )
        
        config = uvicorn.Config(server.build(), host='0.0.0.0', port=8001, log_level="info")
        server_instance = uvicorn.Server(config)
        logger.info("Starting Azure DevOps Agent server on http://localhost:8001")
        await server_instance.serve()

    except ValueError as e:
        logger.error(f"Failed to initialize agent executor due to configuration error: {e}")

if __name__ == "__main__":
    asyncio.run(start_server())