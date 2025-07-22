import os
import logging
import uuid
from typing import Optional
from opentelemetry import trace

logger = logging.getLogger(__name__)

class SDKEvaluationManager:
    """Handles local evaluation using Azure AI Evaluation SDK."""
    
    def __init__(self, tracer):
        self.tracer = tracer
        self.evaluation_available = False
        self.evaluators = {}
        self._initialize_evaluation()

    def _initialize_evaluation(self):
        """Initialize evaluation capabilities if available."""
        try:
            # Try to import azure.ai.evaluation dynamically
            from azure.ai.evaluation import (
                RelevanceEvaluator, 
                CoherenceEvaluator, 
                FluencyEvaluator,
                TaskAdherenceEvaluator,
                AzureOpenAIModelConfiguration
            )
            
            # Initialize model configuration for evaluators
            if all(os.getenv(var) for var in ["AZURE_ENDPOINT", "AZURE_API_KEY", "AZURE_DEPLOYMENT_NAME"]):
                self.model_config = AzureOpenAIModelConfiguration(
                    azure_endpoint=os.environ["AZURE_ENDPOINT"],
                    api_key=os.environ["AZURE_API_KEY"],
                    azure_deployment=os.environ["AZURE_DEPLOYMENT_NAME"],
                    api_version=os.getenv("AZURE_API_VERSION", "2024-02-01"),
                )
                
                # Initialize evaluators to match devops_agent
                self.evaluators = {
                    "relevance": RelevanceEvaluator(self.model_config),
                    "task_adherence": TaskAdherenceEvaluator(self.model_config),
                    "coherence": CoherenceEvaluator(self.model_config),
                    "fluency": FluencyEvaluator(self.model_config)
                }
                
                self.evaluation_available = True
                logger.info("Azure AI Evaluation SDK initialized successfully")
            else:
                logger.warning("Azure OpenAI configuration incomplete. Evaluations will be skipped.")
                
        except ImportError as e:
            logger.warning(f"Azure AI Evaluation SDK not available: {e}. Evaluations will be skipped.")
        except Exception as e:
            logger.error(f"Failed to initialize evaluation: {e}")

    async def evaluate_conversation(self, 
                                  thread_id: str, 
                                  run_id: str, 
                                  user_input: str, 
                                  assistant_response: str,
                                  input_tokens: int,
                                  output_tokens: int):
        """
        Evaluate a single conversation turn using built-in evaluators.
        """
        if not self.evaluation_available:
            logger.debug("Evaluation not available, skipping")
            return

        evaluation_id = str(uuid.uuid4())
        
        try:
            # Run evaluations
            for evaluator_name, evaluator in self.evaluators.items():
                try:
                    await self._run_single_evaluation(
                        evaluator_name, evaluator, evaluation_id, 
                        thread_id, run_id, user_input, assistant_response,
                        input_tokens, output_tokens
                    )
                except Exception as e:
                    logger.error(f"Failed to run {evaluator_name} evaluation: {e}")
                    
        except Exception as e:
            logger.error(f"SDK evaluation failed: {e}", exc_info=True)

    async def _run_single_evaluation(self, 
                                   evaluator_name: str,
                                   evaluator,
                                   evaluation_id: str,
                                   thread_id: str,
                                   run_id: str,
                                   query: str,
                                   response: str,
                                   input_tokens: int,
                                   output_tokens: int):
        """Run a single evaluator and emit telemetry."""
        
        try:
            # Run the evaluation
            result = evaluator(query=query, response=response)
            
            # Extract score and reasoning
            score = self._extract_score(result, evaluator_name)
            reasoning = self._extract_reasoning(result, evaluator_name)
            
            # Emit evaluation telemetry as a proper trace event
            self._emit_evaluation_telemetry(
                evaluator_name, evaluation_id, thread_id, run_id,
                score, reasoning, input_tokens, output_tokens
            )
            
        except Exception as e:
            logger.error(f"Failed to run {evaluator_name} evaluation: {e}")
            raise

    def _extract_score(self, result, evaluator_name: str) -> Optional[float]:
        """Extract score from evaluation result."""
        if hasattr(result, evaluator_name):
            return getattr(result, evaluator_name)
        if hasattr(result, 'score'):
            return result.score
        if isinstance(result, dict):
            return result.get(evaluator_name) or result.get('score')
        return None

    def _extract_reasoning(self, result, evaluator_name: str) -> Optional[str]:
        """Extract reasoning from evaluation result."""
        reasoning_key = f"{evaluator_name}_reason"
        if hasattr(result, reasoning_key):
            return getattr(result, reasoning_key)
        if hasattr(result, 'reason'):
            return result.reason
        if isinstance(result, dict):
            return result.get(reasoning_key) or result.get('reason')
        return None

    def _emit_evaluation_telemetry(self, 
                                 evaluator_name: str,
                                 evaluation_id: str,
                                 thread_id: str,
                                 run_id: str,
                                 score: Optional[float],
                                 reasoning: Optional[str],
                                 input_tokens: int,
                                 output_tokens: int):
        """Emit evaluation result as telemetry trace with proper message format."""
        
        # Create a trace span for evaluation telemetry
        with self.tracer.start_as_current_span("evaluation_telemetry") as span:
            # Set span attributes for context
            span.set_attribute("gen_ai.evaluation.id", evaluation_id)
            span.set_attribute("gen_ai.evaluator.name", evaluator_name.replace("_", "").title())
            span.set_attribute("gen_ai.thread.id", thread_id)
            span.set_attribute("gen_ai.thread.run.id", run_id)
            
            # Create a logger that will emit the trace with the exact message we want
            telemetry_logger = logging.getLogger("gen_ai.evaluation")
            
            # Build the exact custom dimensions that match the expected format
            custom_dimensions = {
                "gen_ai.thread.id": thread_id,
                "gen_ai.thread.run.id": run_id,
                "gen_ai.evaluation.id": evaluation_id,
                "gen_ai.evaluator.id": f"azureai://built-in/evaluators/{evaluator_name}",
                "gen_ai.evaluator.name": evaluator_name.replace("_", "").title(),
                "gen_ai.usage.input_tokens": str(input_tokens),
                "gen_ai.usage.output_tokens": str(output_tokens)
            }
            
            # Add score and reasoning if available
            if score is not None:
                custom_dimensions["gen_ai.evaluation.score"] = str(score)
            if reasoning:
                custom_dimensions["gen_ai.evaluation.reasoning"] = reasoning
            
            # Log the message with the exact format expected
            telemetry_logger.info(
                "gen_ai.evaluation.result",
                extra=custom_dimensions
            )
            
            span.set_status(trace.Status(trace.StatusCode.OK))
        
        logger.info(f"Evaluation result emitted - {evaluator_name}: {score}")
