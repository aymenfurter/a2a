import asyncio
from dotenv import load_dotenv
from azure.identity.aio import DefaultAzureCredential
from semantic_kernel.agents import AgentGroupChat, AzureAIAgent, AzureAIAgentSettings
from semantic_kernel.agents.strategies import TerminationStrategy
from semantic_kernel.contents import ChatMessageContent, AuthorRole
from a2a_wrapper import RemoteA2AAgent

class ApprovalTerminationStrategy(TerminationStrategy):
    async def should_agent_terminate(self, agent, history):
        return "approved" in history[-1].content.lower()

async def main():
    load_dotenv()
    initial_query = "Erstellen Sie eine Dokumentation über die neue Funktion namens 'Screenshot-Funktion in Word'."
    
    formatter_agent = await RemoteA2AAgent.create(
        base_url="http://localhost:8000", 
        name="FormatterAgent",
        description="A remote agent that formats user requests into structured tickets."
    )

    ai_agent_settings = AzureAIAgentSettings()

    async with (
        DefaultAzureCredential() as creds,
        AzureAIAgent.create_client(credential=creds) as client,
    ):
        foundry_agent = await client.agents.create_agent(
            model=ai_agent_settings.model_deployment_name,
            name="TranslaterAgent",
            instructions="Translate the provided sentence to english.",
        )
        
        translate_agent = AzureAIAgent(client=client, definition=foundry_agent)

        chat = AgentGroupChat(
            agents=[translate_agent, formatter_agent],
            termination_strategy=ApprovalTerminationStrategy(agents=[translate_agent], maximum_iterations=2),
        )

        initial_message = ChatMessageContent(role=AuthorRole.USER, content=initial_query)
        await chat.add_chat_message(message=initial_message)
        print(f"User: {initial_query}")
        
        async for content in chat.invoke():
            print(f"{content.name or content.role}: {content.content}")
        
        await chat.reset()
        await client.agents.delete_agent(foundry_agent.id)

if __name__ == "__main__":
    asyncio.run(main())
