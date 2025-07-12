import httpx
from uuid import uuid4
from collections.abc import AsyncIterable
from semantic_kernel.agents import Agent, AgentThread
from semantic_kernel.contents import ChatHistory, ChatMessageContent
from semantic_kernel.contents.streaming_chat_message_content import StreamingChatMessageContent
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    Message, MessageSendConfiguration, MessageSendParams,
    SendMessageRequest, Task, TextPart,
)

class A2AThread(AgentThread):
    """A minimal thread implementation for A2A agents following the AgentThread pattern."""
    
    def __init__(self):
        super().__init__()
    
    async def _create(self) -> str:
        """Starts the thread and returns its ID."""
        self._id = str(uuid4())
        return self._id
    
    async def _delete(self) -> None:
        """Ends the current thread."""
        pass
    
    async def _on_new_message(self, new_message: ChatMessageContent) -> None:
        """Called when a new message has been contributed to the chat."""
        pass

class A2AChannel:
    """A minimal channel implementation for A2A agents."""
    
    def __init__(self, agent):
        self.agent = agent
        self.history = ChatHistory()
        # Store reference to this channel on the agent for debugging
        agent._current_channel = self

    async def reset(self):
        """Reset the channel."""
        self.history = ChatHistory()

    async def receive(self, history):
        """Receive messages from the channel - this is called by AgentChat to sync history."""
        print(f"DEBUG: A2AChannel.receive called with {len(history)} messages")
        self.history = ChatHistory()
        for msg in history:
            print(f"DEBUG: Adding message to channel: {msg.role} - {str(msg.content)[:100]}...")
            self.history.add_message(msg)
        print(f"DEBUG: Channel now has {len(self.history.messages)} messages")

    async def send(self, *args, **kwargs):
        """Send messages to the channel."""
        for arg in args:
            if isinstance(arg, ChatMessageContent):
                self.history.add_message(arg)
            elif isinstance(arg, str):
                self.history.add_message(ChatMessageContent(role="assistant", content=arg))

    async def invoke(self, agent, **kwargs):
        """Invoke the channel with the agent - this is called by AgentChat.invoke_agent()."""
        print(f"DEBUG: A2AChannel.invoke called with {len(self.history.messages)} messages in history")
        
        # Pass the channel's history (which should contain the full chat history) to the agent
        async for response in agent.invoke(messages=self.history, **kwargs):
            # Add the response to history
            if hasattr(response, 'message') and response.message:
                self.history.add_message(response.message)
                print(f"DEBUG: Added response to channel history: {response.message.content[:100]}...")
            yield True, response.message  # (is_visible, message) tuple

    async def invoke_stream(self, agent, messages, **kwargs):
        """Invoke the channel as a stream."""
        print(f"DEBUG: A2AChannel.invoke_stream called")
        
        # Pass the channel's history to the agent
        async for response in agent.invoke_stream(messages=self.history, **kwargs):
            # The messages parameter here is for collecting output messages
            if hasattr(response, 'message') and response.message:
                self.history.add_message(response.message)
                if messages is not None:  # messages is the output collection
                    messages.append(response.message)
            yield response.message

    async def get_history(self):
        """Retrieve the message history specific to this channel."""
        for message in reversed(self.history.messages):
            yield message

