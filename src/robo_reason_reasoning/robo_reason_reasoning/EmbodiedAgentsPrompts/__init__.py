"""
EmbodiedAgentsPrompts — prompt templates for all reasoning methods.

Each class exposes two static methods:
  get_llm_prompts() → prompts for text-only LLM mode (scene JSON as environment)
  get_vlm_prompts() → prompts for vision-language VLM mode (image as environment)

Keeping the two variants as separate methods (rather than runtime string patching)
means LLM and VLM prompts can evolve independently without risk of cross-contamination.
"""

from robo_reason_reasoning.EmbodiedAgentsPrompts.fhp_ffhp_prompts import FHP_FFHP_Prompts
from robo_reason_reasoning.EmbodiedAgentsPrompts.react_prompts import ReActPrompts
from robo_reason_reasoning.EmbodiedAgentsPrompts.cot_sc_prompts import CotScPrompts
from robo_reason_reasoning.EmbodiedAgentsPrompts.tot_prompts import ToTPrompts
from robo_reason_reasoning.EmbodiedAgentsPrompts.always_act_prompts import AlwaysActPrompts
from robo_reason_reasoning.EmbodiedAgentsPrompts.self_refine_prompts import SelfRefinePrompts
from robo_reason_reasoning.EmbodiedAgentsPrompts.predicates_prompts import PredicatesPrompts

__all__ = [
    'FHP_FFHP_Prompts',
    'ReActPrompts',
    'CotScPrompts',
    'ToTPrompts',
    'AlwaysActPrompts',
    'SelfRefinePrompts',
    'PredicatesPrompts',
]
