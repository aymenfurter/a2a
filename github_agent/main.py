import os
import uvicorn
import time
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
from azure.ai.agents.models import MCPToolDefinition, RequiredMcpToolCall, SubmitToolApprovalAction, ToolApproval, MCPToolResource, ToolResources

class DevOpsA2AExecutor(AgentExecutor):
    def __init__(self):
        self.agent = None
        self.project_client = None
        self.agents_client = None
        self.threads = {}
        self.mcp_tool = None
        self._setup_azure_client()

    def _setup_azure_client(self):
        try:
            self.project_client = AIProjectClient(
                endpoint=os.environ["PROJECT_ENDPOINT"],
                credential=DefaultAzureCredential(),
            )
            self.agents_client = self.project_client.agents
            
            self.mcp_tool = MCPToolDefinition(
                server_label="github",
                server_url=os.environ.get("MCP_SERVER_URL"),
                allowed_tools=["create_issue", "list_issues", "get_issue"]
            )

            tool_info = """
Available Tools:
create_issue: {"owner": "aymenfurter", "repo": "a2a", "title": "New Issue", "body": "lorem ipsum"}
list_issues: {"owner": "aymenfurter", "repo": "a2a"}
get_issue: {"owner": "aymenfurter", "repo": "a2a", "issue_number": 1}
"""
            
            self.agent = self.agents_client.create_agent(
                model=os.environ["MODEL_DEPLOYMENT_NAME"],
                name="github-mcp-agent",
                instructions="You are a helpful GitHub assistant. Use the available MCP tools to create, read, and manage GitHub issues. Always use owner 'aymenfurter' and repo 'a2a'." + tool_info,
                tools=[self.mcp_tool]
            )
        except Exception as e:
            pass
            
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            if not self.agent:
                await event_queue.enqueue_event(new_agent_text_message("Agent not initialized"))
                return
                
            thread = self.threads.get(context.context_id)
            if not thread:
                thread = self.agents_client.threads.create()
                self.threads[context.context_id] = thread
            
            message = self.agents_client.messages.create(
                thread_id=thread.id,
                role="user",
                content=context.get_user_input(),
            )
            
            headers = {}
            github_pat = os.environ.get("GITHUB_PAT")
            if github_pat:
                headers["Authorization"] = f"Bearer {github_pat}"
            
            tool_resources = ToolResources(
                mcp=[MCPToolResource(server_label="github", headers=headers)]
            )
            
            run = self.agents_client.runs.create(
                thread_id=thread.id, 
                agent_id=self.agent.id,
                tool_resources=tool_resources
            )
            
            while run.status in ["queued", "in_progress", "requires_action"]:
                time.sleep(1)
                run = self.agents_client.runs.get(thread_id=thread.id, run_id=run.id)
                
                if run.status == "requires_action":
                    if isinstance(run.required_action, SubmitToolApprovalAction):
                        tool_calls = run.required_action.submit_tool_approval.tool_calls
                        if tool_calls:
                            tool_approvals = []
                            for tool_call in tool_calls:
                                if isinstance(tool_call, RequiredMcpToolCall):
                                    tool_approvals.append(
                                        ToolApproval(
                                            tool_call_id=tool_call.id,
                                            approve=True
                                        )
                                    )
                            
                            if tool_approvals:
                                self.agents_client.runs.submit_tool_outputs(
                                    thread_id=thread.id, 
                                    run_id=run.id,
                                    tool_approvals=tool_approvals
                                )
                                    
            if run.status == "failed":
                print (run)
                await event_queue.enqueue_event(new_agent_text_message("Operation failed"))
                return
                    
            messages = self.agents_client.messages.list(thread_id=thread.id)
            
            message_list = []
            if hasattr(messages, 'data'):
                message_list = messages.data
            elif hasattr(messages, '__iter__'):
                message_list = list(messages)
            
            for msg in message_list:
                if msg.role == "assistant" and msg.content:
                    for content_item in msg.content:
                        if hasattr(content_item, 'text') and content_item.text:
                            await event_queue.enqueue_event(new_agent_text_message(content_item.text.value))
                            return
                
        except Exception as e:
            await event_queue.enqueue_event(new_agent_text_message(f"Error: {e}"))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(new_agent_text_message("Operation cancelled."))

if __name__ == "__main__":
    agent_card = AgentCard(
        name='GitHub MCP Agent',
        description='An AI agent that manages GitHub issues using MCP tools.',
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