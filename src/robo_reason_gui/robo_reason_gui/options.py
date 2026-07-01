"""Configuration options surfaced to the GUI selectors.

Provider/model lists are pulled from the reasoning package's ModelRegistry so
the dropdowns never drift from the backend. Only groq + nebius are exposed
(the providers actually in use), even though the registry defines more.

---- Changing models / providers ------------------------------------------------
Edit the two dicts below and restart the GUI node — no rebuild needed:

  GUI_PROVIDERS     — inference providers shown in both modes.
  VLM_ONLY_MODELS   — per-provider models that *only* work with a VLM/vision
                      prompt.  They appear in the Model dropdown when Mode=VLM
                      and are hidden when Mode=LLM.  All remaining models from
                      ModelRegistry are treated as LLM-only (or universal).
--------------------------------------------------------------------------------
"""

# Reasoning methods mirror the dispatch in
# robo_reason_reasoning/embodied_agent.py. That module has no lightweight
# constant to import and pulling it in here would drag in heavy deps
# (roboticstoolbox, the clients, ...), so the canonical list is mirrored.
# Keep in sync if the methods in embodied_agent change.
REASONING_METHODS = ['fhp', 'ffhp', 'react', 'cot_sc', 'tot', 'always_act', 'self_refine']

GUI_PROVIDERS = ('groq', 'nebius')

MODES = ['LLM', 'VLM', 'VLM_LLM']

DEFAULT_TEMPERATURE = 0.1

# ---- VLM model configuration (edit here) ------------------------------------
# Models listed here are shown *only* in VLM mode and are hidden in LLM mode.
# To add a new VLM model: append its key (as it appears in ModelRegistry) to
# the relevant provider list.  To add a new provider: add a new key.
VLM_ONLY_MODELS: dict = {
    'groq':   ['qwen3.6-27b'],
    'nebius': ['qwen3-2.5-70b'],
}
# -----------------------------------------------------------------------------


def get_options() -> dict:
    """Build the options payload, sourcing model lists from ModelRegistry."""
    llm_providers: dict = {}
    vlm_providers: dict = {}
    error = None
    try:
        from robo_reason_reasoning.FoundationClients.src.base_client import ModelRegistry
        registry = {
            'groq': ModelRegistry.GROQ_MODELS,
            'nebius': ModelRegistry.NEBIUS_MODELS,
        }
        for name in GUI_PROVIDERS:
            all_models = sorted(registry[name].keys())
            vlm_only = set(VLM_ONLY_MODELS.get(name, []))
            # LLM mode: all models except those reserved for VLM.
            llm_providers[name] = [m for m in all_models if m not in vlm_only]
            # VLM mode: only the explicitly listed vision-capable models.
            vlm_providers[name] = [m for m in VLM_ONLY_MODELS.get(name, [])
                                   if m in registry[name]]
    except Exception as exc:  # pragma: no cover - defensive, surfaced in UI
        error = f'{type(exc).__name__}: {exc}'

    payload = {
        'providers': llm_providers,
        'vlm_providers': vlm_providers,
        'reasoning_methods': REASONING_METHODS,
        'modes': MODES,
        'temperature_default': DEFAULT_TEMPERATURE,
    }
    if error:
        payload['error'] = error
    return payload
