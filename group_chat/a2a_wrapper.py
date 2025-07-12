import httpx
from uuid import uuid4
from semantic_kernel.agents import Agent, AgentThread
from semantic_kernel.contents import ChatHistory, ChatMessageContent
from semantic_kernel.contents.streaming_chat_message_content import StreamingChatMessageContent
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import Message, MessageSendConfiguration, MessageSendParams, SendMessageRequest, TextPart

class A2AThread(AgentThread):
    def __init__(self):
        super().__init__()
    
    async def _create(self) -> str:
        self._id = str(uuid4())
        return self._id
    
    async def _delete(self) -> None:
        pass
    
    async def _on_new_message(self, new_message: ChatMessageContent) -> None:
        pass

class A2AChannel:
    def __init__(self, agent):
        self.agent = agent
        self.history = ChatHistory()
        agent._current_channel = self

    async def receive(self, history):
        self.history = ChatHistory()
        for msg in history:
            self.history.add_message(msg)

    async def invoke(self, agent, **kwargs):
        async for response in agent.invoke(messages=self.history, **kwargs):
            if hasattr(response, 'message') and response.message:
                self.history.add_message(response.message)
            yield True, response.message

    async def reset(self):
        """Reset the channel by clearing its history."""
        self.history = ChatHistory()

class RemoteA2AAgent(Agent):
    def __init__(self, name: str, description: str, a2a_client: A2AClient):
        super().__init__(name=name, description=description)
        self._client = a2a_client
        self._context_id = f"chat-session-{uuid4().hex}"

    @classmethod
    async def create(cls, base_url: str, name: str, description: str = None) -> "RemoteA2AAgent":
        httpx_client = httpx.AsyncClient(timeout=30.0)
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
        agent_card = await resolver.get_agent_card()
        a2a_client = A2AClient(httpx_client=httpx_client, agent_card=agent_card)
        agent_description = description or agent_card.description or f"A2A {name} Agent"
        return cls(name=name, description=agent_description, a2a_client=a2a_client)

    async def _invoke_agent(self, messages) -> ChatMessageContent:
        prompt = ""
        if hasattr(self, '_current_channel') and hasattr(self._current_channel, 'history'):
            channel_history = self._current_channel.history
            if channel_history.messages:
                context_messages = []
                for i, msg in enumerate(channel_history.messages):
                    role_str = str(msg.role) if hasattr(msg, 'role') else 'user'
                    name_str = f" ({msg.name})" if hasattr(msg, 'name') and msg.name else ""
                    content = str(msg.content)
                    context_messages.append(f"{role_str}{name_str}: {content}")
                prompt = "\n\n".join(context_messages)
        elif isinstance(messages, ChatHistory) and messages.messages:
            context_messages = []
            for msg in messages.messages:
                role_str = str(msg.role) if hasattr(msg, 'role') else 'user'
                name_str = f" ({msg.name})" if hasattr(msg, 'name') and msg.name else ""
                content = str(msg.content)
                context_messages.append(f"{role_str}{name_str}: {content}")
            prompt = "\n\n".join(context_messages)
        elif messages:
            prompt = str(messages)
        else:
            prompt = "Hello"
        
        request = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(
                message=Message(
                    role='user',
                    parts=[TextPart(text=prompt)],
                    messageId=str(uuid4()),
                    contextId=self._context_id,
                ),
                configuration=MessageSendConfiguration(acceptedOutputModes=['text']),
            )
        )

        response = await self._client.send_message(request)
        event = response.root.result
        
        # Extract clean text content like Azure AI agent does
        response_text = ""
        if hasattr(event, 'parts') and event.parts:
            for part in event.parts:
                if hasattr(part, 'root') and hasattr(part.root, 'text'):
                    response_text = part.root.text
                    break
                elif hasattr(part, 'text'):
                    response_text = part.text
                    break
        
        if not response_text:
            response_text = str(event)
        
        return ChatMessageContent(role="assistant", content=response_text, name=self.name)
    
    def get_channel_keys(self):
        return ["A2AChannel"]
    
    async def create_channel(self):
        return A2AChannel(self)
    
    async def invoke(self, messages=None, **kwargs):
        from semantic_kernel.agents.agent import AgentResponseItem
        thread = kwargs.get('thread') or A2AThread()
        if not hasattr(thread, '_id'):
            await thread.create()
        
        # Always use channel history as it contains the complete conversation
        actual_messages = messages
        if hasattr(self, '_current_channel') and hasattr(self._current_channel, 'history'):
            actual_messages = self._current_channel.history
        
        response = await self._invoke_agent(actual_messages)
        yield AgentResponseItem(message=response, thread=thread)
    
    async def invoke_stream(self, messages=None, **kwargs):
        from semantic_kernel.agents.agent import AgentResponseItem
        thread = kwargs.get('thread') or A2AThread()
        if not hasattr(thread, '_id'):
            await thread.create()
        response = await self._invoke_agent(messages)
        streaming_response = StreamingChatMessageContent(role=response.role, content="", name=response.name)
        streaming_response.append_content(str(response.content))
        yield AgentResponseItem(message=streaming_response, thread=thread)
    
    async def get_response(self, messages=None, **kwargs):
        from semantic_kernel.agents.agent import AgentResponseItem
        thread = kwargs.get('thread') or A2AThread()
        if not hasattr(thread, '_id'):
            await thread.create()
        response = await self._invoke_agent(messages)
        return AgentResponseItem(message=response, thread=thread)