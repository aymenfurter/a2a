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
    def __init__(self, name: str, description: str, a2a_client: A2AClient, use_last_message_only: bool = False):
        super().__init__(name=name, description=description)
        self._client = a2a_client
        self._context_id = f"chat-session-{uuid4().hex}"
        self._use_last_message_only = use_last_message_only

    @classmethod
    async def create(cls, base_url: str, name: str, description: str = None, use_last_message_only: bool = False) -> "RemoteA2AAgent":
        httpx_client = httpx.AsyncClient(timeout=30.0)
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
        agent_card = await resolver.get_agent_card()
        a2a_client = A2AClient(httpx_client=httpx_client, agent_card=agent_card)
        agent_description = description or agent_card.description or f"A2A {name} Agent"
        instance = cls(name=name, description=agent_description, a2a_client=a2a_client, use_last_message_only=use_last_message_only)
        # Store agent card for UI access
        instance._agent_card = agent_card
        return instance

    @property
    def agent_card(self):
        """Access to the agent card for UI display."""
        return getattr(self, '_agent_card', None)

    def _extract_messages(self, messages):
        if hasattr(self, '_current_channel') and hasattr(self._current_channel, 'history'):
            messages = self._current_channel.history
        
        if isinstance(messages, ChatHistory) and messages.messages:
            msgs = [messages.messages[-1]] if self._use_last_message_only else messages.messages
            return "\n\n".join([f"{getattr(msg, 'role', 'user')}{f' ({msg.name})' if hasattr(msg, 'name') and msg.name else ''}: {msg.content}" for msg in msgs])
        return str(messages) if messages else "Hello"

    async def _invoke_agent(self, messages) -> ChatMessageContent:
        prompt = self._extract_messages(messages)
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
        
        response_text = ""
        if hasattr(event, 'parts') and event.parts:
            for part in event.parts:
                if hasattr(part, 'root') and hasattr(part.root, 'text'):
                    response_text = part.root.text
                    break
                elif hasattr(part, 'text'):
                    response_text = part.text
                    break
        
        return ChatMessageContent(role="assistant", content=response_text or str(event), name=self.name)
    
    def get_channel_keys(self):
        return ["A2AChannel"]
    
    async def create_channel(self):
        return A2AChannel(self)
    
    async def _get_response_item(self, messages, **kwargs):
        from semantic_kernel.agents.agent import AgentResponseItem
        thread = kwargs.get('thread') or A2AThread()
        if not hasattr(thread, '_id'):
            await thread.create()
        response = await self._invoke_agent(messages)
        return AgentResponseItem(message=response, thread=thread)
    
    async def invoke(self, messages=None, **kwargs):
        yield await self._get_response_item(messages, **kwargs)
    
    async def invoke_stream(self, messages=None, **kwargs):
        item = await self._get_response_item(messages, **kwargs)
        streaming_response = StreamingChatMessageContent(role=item.message.role, content="", name=item.message.name)
        streaming_response.append_content(str(item.message.content))
        item.message = streaming_response
        yield item
    
    async def get_response(self, messages=None, **kwargs):
        return await self._get_response_item(messages, **kwargs)