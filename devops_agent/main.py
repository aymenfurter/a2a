import logging
import os
import uvicorn
import time
from dotenv import load_dotenv

# Load environment variables from .env file
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
from azure.ai.agents.models import MCPToolDefinition, RequiredMcpToolCall, SubmitToolApprovalAction, ToolApproval

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DevOpsA2AExecutor(AgentExecutor):
    def __init__(self):
        self.agent = None
        self.project_client = None
        self.agents_client = None
        self.threads = {}
        self.mcp_tool = None
        self._setup_azure_client()

    def _validate_environment(self):
        """Validate required environment variables"""
        required_vars = [
            "PROJECT_ENDPOINT",
            "MODEL_DEPLOYMENT_NAME"
        ]
        
        missing_vars = []
        for var in required_vars:
            if not os.environ.get(var):
                missing_vars.append(var)
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        logger.info("All required environment variables are present")

    def _setup_azure_client(self):
        try:
            # Validate environment first
            self._validate_environment()
            
            logger.info(f"Setting up Azure client with endpoint: {os.environ['PROJECT_ENDPOINT']}")
            
            self.project_client = AIProjectClient(
                endpoint=os.environ["PROJECT_ENDPOINT"],
                credential=DefaultAzureCredential(),
            )
            self.agents_client = self.project_client.agents
            
            # Initialize DevOps MCP tool
            mcp_server_url = os.environ.get("MCP_SERVER_URL", "http://localhost:9001/mcp")
            mcp_server_label = os.environ.get("MCP_SERVER_LABEL", "azuredevops")
            
            logger.info(f"Initializing MCP tool with server: {mcp_server_url}")
            
            # Add allowed tools for DevOps operations
            devops_tools = [
                "core_list_projects",
                "wit_my_work_items", 
                "wit_create_work_item",
                "wit_update_work_item",
                "wit_add_work_item_comment",
                "wit_list_backlog_work_items"
            ]
            
            self.mcp_tool = MCPToolDefinition(
                server_label=mcp_server_label,
                server_url=mcp_server_url,
                allowed_tools=devops_tools
            )

            # Create agent with MCP tool
            logger.info(f"Creating agent with model: {os.environ['MODEL_DEPLOYMENT_NAME']}")
            
            self.agent = self.agents_client.create_agent(
                model=os.environ["MODEL_DEPLOYMENT_NAME"],
                name="devops-mcp-agent",
                instructions="You are a helpful Azure DevOps assistant. Use the available MCP tools to create, update, and manage work items in Azure DevOps. When creating work items, ensure proper formatting with titles, descriptions, and appropriate work item types. Format responses clearly and provide actionable information about created items.",
                #tools=[self.mcp_tool],
            )
            
            logger.info(f"Successfully created DevOps agent, ID: {self.agent.id}")
            
        except ValueError as e:
            logger.error(f"Environment validation failed: {e}")
            logger.error("Please check your .env file and ensure all required variables are set")
        except Exception as e:
            logger.error(f"Failed to setup Azure client: {e}")
            logger.error(f"Error type: {type(e).__name__}")
            if hasattr(e, 'response'):
                logger.error(f"Response status: {getattr(e.response, 'status_code', 'unknown')}")
            
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            if not self.agent:
                error_msg = "DevOps agent not initialized. Please check the server logs for environment variable issues."
                logger.error(error_msg)
                await event_queue.enqueue_event(new_agent_text_message(error_msg))
                return
                
            logger.info(f"Processing request with context ID: {context.context_id}")
            logger.info(f"User input: {context.get_user_input()}")
                
            thread = self.threads.get(context.context_id)
            if not thread:
                logger.info("Creating new thread")
                thread = self.agents_client.threads.create()
                self.threads[context.context_id] = thread
                logger.info(f"Created thread ID: {thread.id}")
            else:
                logger.info(f"Using existing thread ID: {thread.id}")
            
            # Create message
            logger.info("Creating message in thread")
            message = self.agents_client.messages.create(
                thread_id=thread.id,
                role="user",
                content=context.get_user_input(),
            )
            logger.info(f"Created message ID: {message.id}")
            
            # Create and process run with MCP tools
            logger.info(f"Creating run with agent ID: {self.agent.id}")
            logger.info(f"Agent tools: {[tool.type for tool in self.agent.tools]}")
            
            run = self.agents_client.runs.create(
                thread_id=thread.id, 
                agent_id=self.agent.id
            )
            logger.info(f"Created run ID: {run.id} with status: {run.status}")
            
            # Handle run lifecycle with tool approvals
            iteration_count = 0
            while run.status in ["queued", "in_progress", "requires_action"]:
                iteration_count += 1
                logger.info(f"Run iteration {iteration_count}: status = {run.status}")
                
                time.sleep(1)
                run = self.agents_client.runs.get(thread_id=thread.id, run_id=run.id)
                logger.info(f"Updated run status: {run.status}")
                
                if run.status == "requires_action":
                    logger.info(f"Run requires action: {type(run.required_action)}")
                    
                    if isinstance(run.required_action, SubmitToolApprovalAction):
                        logger.info("Processing tool approval action")
                        tool_calls = run.required_action.submit_tool_approval.tool_calls
                        logger.info(f"Number of tool calls requiring approval: {len(tool_calls) if tool_calls else 0}")
                        
                        if tool_calls:
                            tool_approvals = []
                            for i, tool_call in enumerate(tool_calls):
                                logger.info(f"Tool call {i}: type={type(tool_call)}, id={tool_call.id}")
                                
                                if isinstance(tool_call, RequiredMcpToolCall):
                                    logger.info(f"MCP tool call - server: {tool_call.server_label}, name: {tool_call.name}")
                                    logger.info(f"MCP tool call arguments: {tool_call.arguments}")
                                    
                                    try:
                                        logger.info(f"Approving DevOps tool call: {tool_call.server_label}.{tool_call.name}")
                                        tool_approvals.append(
                                            ToolApproval(
                                                tool_call_id=tool_call.id,
                                                approve=True
                                            )
                                        )
                                    except Exception as e:
                                        logger.error(f"Error approving tool_call {tool_call.id}: {e}")
                                else:
                                    logger.warning(f"Unexpected tool call type: {type(tool_call)}")
                            
                            if tool_approvals:
                                logger.info(f"Submitting {len(tool_approvals)} tool approvals")
                                self.agents_client.runs.submit_tool_outputs(
                                    thread_id=thread.id, 
                                    run_id=run.id,
                                    tool_approvals=tool_approvals
                                )
                                logger.info("Tool approvals submitted successfully")
                            else:
                                logger.warning("No tool approvals to submit")
                        else:
                            logger.warning("No tool calls in required action")
                    else:
                        logger.info(f"Non-approval required action: {type(run.required_action)}")
                
                # Safety check to prevent infinite loops
                if iteration_count > 30:
                    logger.error("Run exceeded maximum iterations, breaking loop")
                    break
                    
            logger.info(f"Run completed with final status: {run.status}")
            
            if run.status == "failed":
                logger.error(f"Run failed with error: {run.last_error}")
                await event_queue.enqueue_event(new_agent_text_message(f"Run failed: {run.last_error.message if run.last_error else 'Unknown error'}"))
                return
                    
            # Get response messages
            logger.info("Retrieving messages from thread")
            messages = self.agents_client.messages.list(thread_id=thread.id)
            logger.info(f"Retrieved {len(messages.data) if hasattr(messages, 'data') else 'unknown'} messages")
            
            if messages:
                for i, msg in enumerate(messages):
                    logger.info(f"Message {i}: role={msg.role}, content_count={len(msg.content) if msg.content else 0}")
                    
                    if msg.role == "assistant" and msg.content:
                        # Look for text content in the assistant's message
                        for content_item in msg.content:
                            if hasattr(content_item, 'text') and content_item.text:
                                result = content_item.text.value
                                logger.info(f"Found assistant response: {result[:100]}...")
                                await event_queue.enqueue_event(new_agent_text_message(result))
                                return
                
                logger.warning("No assistant text response found in messages")
                await event_queue.enqueue_event(new_agent_text_message("No response generated from the assistant."))
            else:
                logger.warning("No messages retrieved from thread")
                await event_queue.enqueue_event(new_agent_text_message("DevOps operation completed but no response generated."))
                
        except Exception as e:
            error_msg = f"DevOps agent error: {e}"
            logger.error(error_msg, exc_info=True)
            await event_queue.enqueue_event(new_agent_text_message(error_msg))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Cancel the current execution for the given context."""
        try:
            logger.info(f"Cancelling execution for context: {context.context_id}")
            
            # If there's an active thread for this context, we could potentially cancel any running operations
            thread = self.threads.get(context.context_id)
            if thread:
                logger.info(f"Found thread {thread.id} for context {context.context_id}")
                # Note: Azure AI Agents API doesn't provide direct run cancellation in the current version
                # This is a placeholder for potential future cancellation logic
                
            await event_queue.enqueue_event(new_agent_text_message("Operation cancelled."))
            logger.info(f"Successfully cancelled execution for context: {context.context_id}")
            
        except Exception as e:
            error_msg = f"Error cancelling execution: {e}"
            logger.error(error_msg)
            await event_queue.enqueue_event(new_agent_text_message(error_msg))

if __name__ == "__main__":
    agent_card = AgentCard(
        name='Azure DevOps MCP Agent',
        description='An AI agent that manages Azure DevOps work items using MCP tools. Can create, update, and query work items, sprints, and projects through natural language commands.',
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

    logger.info("Starting Azure DevOps MCP A2A Agent server on http://localhost:8001")
    uvicorn.run(server.build(), host='0.0.0.0', port=8001)