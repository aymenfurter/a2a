import asyncio
from dotenv import load_dotenv
from semantic_kernel.agents import AgentGroupChat
from semantic_kernel.agents.strategies import TerminationStrategy
from semantic_kernel.contents import ChatMessageContent, AuthorRole, ChatHistory
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel.connectors.ai.prompt_execution_settings import PromptExecutionSettings
from a2a_agent import RemoteA2AAgent
from ui import UI

class ChatTerminationStrategy(TerminationStrategy):
    def __init__(self, agents, ui, maximum_iterations: int = 15):
        super().__init__(agents=agents, maximum_iterations=maximum_iterations)
        object.__setattr__(self, '_service', None)
        object.__setattr__(self, '_ui', ui)
        object.__setattr__(self, 'termination_prompt', (
            "Analyze a workflow that extracts todos from Confluence and creates Azure DevOps work items. "
            "Complete when work items are created or no todos exist. Respond 'true' if complete, 'false' if not."
        ))
    
    @property
    def service(self):
        if self._service is None:
            object.__setattr__(self, '_service', AzureChatCompletion())
        return self._service
    
    async def should_agent_terminate(self, agent, history):
        if len(history) < 2:
            return False
            
        last_message = history[-1].content.lower()
            
        try:
            chat_history = ChatHistory()
            chat_history.add_message(ChatMessageContent(role=AuthorRole.SYSTEM, content=self.termination_prompt))
            
            for msg in history[-3:]:
                chat_history.add_message(msg)
                
            chat_history.add_message(ChatMessageContent(role=AuthorRole.USER, content="Complete? 'true' or 'false'."))
            
            response = await self.service.get_chat_message_content(
                chat_history, settings=PromptExecutionSettings(max_tokens=10, temperature=0.1)
            )
            
            should_terminate = "true" in response.content.lower()
            self._ui.add_message("System", f"Termination check: {should_terminate} - {response.content}")
            return should_terminate
            
        except Exception as e:
            self._ui.add_message("System", f"Termination analysis error: {e}")
            return any(kw in last_message for kw in ["completed", "done", "finished"])

class Orchestrator:
    def __init__(self, ui):
        self.state = "INITIAL"
        self.ui = ui
        self.transitions = {
            "INITIAL": (["todo", "action item", "task", "found", "extracted"], "FORMAT_TODOS", "TODOS_EXTRACTED"),
            "TODOS_EXTRACTED": (["assigned to", "description", "acceptance criteria", "detailed", "expand"], "CREATE_WORK_ITEMS", "FORMATTED"),
            "FORMATTED": (["created", "success", "work items created", "installed", "completed"], None, "COMPLETED")
        }
        
    async def should_continue_workflow(self, content: str) -> tuple[bool, str]:
        content_lower = content.lower()
        
        if "no todos" in content_lower or "no action items" in content_lower:
            return False, "No work items to process"
            
        if self.state in self.transitions:
            keywords, next_action, next_state = self.transitions[self.state]
            if any(kw in content_lower for kw in keywords):
                old_state = self.state
                self.state = next_state
                self.ui.update_workflow_state(self.state)
                self.ui.add_message("System", f"State transition: {old_state} -> {next_state}")
                return next_action is not None, next_action or "Workflow completed"
        
        return False, "Workflow step completed"

async def main():
    load_dotenv()
    
    async with UI() as ui:
        try:
            ui.add_message("System", "Initializing A2A agents...")
            
            # Create agents and store their cards
            confluence_agent = await RemoteA2AAgent.create("http://localhost:8002", "ConfluenceAgent", "Reads Confluence pages and extracts todos")
            formatter_agent = await RemoteA2AAgent.create("http://localhost:8000", "FormatterAgent", "Formats requests into structured tickets", True)
            devops_agent = await RemoteA2AAgent.create("http://localhost:8001", "DevOpsAgent", "Creates Azure DevOps work items")
            
            agents = [confluence_agent, formatter_agent, devops_agent]
            
            # Add agent cards to UI using the agent_card property
            for agent in agents:
                if hasattr(agent, 'agent_card') and agent.agent_card:
                    ui.add_agent_card(agent.name, agent.agent_card)

            orchestrator = Orchestrator(ui)
            chat = AgentGroupChat(agents=agents, termination_strategy=ChatTerminationStrategy(agents, ui, 15))

            confluence_query = "Analyze https://aymenfurter.atlassian.net/wiki/spaces/~557058e4fa0cdeeab349c084c43e9310ea2ed3/pages/65706/2025-07-12+Besprechungsnotizen and extract todos/action items."
            await chat.add_chat_message(ChatMessageContent(role=AuthorRole.USER, content=confluence_query))
            ui.add_message("User", confluence_query)
            
            requests = {
                "FORMAT_TODOS": "Format extracted todos into structured work items for Azure DevOps with titles, descriptions, types, and acceptance criteria.",
                "CREATE_WORK_ITEMS": "Create the formatted work items in Azure DevOps. Provide confirmation with work item IDs if possible."
            }
            
            pending_requests = []
            
            async for content in chat.invoke():
                agent_card = None
                if hasattr(content, 'name') and content.name:
                    for agent in agents:
                        if agent.name == content.name and hasattr(agent, 'agent_card') and agent.agent_card:
                            agent_card = agent.agent_card
                            break
                
                ui.set_active_agent(content.name or content.role, agent_card)
                ui.add_message(content.name or content.role, content.content, content.name, is_agent=True)
                
                should_continue, next_step = await orchestrator.should_continue_workflow(content.content)
                if should_continue and next_step in requests:
                    pending_requests.append(ChatMessageContent(role=AuthorRole.USER, content=requests[next_step]))
                    ui.add_pending_request(next_step)
                    ui.add_message("System", f"Queued {next_step}")
                elif not should_continue:
                    ui.add_message("System", next_step)
                
                await asyncio.sleep(0.1)
            
            for request in pending_requests:
                if chat.is_complete:
                    ui.add_message("System", "Chat completed, skipping pending request")
                    break
                    
                ui.add_message("System", "Processing queued request...")
                await chat.add_chat_message(request)
                
                async for content in chat.invoke():
                    # Set active agent with card
                    agent_card = None
                    if hasattr(content, 'name') and content.name:
                        for agent in agents:
                            if agent.name == content.name and hasattr(agent, 'agent_card') and agent.agent_card:
                                agent_card = agent.agent_card
                                break
                    
                    ui.set_active_agent(content.name or content.role, agent_card)
                    ui.add_message(content.name or content.role, content.content, content.name, is_agent=True)
                    
                    should_continue, next_step = await orchestrator.should_continue_workflow(content.content)
                    if not should_continue:
                        ui.add_message("System", next_step)
                        break
                    
                    await asyncio.sleep(0.1)
            
            await chat.reset()
            ui.add_message("System", "Workflow completed successfully!")
            ui.update_workflow_state("COMPLETED")
            
            await asyncio.sleep(3)
            
        except KeyboardInterrupt:
            ui.add_message("System", "Workflow interrupted by user")
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())