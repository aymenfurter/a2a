import asyncio
from dotenv import load_dotenv
from semantic_kernel.agents import AgentGroupChat
from semantic_kernel.agents.strategies import TerminationStrategy
from semantic_kernel.contents import ChatMessageContent, AuthorRole, ChatHistory
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel.connectors.ai.prompt_execution_settings import PromptExecutionSettings
from semantic_kernel.prompt_template import KernelPromptTemplate, PromptTemplateConfig
from semantic_kernel.functions import KernelArguments
from semantic_kernel.kernel import Kernel
from a2a_agent import RemoteA2AAgent

class WorkflowTerminationStrategy(TerminationStrategy):
    """A chat completion-based termination strategy for the A2A workflow."""
    
    def __init__(self, agents, maximum_iterations: int = 15):
        super().__init__(agents=agents, maximum_iterations=maximum_iterations)
        # Use object.__setattr__ to bypass Pydantic validation for custom attributes
        object.__setattr__(self, '_service', None)
        object.__setattr__(self, 'topic', "Confluence analysis and Azure DevOps work item creation workflow")
        object.__setattr__(self, 'termination_prompt', (
            "You are analyzing a workflow that extracts todos from Confluence and creates Azure DevOps work items. "
            "The workflow involves: 1) Confluence agent extracting todos, 2) Formatter agent structuring them, 3) DevOps agent creating work items. "
            "Determine if this workflow has been completed successfully. "
            "The workflow is complete when work items have been created in Azure DevOps or when there are no todos to process. "
            "If the workflow is complete, respond with 'true'. If more steps are needed, respond with 'false'."
        ))
    
    @property
    def service(self):
        """Lazy initialization of the Azure Chat Completion service."""
        if self._service is None:
            object.__setattr__(self, '_service', AzureChatCompletion())
        return self._service
    
    async def _render_prompt(self, prompt: str, arguments: KernelArguments) -> str:
        """Helper to render a prompt with arguments."""
        prompt_template_config = PromptTemplateConfig(template=prompt)
        prompt_template = KernelPromptTemplate(prompt_template_config=prompt_template_config)
        return await prompt_template.render(Kernel(), arguments=arguments)
    
    async def should_agent_terminate(self, agent, history):
        """Determine if the workflow should terminate based on conversation analysis."""
        if len(history) < 2:
            return False
            
        # Check for basic completion keywords first
        last_message = history[-1].content.lower()
        if any(keyword in last_message for keyword in ["completed", "done", "finished", "created successfully", "tickets created", "no todos found", "no action items"]):
            return True
            
        # Use AI to analyze the conversation for workflow completion
        try:
            chat_history = ChatHistory()
            chat_history.add_message(
                ChatMessageContent(
                    role=AuthorRole.SYSTEM,
                    content=await self._render_prompt(
                        self.termination_prompt,
                        KernelArguments(topic=self.topic),
                    ),
                ),
            )
            
            # Add recent conversation context
            for msg in history[-5:]:  # Last 5 messages for context
                chat_history.add_message(msg)
                
            chat_history.add_message(
                ChatMessageContent(
                    role=AuthorRole.USER, 
                    content="Has the workflow been completed? Respond with 'true' or 'false'."
                ),
            )
            
            response = await self.service.get_chat_message_content(
                chat_history,
                settings=PromptExecutionSettings(max_tokens=10, temperature=0.1),
            )
            
            should_terminate = "true" in response.content.lower()
            
            print("*********************")
            print(f"Should terminate: {should_terminate}")
            print(f"AI Analysis: {response.content}")
            print("*********************")
            
            return should_terminate
            
        except Exception as e:
            print(f"Error in termination analysis: {e}")
            # Fallback to simple keyword detection
            return any(keyword in last_message for keyword in ["completed", "done", "finished", "created successfully"])

