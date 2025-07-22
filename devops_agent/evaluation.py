import os
import logging
from azure.ai.projects.models import (
    AgentEvaluationRequest,
    AgentEvaluationSamplingConfiguration,
    AgentEvaluationRedactionConfiguration,
    EvaluatorIds
)
from opentelemetry import trace

logger = logging.getLogger(__name__)

class EvaluationManager:
    """Handles service-side evaluation requests for agent runs."""
    
    def __init__(self, project_client, tracer):
        self.project_client = project_client
        self.tracer = tracer

    async def submit_continuous_evaluation(self, thread_id: str, run_id: str, agent):
        """
        Submits a service-side evaluation request for a completed thread run.
        This uses AgentEvaluationRequest to have the Azure AI service perform the evaluation.
        """
        with self.tracer.start_as_current_span("submit_service_side_evaluation") as span:
            span.set_attribute("thread_id", thread_id)
            span.set_attribute("run_id", run_id)
            # Add gen_ai attributes for evaluation tracking
            span.set_attribute("gen_ai.system", "azure_ai_projects")
            span.set_attribute("gen_ai.operation.name", "evaluation")
            
            if not agent:
                logger.error("Cannot submit evaluation, agent is not initialized.")
                return

            try:
                logger.info(f"Submitting service-side evaluation request for thread {thread_id}, run {run_id}")

                # Get Application Insights connection string
                app_insights_connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
                if not app_insights_connection_string:
                    logger.error("APPLICATIONINSIGHTS_CONNECTION_STRING not found in environment variables")
                    return

                _evaluation_request = AgentEvaluationRequest(
                    run_id=run_id,
                    thread_id=thread_id,
                    evaluators={
                        "Relevance": {"Id": EvaluatorIds.RELEVANCE.value},
                        "TaskAdherence": {"Id": EvaluatorIds.TASK_ADHERENCE.value},
                        "ToolCallAccuracy": {"Id": EvaluatorIds.TOOL_CALL_ACCURACY.value},
                        "HateUnfairness": {"Id": EvaluatorIds.HATE_UNFAIRNESS.value},
                        "Coherence": {"Id": EvaluatorIds.COHERENCE.value},
                        "Fluency": {"Id": EvaluatorIds.FLUENCY.value}
                    },
                    sampling_configuration=AgentEvaluationSamplingConfiguration(
                        name="default",
                        sampling_percent=100,
                    ),
                    redaction_configuration=AgentEvaluationRedactionConfiguration(
                        redact_score_properties=False,
                    ),
                    app_insights_connection_string=app_insights_connection_string,
                )

                evaluation_result = self.project_client.evaluations.create_agent_evaluation(
                    evaluation=_evaluation_request
                )

                logger.info(f"Successfully submitted evaluation job '{evaluation_result.id}' with status '{evaluation_result.status}'.")
                span.set_attribute("eval.job_id", evaluation_result.id)
                span.set_attribute("eval.status", "submitted")
                span.set_status(trace.Status(trace.StatusCode.OK))

            except Exception as e:
                logger.error(f"Service-side continuous evaluation failed for thread {thread_id}: {e}", exc_info=True)
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, description=str(e)))
