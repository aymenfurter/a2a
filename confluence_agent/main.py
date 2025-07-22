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
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from oauth_auth import get_atlassian_bearer_token

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

class ConfluenceA2AExecutor(AgentExecutor):
    def __init__(self):
        self.conversations = {}
        self.atlassian_token = None
        self.client = AzureOpenAI(
            base_url=f"{os.environ['AZURE_OPENAI_ENDPOINT']}/openai/v1/",
            azure_ad_token_provider=get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"),
            api_version="preview"
        )
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
        user_input = context.get_user_input()
        input_tokens = self._count_tokens(user_input)
        
        with self.span_manager.create_inference_span(context.context_id, user_input) as span:
            self.span_manager.set_token_usage(span, input_tokens)
            
            try:
                start_time = time.time()
                
                # Generate unique IDs for tracing
                thread_id = f"thread_{context.context_id}"
                run_id = f"run_{uuid.uuid4().hex}"
                message_id = f"msg_{uuid.uuid4().hex}"
                
                conversation = self.conversations.get(context.context_id, {})
                is_new_thread = 'last_response_id' not in conversation
                
                self.span_manager.set_thread_attributes(span, thread_id, is_new_thread)
                self.span_manager.set_run_attributes(span, run_id, "in_progress")
                
                if not self.atlassian_token:
                    self.atlassian_token = await get_atlassian_bearer_token()
                
                mcp_config = {
                    "type": "mcp", "server_url": os.environ["MCP_SERVER_URL"], "server_label": os.environ["MCP_SERVER_LABEL"],
                    "require_approval": "never", "allowed_tools": ["getConfluencePage"], "headers": {"Authorization": f"Bearer {self.atlassian_token}"}
                }
                
                input_data = [{"role": "user", "content": user_input}]
                if is_new_thread:
                    input_data.insert(0, {"role": "system", "content": "You are a Confluence assistant. Use MCP tools to search and analyze content."})
                
                response = await asyncio.to_thread(
                    self.client.responses.create,
                    model=os.environ["MODEL_DEPLOYMENT_NAME"],
                    previous_response_id=conversation.get('last_response_id'),
                    input=input_data,
                    tools=[mcp_config]
                )
                
                self.conversations[context.context_id] = {'last_response_id': response.id}
                
                result_text = ""
                for output_item in response.output or []:
                    if output_item.type == "message" and output_item.content:
                        for content in output_item.content:
                            if hasattr(content, 'text'):
                                result_text = content.text
                                break
                        if result_text:
                            break
                
                if not result_text:
                    result_text = "Operation completed."
                
                # Set final attributes
                output_tokens = self._count_tokens(result_text)
                self.span_manager.set_execution_timing(span, start_time)
                self.span_manager.set_token_usage(span, input_tokens, output_tokens)
                self.span_manager.set_run_attributes(span, run_id, "completed")
                self.span_manager.set_success_result(span, result_text)
                
                await event_queue.enqueue_event(new_agent_text_message(result_text))
                
                # Create assistant message span
                with self.span_manager.create_assistant_message_span(thread_id, run_id, message_id) as msg_span:
                    logger.info(f"Assistant message logged for thread {thread_id}")
                
                # Trigger evaluation asynchronously if available
                if EVALUATION_AVAILABLE:
                    asyncio.create_task(
                        self.evaluation_manager.evaluate_conversation(
                            thread_id, run_id, user_input, result_text, input_tokens, output_tokens
                        )
                    )
                    
            except Exception as e:
                error_message = f"Error: {str(e)}"
                if "401" in str(e):
                    self.atlassian_token = None
                    error_message = "Authentication error: Please re-authenticate with Atlassian."
                
                await event_queue.enqueue_event(new_agent_text_message(error_message))
                self.span_manager.set_error_result(span, e, error_message)

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
    
    logger.info("Starting Confluence MCP Agent server on http://localhost:8002")
    asyncio.run(start_server())