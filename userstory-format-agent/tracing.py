import os
import logging
from opentelemetry import trace

logger = logging.getLogger(__name__)

def setup_azure_monitor_tracing():
    """Configure Azure Monitor tracing if connection string is available."""
    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        logger.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set. Tracing will not be enabled.")
        return
        
    try:
        # Import and configure Azure Monitor
        from azure.monitor.opentelemetry._configure import configure_azure_monitor
        
        # Configure with minimal HTTP instrumentation to avoid loops
        configure_azure_monitor(
            connection_string=connection_string,
            disable_offline_storage=True,
        )
        
        logger.info("Azure Monitor tracing configured successfully.")
        
    except Exception as e:
        logger.error(f"Failed to configure Azure Monitor tracing: {e}")

def get_tracer(name: str):
    """Get a tracer instance for the given name."""
    return trace.get_tracer(name)
