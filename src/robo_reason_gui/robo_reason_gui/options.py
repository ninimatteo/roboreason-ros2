"""Configuration options surfaced to the GUI selectors.

Provider/model lists are pulled from the reasoning package's ModelRegistry so
the dropdowns never drift from the backend. Only groq + nebius are exposed
(the providers actually in use), even though the registry defines more.
"""

# Reasoning methods mirror the dispatch in
# robo_reason_reasoning/embodied_agent.py. That module has no lightweight
# constant to import and pulling it in here would drag in heavy deps
# (roboticstoolbox, the clients, ...), so the canonical list is mirrored.
# Keep in sync if the methods in embodied_agent change.
REASONING_METHODS = ['fhp', 'ffhp', 'react', 'cot_sc', 'tot', 'always_act', 'self_refine']

GUI_PROVIDERS = ('groq', 'nebius')

MODES = ['LLM', 'VLM']

DEFAULT_TEMPERATURE = 0.1


def get_options() -> dict:
    """Build the options payload, sourcing model lists from ModelRegistry."""
    providers: dict = {}
    error = None
    try:
        from robo_reason_reasoning.FoundationClients.src.base_client import ModelRegistry
        registry = {
            'groq': ModelRegistry.GROQ_MODELS,
            'nebius': ModelRegistry.NEBIUS_MODELS,
        }
        for name in GUI_PROVIDERS:
            providers[name] = sorted(registry[name].keys())
    except Exception as exc:  # pragma: no cover - defensive, surfaced in UI
        error = f'{type(exc).__name__}: {exc}'

    payload = {
        'providers': providers,
        'reasoning_methods': REASONING_METHODS,
        'modes': MODES,
        'temperature_default': DEFAULT_TEMPERATURE,
    }
    if error:
        payload['error'] = error
    return payload