class RemoteA2AAgent(Agent):
    """
    A Semantic Kernel Agent that wraps a remote A2A (Agent-to-Agent) client.
    """
    
    def __init__(self, name: str, description: str, a2a_client: A2AClient):
        super().__init__(name=name, description=description)
        self._client = a2a_client
        self._context_id = f"chat-session-{uuid4().hex}"

    @classmethod
    async def create(cls, base_url: str, name: str, description: str = None) -> "RemoteA2AAgent":
        """
        Factory method to create a RemoteA2AAgent by discovering the agent card.
        """
        httpx_client = httpx.AsyncClient(timeout=30.0)
        
        try:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
            agent_card = await resolver.get_agent_card()
            a2a_client = A2AClient(httpx_client=httpx_client, agent_card=agent_card)
            agent_description = description or agent_card.description or f"A2A {name} Agent"
            return cls(name=name, description=agent_description, a2a_client=a2a_client)
        except Exception as e:
            await httpx_client.aclose()
            raise e

    async def _invoke_agent(self, messages) -> ChatMessageContent:
        """
        Invokes the remote A2A agent and returns its response.
        """
        print(f"DEBUG: _invoke_agent called with messages: {messages}")
        print(f"DEBUG: type of messages: {type(messages)}")
        
        # Handle different message input types
        prompt = ""
        context_messages = []
        
        if messages is None:
            print("DEBUG: messages is None - this suggests AgentGroupChat is not passing chat history properly")
            prompt = "Hello, I'm ready to help. What would you like me to do?"
        elif isinstance(messages, str):
            print(f"DEBUG: messages is string: '{messages}'")
            prompt = messages
        elif isinstance(messages, ChatMessageContent):
            print(f"DEBUG: messages is ChatMessageContent: '{messages.content}'")
            prompt = str(messages.content)
        elif isinstance(messages, ChatHistory):
            print(f"DEBUG: messages is ChatHistory with {len(messages.messages)} messages")
            if messages.messages:
                # Build context from all messages
                for i, msg in enumerate(messages.messages):
                    role_str = str(msg.role) if hasattr(msg, 'role') else 'user'
                    name_str = f" ({msg.name})" if hasattr(msg, 'name') and msg.name else ""
                    content_preview = str(msg.content)[:100] + "..." if len(str(msg.content)) > 100 else str(msg.content)
                    print(f"DEBUG: Message {i}: {role_str}{name_str}: {content_preview}")
                    context_messages.append(f"{role_str}{name_str}: {msg.content}")
                
                # Use the full conversation context as prompt
                if context_messages:
                    prompt = "\n".join(context_messages)
                    print(f"DEBUG: Built context from {len(context_messages)} messages")
                    print(f"DEBUG: Context preview: {prompt[:200]}...")
                else:
                    print("DEBUG: No content in ChatHistory messages")
                    prompt = "Hello, I'm ready to help. What would you like me to do?"
            else:
                print("DEBUG: ChatHistory is empty - AgentGroupChat should have chat history by now")
                prompt = "Hello, I'm ready to help. What would you like me to do?"
        elif isinstance(messages, list):
            print(f"DEBUG: messages is list with {len(messages)} items")
            if messages:
                # Build context from all messages in list
                for i, msg in enumerate(messages):
                    if isinstance(msg, ChatMessageContent):
                        role_str = str(msg.role) if hasattr(msg, 'role') else 'user'
                        name_str = f" ({msg.name})" if hasattr(msg, 'name') and msg.name else ""
                        content_preview = str(msg.content)[:100] + "..." if len(str(msg.content)) > 100 else str(msg.content)
                        print(f"DEBUG: List message {i}: {role_str}{name_str}: {content_preview}")
                        context_messages.append(f"{role_str}{name_str}: {msg.content}")
                    elif isinstance(msg, str):
                        print(f"DEBUG: List message {i}: user: {msg[:100]}...")
                        context_messages.append(f"user: {msg}")
                    else:
                        print(f"DEBUG: List message {i}: user: {str(msg)[:100]}...")
                        context_messages.append(f"user: {str(msg)}")
                
                if context_messages:
                    prompt = "\n".join(context_messages)
                    print(f"DEBUG: Built context from {len(context_messages)} list messages")
                    print(f"DEBUG: Context preview: {prompt[:200]}...")
                else:
                    print("DEBUG: No usable content in messages list")
                    prompt = "Hello, I'm ready to help. What would you like me to do?"
            else:
                print("DEBUG: messages list is empty")
                prompt = "Hello, I'm ready to help. What would you like me to do?"
        else:
            print(f"DEBUG: converting messages to string: '{str(messages)}'")
            prompt = str(messages)

        print(f"DEBUG: Final prompt length: {len(prompt)}")
        print(f"DEBUG: Final prompt preview: {prompt[:300]}...")
        
        # If prompt is empty, provide a default context
        if not prompt.strip():
            prompt = "Please introduce yourself and explain your capabilities."
            print(f"DEBUG: Using default prompt: '{prompt}'")
        
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
        
        # Extract the response content similar to client.py
        event = response.root.result
        response_text = ""
        
        if isinstance(event, Task):
            response_text = f"✓ Task created! ID: {event.id}"
        elif isinstance(event, Message) and event.parts:
            for part in event.parts:
                text = getattr(part, 'text', None) or getattr(getattr(part, 'root', None), 'text', None) or str(part)
                if text and not text.startswith('<'):
                    response_text = text
                    break
        else:
            # Fallback to the original approach if the structure is different
            try:
                result = response.model_dump(mode='json', exclude_none=True)
                response_text = result["result"]["parts"][0]["text"]
            except (KeyError, IndexError, TypeError):
                response_text = str(event) if event else "No response received"
        
        return ChatMessageContent(role="assistant", content=response_text, name=self.name)
    
    def get_channel_keys(self):
        """Override to provide a channel key for the agent."""
        return ["A2AChannel"]
    
    async def create_channel(self):
        """Override to provide a minimal channel implementation."""
        return A2AChannel(self)
    
    async def get_response(self, messages=None, **kwargs):
        """Required implementation of abstract method from Agent."""
        from semantic_kernel.agents.agent import AgentResponseItem
        
        print(f"DEBUG: get_response called with messages: {messages}")
        print(f"DEBUG: get_response kwargs: {kwargs}")
        
        # Get thread from kwargs if available
        thread = kwargs.get('thread')
        if thread is None:
            thread = A2AThread()
            await thread.create()
        
        # Try to get the actual group chat history from the thread or kwargs
        actual_messages = await self._extract_group_chat_messages(messages, thread, kwargs)
        
        response = await self._invoke_agent(actual_messages)
        return AgentResponseItem(message=response, thread=thread)
    
    async def invoke(self, messages=None, **kwargs):
        """Required implementation of abstract method from Agent."""
        from semantic_kernel.agents.agent import AgentResponseItem
        
        print(f"DEBUG: invoke called with messages: {messages}")
        print(f"DEBUG: invoke kwargs: {kwargs}")
        
        # Get thread from kwargs if available
        thread = kwargs.get('thread')
        if thread is None:
            thread = A2AThread()
            await thread.create()
        
        # Try to get the actual group chat history from the thread or kwargs
        actual_messages = await self._extract_group_chat_messages(messages, thread, kwargs)
        
        response = await self._invoke_agent(actual_messages)
        yield AgentResponseItem(message=response, thread=thread)
    
    async def invoke_stream(self, messages=None, **kwargs):
        """
        Required implementation of abstract method from Agent.
        This implementation doesn't actually stream - it just returns the full response at once.
        """
        from semantic_kernel.agents.agent import AgentResponseItem
        
        print(f"DEBUG: invoke_stream called with messages: {messages}")
        print(f"DEBUG: invoke_stream kwargs: {kwargs}")
        
        # Get thread from kwargs if available
        thread = kwargs.get('thread')
        if thread is None:
            thread = A2AThread()
            await thread.create()
        
        # Try to get the actual group chat history from the thread or kwargs
        actual_messages = await self._extract_group_chat_messages(messages, thread, kwargs)
        
        response = await self._invoke_agent(actual_messages)
        streaming_response = StreamingChatMessageContent(
            role=response.role,
            content="",
            name=response.name,
        )
        streaming_response.append_content(str(response.content))
        yield AgentResponseItem(message=streaming_response, thread=thread)

    async def _extract_group_chat_messages(self, messages, thread, kwargs):
        """Extract the actual group chat messages from various sources."""
        print(f"DEBUG: _extract_group_chat_messages - messages: {messages}")
        print(f"DEBUG: _extract_group_chat_messages - thread: {thread}")
        print(f"DEBUG: _extract_group_chat_messages - kwargs keys: {list(kwargs.keys())}")
        
        # The AgentChat should have passed the full history via channel.receive()
        # So our channel should have the complete history
        
        # 1. First check if we have a channel with history
        if hasattr(self, '_current_channel') and hasattr(self._current_channel, 'history'):
            channel_history = self._current_channel.history
            print(f"DEBUG: Found channel history with {len(channel_history.messages)} messages")
            if channel_history.messages:
                return channel_history
        
        # 2. Check if there's a chat history in kwargs (backup)
        if 'chat_history' in kwargs:
            chat_history = kwargs['chat_history']
            print(f"DEBUG: Found chat_history in kwargs: {chat_history}")
            if hasattr(chat_history, 'messages') and chat_history.messages:
                print(f"DEBUG: chat_history has {len(chat_history.messages)} messages")
                return chat_history
        
        # 3. Check the messages parameter directly
        if messages is not None and isinstance(messages, ChatHistory) and messages.messages:
            print(f"DEBUG: Using messages parameter with {len(messages.messages)} messages")
            return messages
        
        # 4. Check if the thread has messages (unlikely but possible)
        if hasattr(thread, 'get_messages'):
            try:
                thread_messages = []
                async for msg in thread.get_messages():
                    thread_messages.append(msg)
                print(f"DEBUG: Got {len(thread_messages)} messages from thread.get_messages()")
                if thread_messages:
                    return thread_messages
            except Exception as e:
                print(f"DEBUG: Error getting messages from thread: {e}")
        
        print(f"DEBUG: No chat history found anywhere - this is unexpected")
        return messages

    async def invoke(self, messages=None, **kwargs):
        """Required implementation of abstract method from Agent."""
        from semantic_kernel.agents.agent import AgentResponseItem
        
        print(f"DEBUG: invoke called with messages: {messages}")
        print(f"DEBUG: invoke kwargs: {kwargs}")
        
        # The key insight: AgentChat (parent of AgentGroupChat) should pass the chat history
        # Let's look for it in the right place
        
        # Get thread from kwargs if available
        thread = kwargs.get('thread')
        if thread is None:
            thread = A2AThread()
            await thread.create()
        
        # IMPORTANT: AgentChat.invoke_agent() passes the chat history in a specific way
        # Let's check if the messages parameter contains the actual chat history
        actual_messages = messages
        
        # If messages is empty but we have kwargs with chat context, use that
        if (messages is None or 
            (isinstance(messages, ChatHistory) and len(messages.messages) == 0)):
            
            # Try to extract from different sources
            extracted = await self._extract_group_chat_messages(messages, thread, kwargs)
            if extracted is not None:
                actual_messages = extracted
        
        print(f"DEBUG: Final actual_messages for invoke: {type(actual_messages)} - {actual_messages}")
        
        response = await self._invoke_agent(actual_messages)
        yield AgentResponseItem(message=response, thread=thread)

    async def cleanup(self):
        """
        Clean up the HTTP client when done.
        """
        if hasattr(self._client, '_httpx_client'):
            await self._client._httpx_client.aclose()