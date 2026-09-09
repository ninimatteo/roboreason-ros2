"""Configuration options surfaced to the GUI selectors.

Provider/model lists are pulled from the reasoning package's ModelRegistry so
the dropdowns never drift from the backend. Only groq + nebius are exposed
(the providers actually in use), even though the registry defines more.

---- Changing models / providers ------------------------------------------------
Edit the two dicts below and restart the GUI node — no rebuild needed:

  GUI_PROVIDERS     — inference providers shown in both modes.
  VISION_MODELS     — per-provider models that accept an image. They appear
                      in the Model dropdown when Mode=VLM. 2026-09-04: they
                      are now ALSO offered in Mode=LLM (text-only call, no
                      image) — every vision model here can run text-only, so
                      excluding them from LLM mode only blocked direct 1:1
                      comparisons against text-only models on the same task.
                      All remaining models from ModelRegistry are LLM-only.
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

# ---- Vision-capable model configuration (edit here) --------------------------
# Models listed here appear in the Model dropdown for both Mode=LLM (text
# only) and Mode=VLM (with image). To add one: append its key (as it appears
# in ModelRegistry) to the relevant provider list. To add a new provider: add
# a new key.
# List order is also selection order: the frontend fills each <select> in
# this order and the browser defaults to the first <option>, so the first
# entry per provider is the de facto default VLM model.
# 2026-09-04: 'kimi-k2.6' is now first/default for nebius, replacing
# 'nvidia-cosmos3-33b' (operator's earlier pick) — cosmos3 returned a live
# 409 "model is stopped" on Nebius (a serverless deployment/warm-up state on
# their side, not our bug) while kimi-k2.6 ran sort_hard and arith_hard
# clean back to back on real hardware. cosmos3-33b stays listed (it may just
# have been cold) but is no longer the default. None of the three is a
# verified equivalent to the July VLM arm's 'qwen3-2.5-70b' (gone from the
# catalog, see base_client.py) — any new mode=VLM + Nebius run is a fresh
# baseline, not comparable to the July numbers.
# 2026-09-03: 'qwen3-2.5-70b' (Qwen2.5-VL-72B-Instruct, the July benchmark's
# VLM-arm model) is gone from Nebius's catalog entirely.
# NOTE: 'kimi-k3' was briefly listed here and was wrong at the time — it
# 404'd on api.tokenfactory.nebius.com (what this client actually calls,
# unlike api.studio.nebius.com, a different host with its own catalog) when
# checked 2026-09-04 morning. It reappeared on tokenfactory a few hours
# later the same day — this catalog changes within a single session, not
# just week to week. Re-verify live (an actual chat completion, not just
# GET /v1/models — that listing has lagged real availability both ways) each
# time before trusting an entry here, not just once.
VISION_MODELS: dict = {
    'groq':   ['qwen3.6-27b', 'qwen3.8-27b'],
    'nebius': ['kimi-k2.6', 'nvidia-cosmos3-33b', 'minicpm-v-4.5'],
}

# Models present in ModelRegistry that are NOT chat-capable (e.g. embeddings).
# Excluded from both LLM and VLM dropdowns.
NON_CHAT_MODELS: dict = {
    'nebius': ['qwen3-embedding-8b'],
}

# ---- Speed tiers (measured, not guessed from parameter count) ----------------
# One live "reply with one word" chat-completion call per model, single
# sample (not averaged over repeats — noisy, but the tiers are wide enough
# that it's a reasonable first cut), 2026-09-04. Thresholds: fast <1s,
# medium 1-5s, slow >5s. All 4 Groq models measured <1s (Groq's dedicated
# LPU hardware — expected). Not covered: the 4 Nebius models that were
# returning 409 "model is stopped" that day (can't time an inactive
# deployment) and the embeddings-only model (excluded above, not a chat
# call). A model absent from here just shows no tier suffix in the
# dropdown rather than a guess.
MODEL_SPEED_TIER: dict = {
    'groq': {
        'openai-oss-20b': 'fast', 'openai-oss-120b': 'fast',
        'qwen3.6-27b': 'fast', 'qwen3.8-27b': 'fast',
    },
    'nebius': {
        'minicpm-v-4.5': 'fast', 'qwen3-30b': 'fast', 'qwen3-235b': 'fast',
        'openai-oss-120b': 'fast', 'hermes-4-405b': 'fast',
        'nvidia-nemotron-30b': 'fast', 'deepseek-v4-pro': 'fast',
        'minimax-m3': 'fast', 'kimi-k2.7-code': 'fast',
        'nvidia-nemotron-120b': 'fast', 'nemotron-3-ultra-550b': 'fast',
        'nemotron-3.5-lightning': 'fast', 'kimi-k2.6': 'fast',
        'google-gemma-27b': 'medium', 'glm-5.2': 'medium',
        'llama-3.3-70b': 'medium', 'qwen3.5-397b': 'medium',
        'glm-5.1': 'slow', 'deepseek-v4-flash': 'slow',
        'glm-5.3-flash': 'slow',
    },
}
# -----------------------------------------------------------------------------


def _labeled_models(provider: str, keys: list) -> list:
    """Pairs each model key with a '{key} ({tier})' display label (tier
    omitted when unmeasured) — value sent to the backend stays the bare
    key; only the dropdown's visible text changes. See MODEL_SPEED_TIER.
    """
    tiers = MODEL_SPEED_TIER.get(provider, {})
    return [
        {'value': k, 'label': f'{k} ({tiers[k]})' if k in tiers else k}
        for k in keys
    ]


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
            excluded = set(NON_CHAT_MODELS.get(name, []))
            # LLM mode: every chat-capable model, vision-capable ones
            # included (they run text-only fine — see VISION_MODELS above)
            # so they can be compared 1:1 against text-only models.
            llm_keys = [m for m in all_models if m not in excluded]
            llm_providers[name] = _labeled_models(name, llm_keys)
            # VLM mode: only the explicitly listed vision-capable models.
            vlm_keys = [m for m in VISION_MODELS.get(name, []) if m in registry[name]]
            vlm_providers[name] = _labeled_models(name, vlm_keys)
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
