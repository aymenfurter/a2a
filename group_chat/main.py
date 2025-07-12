import sys
print("Starting script", file=sys.stderr)
import os
import asyncio
from collections.abc import AsyncIterable
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from azure.identity.aio import DefaultAzureCredential
from semantic_kernel.agents import (
    Agent,
    ChatCompletionAgent,
)
from semantic_kernel.contents import ChatMessageContent, ChatHistory
from semantic_kernel.contents.streaming_chat_message_content import StreamingChatMessageContent
from semantic_kernel.functions.kernel_function_decorator import kernel_function
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion

from semantic_kernel.agents import AgentGroupChat, AzureAIAgent, AzureAIAgentSettings
from semantic_kernel.agents.strategies import TerminationStrategy
from semantic_kernel.contents import AuthorRole
from azure.identity.aio import DefaultAzureCredential


from a2a_wrapper import RemoteA2AAgent

console = Console()

class ApprovalTerminationStrategy(TerminationStrategy):
    """A strategy for determining when an agent should terminate."""

    async def should_agent_terminate(self, agent, history):
        """Check if the agent should terminate."""
        return "approved" in history[-1].content.lower()

async def main():
    load_dotenv()
    initial_query = "Erstellen Sie eine Dokumentation über die neue Funktion namens 'Screenshot-Funktion in Word'."
    # 1. Create the remote A2A agent wrapper
    console.print(Panel.fit("[bold cyan]Connecting to remote A2A Formatter Agent...[/bold cyan]"))
    try:
        formatter_agent = await RemoteA2AAgent.create(
            base_url="http://localhost:8000", 
            name="FormatterAgent",
            description="A remote agent that formats user requests into structured tickets."
        )
        console.print("[green]Successfully connected to A2A agent.[/green]")

        ai_agent_settings = AzureAIAgentSettings()

        async with (
            DefaultAzureCredential() as creds,
            AzureAIAgent.create_client(credential=creds) as client,
        ):
           
            foundry_agent = await client.agents.create_agent(
                model=ai_agent_settings.model_deployment_name,
                name="TranslaterAgent",
                instructions="Translate to french.",
            )
            
            translate_agent = AzureAIAgent(
                client=client,
                definition=foundry_agent,
            )

            chat = AgentGroupChat(
                agents=[translate_agent, formatter_agent],
                termination_strategy=ApprovalTerminationStrategy(agents=[translate_agent], maximum_iterations=10),
            )

            try:
                # Add the initial message with proper role
                initial_message = ChatMessageContent(
                    role=AuthorRole.USER,
                    content=initial_query
                )
                await chat.add_chat_message(message=initial_message)
                console.print(f"[bold green]👤 User[/bold green]: [white]{initial_query}[/white]")
                
                async for content in chat.invoke():
                    if content.name:
                        console.print(f"[bold blue]🤖 {content.name}[/bold blue]: [white]{content.content}[/white]")
                    else:
                        console.print(f"[bold yellow]👤 {content.role}[/bold yellow]: [white]{content.content}[/white]")
            finally:
                # 8. Cleanup: Delete the agents
                await chat.reset()
                await client.agents.delete_agent(foundry_agent.id)


    except Exception as e:
        console.print(f"[red]Failed to connect to A2A agent on http://localhost:8000.[/red]")
        console.print(f"[yellow]Please ensure 'a2a_formatter_agent.py' is running in another terminal.[/yellow]")
        console.print(f"Error: {e}")
        return

    console.print(Panel.fit("[bold cyan]Creating local Ticket Populator Agent...[/bold cyan]"))
   

if __name__ == "__main__":
    asyncio.run(main())
