"""LLM client wrapper — ported verbatim from RoboReason-Lab."""
from groq import Groq
from dotenv import load_dotenv


class GroqLLMSRegistry:
    """Registry of supported LLM model names."""

    _groq_registry = {
        "llama3.1-8b": "llama-3.1-8b-instant",
        "llama3.3-70b": "llama-3.3-70b-versatile",
        "llama4-scout-17b": "meta-llama/llama-4-scout-17b-16e-instruct",
        "llama4-maverick-17b": "meta-llama/llama-4-maverick-17b-128e-instruct",
        "moonshotai-kimik2-32b": "moonshotai/kimi-k2-instruct-0905",
        "qwen3-32b": "qwen/qwen3-32b",
        "openai-oss-20b": "openai/gpt-oss-20b",
        "openai-oss-120b": "openai/gpt-oss-120b",
    }

    _openai_registry = {
        "gpt-4": "gpt-4",
        "gpt-4-turbo": "gpt-4-turbo",
        "gpt-4o": "gpt-4o",
        "gpt-4o-mini": "gpt-4o-mini",
    }

    @staticmethod
    def _get_groq_model_name(model_name: str) -> str:
        assert model_name in GroqLLMSRegistry._groq_registry, NotImplementedError(
            f"Model {model_name} not supported. Supported: {list(GroqLLMSRegistry._groq_registry.keys())}"
        )
        return GroqLLMSRegistry._groq_registry.get(model_name)

    @staticmethod
    def _get_openai_model_name(model_name: str) -> str:
        assert model_name in GroqLLMSRegistry._openai_registry, NotImplementedError(
            f"Model {model_name} not supported. Supported: {list(GroqLLMSRegistry._openai_registry.keys())}"
        )
        return GroqLLMSRegistry._openai_registry.get(model_name)


class LLMClient:
    """Unified LLM client for Groq (and OpenAI in future)."""

    def __init__(self, **model_parameters):
        load_dotenv()

        assert 'model_name' in model_parameters, "model_name must be provided."

        model_name = model_parameters['model_name']
        self.provider = model_name.split("/")[0]
        self.model_name = self.get_model_name(
            model_name.split("/")[1] if "/" in model_name else model_name
        )
        self.temperature = model_parameters.get("temperature", 0.0)
        self.max_tokens = model_parameters.get("max_tokens", 8192)
        self.top_p = model_parameters.get("top_p", 1.0)
        self.stream = model_parameters.get("stream", False)
        self.stop = model_parameters.get("stop", None)

        self.client = Groq()
        self.usage_metrics = []

    def get_model_name(self, model_name: str) -> str:
        if self.provider == "groq":
            return GroqLLMSRegistry._get_groq_model_name(model_name)
        elif self.provider == "openai":
            return GroqLLMSRegistry._get_openai_model_name(model_name)
        else:
            raise NotImplementedError(f"Provider '{self.provider}' not supported.")

    def get_total_used_tokens(self) -> int:
        return sum(m.get('total_tokens', 0) for m in self.usage_metrics)

    def _update_usage_metrics(self, **usage_metrics):
        self.usage_metrics.append(usage_metrics)

    def log_usage_metrics(self):
        if self.usage_metrics:
            print(f"({self.__class__.__name__}) LLM Usage Metrics:")
            for m in self.usage_metrics:
                print(m)
        else:
            print("No usage metrics available.")

    def __call__(self, user_message: str,
                 system_message: str = "You are a helpful assistant.",
                 force_json: bool = False, **kwargs):
        assert user_message, "User message cannot be empty."

        parameters = {
            "temperature": kwargs.get("temperature", self.temperature),
            "max_completion_tokens": kwargs.get("max_tokens", self.max_tokens),
            "top_p": kwargs.get("top_p", self.top_p),
            "stream": kwargs.get("stream", self.stream),
            "stop": kwargs.get("stop", self.stop),
        }

        if self.provider == 'groq':
            request = {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_message},
                ],
                **parameters,
            }
            if force_json:
                request["response_format"] = {"type": "json_object"}

            completion = self.client.chat.completions.create(**request)
            self._update_usage_metrics(**dict(completion.usage))
            return completion.choices[0].message.content

        else:
            raise NotImplementedError(f"Provider {self.provider} not implemented.")


if __name__ == "__main__":
    
    # Example usage
    llm_client = LLMClient(model_name="groq/openai-oss-120b", temperature=0.7)
    response = llm_client("What is the capital of France?")
    print(response)
    llm_client.log_usage_metrics()