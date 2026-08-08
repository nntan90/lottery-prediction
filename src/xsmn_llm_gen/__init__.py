"""Single-provider LLM ranking shadow for the merged XSMN scope."""

from .config import (
    LLMGenConfig,
    LLMGenConfigError,
    load_llm_gen_config,
)
from .evidence import build_evidence_packet, compute_input_hash
from .providers import (
    AnthropicAdapter,
    OpenAIAdapter,
    ProviderError,
    create_provider_adapter,
    ensure_agentrouter_model_available,
)
from .service import run_llm_gen, validate_ranked_candidates

__all__ = [
    "AnthropicAdapter",
    "LLMGenConfig",
    "LLMGenConfigError",
    "OpenAIAdapter",
    "ProviderError",
    "build_evidence_packet",
    "compute_input_hash",
    "create_provider_adapter",
    "ensure_agentrouter_model_available",
    "load_llm_gen_config",
    "run_llm_gen",
    "validate_ranked_candidates",
]
