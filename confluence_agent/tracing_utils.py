import os
import time
import uuid
from opentelemetry import trace
from typing import Optional

class SpanManager:
    """Manages OpenTelemetry span attributes and lifecycle for agent operations."""
    
    def __init__(self, tracer):
        self.tracer = tracer
    
    def create_inference_span(self, context_id: str, user_input: str):
        """Create and configure a span for AI inference operations."""
        span = self.tracer.start_span("gen_ai_inference_call", kind=trace.SpanKind.CLIENT)
        
        # Basic attributes
        span.set_attribute("context_id", context_id)
        span.set_attribute("user_input", user_input)
        span.set_attribute("gen_ai.system", "azure_openai")
        span.set_attribute("gen_ai.operation.name", "process_thread_run")
        
        # Model attributes
        model_name = os.environ.get("MODEL_DEPLOYMENT_NAME", "unknown")
        span.set_attribute("gen_ai.request.model", model_name)
        span.set_attribute("gen_ai.response.model", model_name)
        
        # Agent attributes
        span.set_attribute("gen_ai.agent.name", "confluence-mcp-agent")
        span.set_attribute("gen_ai.agent.version", "1.0.0")
        
        return span
    
    def create_assistant_message_span(self, thread_id: str, run_id: str, message_id: str):
        """Create a span for assistant message logging."""
        span = self.tracer.start_span("gen_ai.assistant.message", kind=trace.SpanKind.INTERNAL)
        
        span.set_attribute("gen_ai.system", "azure.openai")
        span.set_attribute("gen_ai.thread.id", thread_id)
        span.set_attribute("gen_ai.thread.run.id", run_id)
        span.set_attribute("gen_ai.message.id", message_id)
        span.set_attribute("gen_ai.event.content", '{"role": "assistant"}')
        
        return span
    
    def set_token_usage(self, span, input_tokens: int, output_tokens: Optional[int] = None):
        """Set token usage attributes on the span."""
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        if output_tokens is not None:
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            span.set_attribute("gen_ai.usage.total_tokens", input_tokens + output_tokens)
    
    def set_execution_timing(self, span, start_time: float):
        """Set execution duration attribute on the span."""
        duration_ms = (time.time() - start_time) * 1000
        span.set_attribute("gen_ai.execution.duration_ms", duration_ms)
    
    def set_thread_attributes(self, span, thread_id: str, is_new_thread: bool):
        """Set thread-related attributes on the span."""
        span.set_attribute("gen_ai.thread.id", thread_id)
        span.set_attribute("gen_ai.thread.created", is_new_thread)
    
    def set_run_attributes(self, span, run_id: str, status: str):
        """Set run-related attributes on the span."""
        span.set_attribute("gen_ai.run.id", run_id)
        span.set_attribute("gen_ai.run.status", status)
    
    def set_success_result(self, span, response_text: str):
        """Set attributes for successful execution."""
        span.set_attribute("agent.response", response_text)
        span.set_status(trace.Status(trace.StatusCode.OK))
    
    def set_error_result(self, span, error: Exception, error_message: str):
        """Set attributes for failed execution."""
        span.set_attribute("gen_ai.error.type", type(error).__name__)
        span.set_attribute("gen_ai.error.message", str(error))
        span.record_exception(error)
        span.set_status(trace.Status(trace.StatusCode.ERROR, description=error_message))
    
    def set_failure_result(self, span, error_message: str):
        """Set attributes for run failure."""
        span.set_attribute("gen_ai.error.message", error_message)
        span.set_status(trace.Status(trace.StatusCode.ERROR, description=error_message))
