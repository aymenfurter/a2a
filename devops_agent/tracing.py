import os
import logging
from opentelemetry import trace
from azure.monitor.opentelemetry._configure import configure_azure_monitor

logger = logging.getLogger(__name__)

def setup_azure_monitor_tracing():
    """Configure Azure Monitor tracing if connection string is available."""
    if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        try:
            configure_azure_monitor()
            logger.info("Azure Monitor tracing configured successfully.")
        except Exception as e:
            logger.error(f"Failed to configure Azure Monitor tracing: {e}")
    else:
        logger.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set. Tracing will not be enabled.")

def get_tracer(name: str):
    """Get a tracer instance for the given name."""
    return trace.get_tracer(name)
