from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.markdown import Markdown
from datetime import datetime
import time

class Display:
    def __init__(self):
        self.console = Console()
        self.layout = Layout()
        self.messages = []
        self.workflow_state = "INITIAL"
        self.active_agent = None
        self.active_agent_card = None
        self.start_time = time.time()
        self.pending_requests = []
        self.agent_cards = {}
        
        # Setup layout
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=4)
        )
        
        self.layout["main"].split_row(
            Layout(name="left_panel", minimum_size=40),
            Layout(name="conversation", ratio=2)
        )
        
        self.layout["left_panel"].split(
            Layout(name="workflow", size=12),
            Layout(name="agent_card", ratio=1)
        )
    
    def update_workflow_state(self, state):
        self.workflow_state = state
    
    def set_active_agent(self, agent_name, agent_card=None):
        self.active_agent = agent_name
        if agent_card:
            self.active_agent_card = agent_card
            self.agent_cards[agent_name] = agent_card
        elif agent_name in self.agent_cards:
            self.active_agent_card = self.agent_cards[agent_name]
    
    def add_agent_card(self, agent_name, agent_card):
        self.agent_cards[agent_name] = agent_card
    
    def add_message(self, role, content, agent_name=None, is_full_message=False):
        timestamp = datetime.now().strftime("%H:%M:%S")
        new_message = {
            "time": timestamp,
            "role": role,
            "content": content,
            "agent": agent_name or role,
            "is_full": is_full_message
        }
        # Insert at the beginning for most recent at top
        self.messages.insert(0, new_message)
        # Keep only last 15 messages for display
        if len(self.messages) > 15:
            self.messages = self.messages[:15]
    
    def add_pending_request(self, request_type):
        if request_type not in self.pending_requests:
            self.pending_requests.append(request_type)
    
    def remove_pending_request(self, request_type):
        if request_type in self.pending_requests:
            self.pending_requests.remove(request_type)
    
    def generate_display(self):
        # Header
        runtime = time.time() - self.start_time
        header_text = f"A2A Monitor - Runtime: {runtime:.1f}s - State: {self.workflow_state}"
        if self.active_agent:
            header_text += f" - Active: {self.active_agent}"
        
        self.layout["header"].update(Panel(header_text, style="bold blue"))
        
        # Status panel
        status_table = Table(title="Status", show_header=True, header_style="bold magenta")
        status_table.add_column("Stage", style="cyan", width=20)
        status_table.add_column("Status", style="green", width=12)
        
        stages = [
            ("INITIAL", "Extract Todos"),
            ("TODOS_EXTRACTED", "Format Items"),
            ("FORMATTED", "Create Work Items"),
            ("COMPLETED", "Completed")
        ]
        
        current_stage_index = next((i for i, (s, _) in enumerate(stages) if s == self.workflow_state), 0)
        
        for i, (stage, description) in enumerate(stages):
            if stage == self.workflow_state:
                status = "Active"
                style = "bold yellow"
            elif i < current_stage_index:
                status = "Done"
                style = "green"
            else:
                status = "Pending"
                style = "dim"
            
            status_table.add_row(description, status, style=style)
        
        # Add pending requests to status panel
        status_content = [status_table]
        if self.pending_requests:
            pending_text = "\n".join([f"{req.replace('_', ' ')}" for req in self.pending_requests])
            status_content.append(Text(f"Queued:\n{pending_text}", style="yellow"))
        
        self.layout["workflow"].update(Panel(Columns(status_content), title="Progress"))
        
        # Agent card panel
        if self.active_agent_card:
            agent_info = []
            agent_info.append(f"**Name:** {self.active_agent}")
            
            if hasattr(self.active_agent_card, 'description') and self.active_agent_card.description:
                agent_info.append(f"**Description:** {self.active_agent_card.description}")
            else:
                agent_info.append("**Description:** No description available")
            
            if hasattr(self.active_agent_card, 'capabilities') and self.active_agent_card.capabilities:
                # Handle capabilities that might be tuples or other objects
                try:
                    capabilities_list = []
                    for cap in self.active_agent_card.capabilities:
                        if isinstance(cap, tuple):
                            capabilities_list.append(str(cap[0]) if cap else str(cap))
                        else:
                            capabilities_list.append(str(cap))
                    capabilities = ", ".join(capabilities_list)
                    agent_info.append(f"**Capabilities:** {capabilities}")
                except Exception as e:
                    agent_info.append(f"**Capabilities:** {str(self.active_agent_card.capabilities)}")
            
            if hasattr(self.active_agent_card, 'version') and self.active_agent_card.version:
                agent_info.append(f"**Version:** {self.active_agent_card.version}")
                
            if hasattr(self.active_agent_card, 'provider') and self.active_agent_card.provider:
                agent_info.append(f"**Provider:** {self.active_agent_card.provider}")
            
            agent_markdown = Markdown("\n".join(agent_info))
            self.layout["agent_card"].update(Panel(agent_markdown, title=f"{self.active_agent} Info"))
        elif self.active_agent:
            # Show basic info even without card
            agent_info = [f"**Name:** {self.active_agent}", "**Status:** Active"]
            agent_markdown = Markdown("\n".join(agent_info))
            self.layout["agent_card"].update(Panel(agent_markdown, title=f"{self.active_agent} Info"))
        else:
            self.layout["agent_card"].update(Panel("No active agent", title="Agent Info", style="dim"))
        
        # Conversation panel
        conv_table = Table(show_header=True, header_style="bold cyan", title="Conversation Flow (Most Recent First)")
        conv_table.add_column("Time", width=8)
        conv_table.add_column("Agent", width=15)
        conv_table.add_column("Message", ratio=1)
        
        for msg in self.messages:
            # Show full message for agents, truncated for others
            if msg["agent"] in ["ConfluenceAgent", "FormatterAgent", "DevOpsAgent"] or msg["is_full"]:
                content_display = msg["content"]
            else:
                content_display = msg["content"][:100] + "..." if len(msg["content"]) > 100 else msg["content"]
            
            agent_style = {
                "ConfluenceAgent": "blue",
                "FormatterAgent": "green", 
                "DevOpsAgent": "red",
                "System": "yellow",
                "User": "cyan"
            }.get(msg["agent"], "white")
            
            conv_table.add_row(
                msg["time"],
                msg["agent"],
                content_display,
                style=agent_style
            )
        
        self.layout["conversation"].update(Panel(conv_table, title="Messages"))
        
        # Footer with controls
        footer_text = "Press Ctrl+C to stop | Live updates enabled | Agent responses shown in full"
        self.layout["footer"].update(Panel(footer_text, style="dim"))
        
        return self.layout

class UI:
    def __init__(self):
        self.display = Display()
        self.live = None
    
    async def __aenter__(self):
        self.live = Live(self.display.generate_display(), refresh_per_second=2, screen=True)
        self.live.__enter__()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.live:
            self.live.__exit__(exc_type, exc_val, exc_tb)
    
    def update(self):
        if self.live:
            self.live.update(self.display.generate_display())
    
    def add_message(self, role, content, agent_name=None, is_agent=False):
        self.display.add_message(role, content, agent_name, is_full_message=is_agent)
        self.update()
    
    def set_active_agent(self, agent_name, agent_card=None):
        self.display.set_active_agent(agent_name, agent_card)
        self.update()
    
    def add_agent_card(self, agent_name, agent_card):
        self.display.add_agent_card(agent_name, agent_card)
    
    def update_workflow_state(self, state):
        self.display.update_workflow_state(state)
        self.update()
    
    def add_pending_request(self, request_type):
        self.display.add_pending_request(request_type)
        self.update()
    
    def remove_pending_request(self, request_type):
        self.display.remove_pending_request(request_type)
        self.update()