class WorkflowOrchestrator:
    """Orchestrates the workflow between Confluence, Formatter, and DevOps agents."""
    
    def __init__(self, confluence_agent, formatter_agent, devops_agent):
        self.confluence_agent = confluence_agent
        self.formatter_agent = formatter_agent  
        self.devops_agent = devops_agent
        self.workflow_state = "INITIAL"  # INITIAL -> TODOS_EXTRACTED -> FORMATTED -> COMPLETED
        
    async def should_continue_workflow(self, content: str) -> tuple[bool, str]:
        """Determine if workflow should continue and what the next step should be."""
        content_lower = content.lower()

        # print out current workflow state and content
        print(f"Current workflow state: {self.workflow_state}")
        print(f"Content: {content_lower}")
        
        if self.workflow_state == "INITIAL":
            if any(phrase in content_lower for phrase in ["todo", "action item", "task", "found", "extracted"]):
                if "no todos" in content_lower or "no action items" in content_lower:
                    return False, "No work items to process"
                self.workflow_state = "TODOS_EXTRACTED"
                return True, "FORMAT_TODOS"
            elif "no todos" in content_lower or "no action items" in content_lower:
                return False, "No work items found to process"
                
        elif self.workflow_state == "TODOS_EXTRACTED":
            if any(phrase in content_lower for phrase in ["formatted", "structured", "work item"]):
                self.workflow_state = "FORMATTED"
                return True, "CREATE_WORK_ITEMS"
                
        elif self.workflow_state == "FORMATTED":
            if any(phrase in content_lower for phrase in ["created", "success", "work items created"]):
                self.workflow_state = "COMPLETED"
                return False, "Workflow completed successfully"
        
        return False, "Workflow step completed"

async def main():
    load_dotenv()
    
    confluence_query = "Please analyze the Confluence page: https://aymenfurter.atlassian.net/wiki/spaces/~557058e4fa0cdeeab349c084c43e9310ea2ed3/pages/65706/2025-07-12+Besprechungsnotizen and extract any open todos or action items as a list."
    
    confluence_agent = await RemoteA2AAgent.create(
        base_url="http://localhost:8002", 
        name="ConfluenceAgent",
        description="Agent that reads Confluence pages and extracts todos and action items"
    )
    
    formatter_agent = await RemoteA2AAgent.create(
        base_url="http://localhost:8000", 
        name="FormatterAgent",
        description="Agent that formats user requests into structured tickets"
    )
    
    devops_agent = await RemoteA2AAgent.create(
        base_url="http://localhost:8001", 
        name="DevOpsAgent", 
        description="Agent that creates and manages Azure DevOps work items"
    )

    # Create workflow orchestrator
    orchestrator = WorkflowOrchestrator(confluence_agent, formatter_agent, devops_agent)

    # Create group chat with all A2A agents and improved termination strategy
    chat = AgentGroupChat(
        agents=[confluence_agent, formatter_agent, devops_agent],
        termination_strategy=WorkflowTerminationStrategy(
            agents=[confluence_agent, formatter_agent, devops_agent], 
            maximum_iterations=15
        ),
    )

    # Start the workflow
    initial_message = ChatMessageContent(role=AuthorRole.USER, content=confluence_query)
    await chat.add_chat_message(message=initial_message)
    print(f"User: {confluence_query}")
    print("=" * 80)
    
    # Process the conversation with workflow orchestration
    pending_requests = []
    
    async for content in chat.invoke():
        print(f"**{content.name or content.role}**")
        print(f"{content.content}")
        print("-" * 40)
        
        should_continue, next_step = await orchestrator.should_continue_workflow(content.content)
        
        if should_continue:
            if next_step == "FORMAT_TODOS":
                pending_requests.append(ChatMessageContent(
                    role=AuthorRole.USER, 
                    content="Please format the extracted todos into structured work items suitable for Azure DevOps creation. Include titles, descriptions, work item types (Task, User Story, Bug), and any acceptance criteria."
                ))
                print(f"**System: Queued request for formatting extracted todos...**")
                print("-" * 40)
                
            elif next_step == "CREATE_WORK_ITEMS":
                pending_requests.append(ChatMessageContent(
                    role=AuthorRole.USER,
                    content="Please create the formatted work items in Azure DevOps. Provide confirmation of successful creation with work item IDs if possible."
                ))
                print(f"**System: Queued request for creating work items in Azure DevOps...**")
                print("-" * 40)
        else:
            print(f"**System: {next_step}**")
            print("-" * 40)
    
    # Process any pending requests after the current conversation round is complete
    for request in pending_requests:
        print(f"**System: Processing queued request...**")
        print("-" * 40)
        await chat.add_chat_message(message=request)
        
        # Process responses to the queued requests
        async for content in chat.invoke():
            print(f"**{content.name or content.role}**")
            print(f"{content.content}")
            print("-" * 40)
            
            # Check if this completes a workflow step
            should_continue, next_step = await orchestrator.should_continue_workflow(content.content)
            if not should_continue:
                print(f"**System: {next_step}**")
                print("-" * 40)
                break
    
    await chat.reset()
    print("=" * 80)
    print("**Workflow completed successfully!**")

if __name__ == "__main__":
    asyncio.run(main())