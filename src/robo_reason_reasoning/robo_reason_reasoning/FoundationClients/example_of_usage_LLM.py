"""
Example of usage of the LLM client. This file is meant to be run as a script
"""

def test_groq():
    return {
        "model_name": "groq/openai-oss-120b",
        'temperature': 0.7,
        'max_tokens': 2048,
        'top_p': 0.9
    }
    

def test_nebius():
    return {
        "model_name": "nebius/qwen3-2.5-70b",
        'temperature': 0.7,
        'max_tokens': 2048,
        'top_p': 0.9,
        'logprobs': False,
        'full_content': False
    }

if __name__ == "__main__":

    try:
        from .src.llm_client import LLMClient
    except ImportError:
        from src.llm_client import LLMClient
    
    use_nebius = False
    use_groq = True

    if use_nebius:
        model_parameters = test_nebius()
    elif use_groq:
        model_parameters = test_groq()

    system_message = "You are a helpful assistant, with strong critical and analytical skills, but very smart at emotional intelligence."
    user_message = "What would be a good travel itinerary to move from Italian capital to italian finance capital?"

    client = LLMClient(
        **model_parameters
    )

    response = client(
        system_message=system_message,
        user_message=user_message
    )

    print(response)
