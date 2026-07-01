import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# Mock dependencies if not installed
try:
    import pandas as pd
except ImportError:
    # Create a mock pandas module
    mock_pd = MagicMock()
    mock_pd.DataFrame = MagicMock
    mock_pd.Timestamp.now.return_value = "2024-01-01"
    sys.modules["pandas"] = mock_pd
    import pandas as pd

try:
    import dotenv
except ImportError:
    mock_dotenv = MagicMock()
    sys.modules["dotenv"] = mock_dotenv

# Mock generic google module for direct imports in clients
try:
    import google.genai
except ImportError:
    mock_google = MagicMock()
    mock_genai_pkg = MagicMock()
    mock_google.genai = mock_genai_pkg
    sys.modules["google"] = mock_google
    sys.modules["google.genai"] = mock_genai_pkg

try:
    from pydantic import BaseModel
except ImportError:
    BaseModel = MagicMock()

# Now import the module to test
try:
    from . import base_client
    from .llm_client import LLMClient
    from .vlm_client import VLMClient
    from .base_client import ModelRegistry
except ImportError:
    import base_client
    from llm_client import LLMClient
    from vlm_client import VLMClient
    from base_client import ModelRegistry

class TestResponseModel(BaseModel):
    """Test Pydantic model for structured output tests."""
    name: str
    age: int


class TestFoundationClients(unittest.TestCase):

    def setUp(self):
        # Patch the SDK imports in the module
        base_client_module = base_client.__name__
        self.groq_patcher = patch(f'{base_client_module}.Groq')
        self.openai_patcher = patch(f'{base_client_module}.OpenAI')
        self.anthropic_patcher = patch(f'{base_client_module}.Anthropic')
        self.genai_patcher = patch(f'{base_client_module}.genai')

        self.mock_groq = self.groq_patcher.start()
        self.mock_openai = self.openai_patcher.start()
        self.mock_anthropic = self.anthropic_patcher.start()
        self.mock_genai = self.genai_patcher.start()
        
        # Setup mock return values
        self.mock_groq_instance = self.mock_groq.return_value
        self.mock_openai_instance = self.mock_openai.return_value
        self.mock_anthropic_instance = self.mock_anthropic.return_value
        # Gemini
        self.mock_genai_client = self.mock_genai.Client.return_value

        # Ensure clients are initialized with mocks even if imports failed in real module
        base_client.Groq = self.mock_groq
        base_client.OpenAI = self.mock_openai
        base_client.Anthropic = self.mock_anthropic
        base_client.genai = self.mock_genai

    def tearDown(self):
        self.groq_patcher.stop()
        self.openai_patcher.stop()
        self.anthropic_patcher.stop()
        self.genai_patcher.stop()

    def test_registry(self):
        self.assertEqual(ModelRegistry.get_model_id("groq", "llama3.1-8b"), "llama-3.1-8b-instant")
        self.assertEqual(ModelRegistry.get_model_id("openai", "gpt-4o"), "gpt-4o")
        self.assertEqual(ModelRegistry.get_model_id("nebius", "llama3.1-70b"), "meta-llama/Meta-Llama-3.1-70B-Instruct")
        self.assertEqual(ModelRegistry.get_model_id("nebius", "qwen2-vl-72b"), "Qwen/Qwen2-VL-72B-Instruct")
        self.assertEqual(ModelRegistry.get_model_id("unknown", "test-model"), "test-model")

    def test_llm_client_groq_init(self):
        client = LLMClient(model_name="groq/llama3.1-8b", api_key="test_key")
        self.assertEqual(client.provider, "groq")
        self.assertEqual(client.model_name, "llama-3.1-8b-instant")
        self.mock_groq.assert_called_with(api_key="test_key")

    def test_llm_client_call_groq(self):
        client = LLMClient(model_name="groq/llama3.1-8b", api_key="test_key")
        
        # Mock response
        mock_chat = self.mock_groq_instance.chat.completions.create
        mock_message = MagicMock()
        mock_message.choices[0].message.content = "Test response"
        mock_message.usage.prompt_tokens = 10
        mock_message.usage.completion_tokens = 20
        mock_chat.return_value = mock_message

        response = client("Hello")
        self.assertEqual(response, "Test response")
        
        # Check metrics (assuming pandas fits or is mocked)
        if hasattr(client, 'usage_metrics') and client.usage_metrics is not None:
             # If using real pandas or mock behaving like it
             pass

    def test_llm_client_nebius_init(self):
        client = LLMClient(model_name="nebius/llama3.1-70b", api_key="test_key")
        self.assertEqual(client.provider, "nebius")
        self.assertEqual(client.model_name, "meta-llama/Meta-Llama-3.1-70B-Instruct")
        self.mock_openai.assert_called_with(
            api_key="test_key",
            base_url="https://api.tokenfactory.nebius.com/v1/"
        )

    def test_llm_client_call_nebius_params(self):
        client = LLMClient(model_name="nebius/llama3.1-70b", api_key="test_key")

        mock_create = self.mock_openai_instance.chat.completions.create
        mock_message = MagicMock()
        mock_message.choices[0].message.content = "Nebius response"
        mock_message.usage.prompt_tokens = 10
        mock_message.usage.completion_tokens = 20
        mock_create.return_value = mock_message

        messages = [
            {
                "role": "system",
                "content": "You are a chemistry expert."
            },
            {
                "role": "user",
                "content": "Hello!"
            },
            {
                "role": "assistant",
                "content": "Hello! How can I assist you with chemistry today?"
            }
        ]

        response = client(
            messages=messages,
            max_tokens=100,
            temperature=1,
            top_p=1,
            top_k=50,
            n=1,
            stream=False,
            stop=None,
            presence_penalty=0,
            frequency_penalty=0,
            logit_bias=None,
            logprobs=False,
            top_logprobs=None,
            user=None,
            extra_body={
                "guided_json": {
                    "type": "object",
                    "properties": {}
                }
            },
            response_format={
                "type": "json_object"
            }
        )

        self.assertEqual(response, "Nebius response")
        call_args = mock_create.call_args[1]
        self.assertEqual(call_args["model"], "meta-llama/Meta-Llama-3.1-70B-Instruct")
        self.assertEqual(call_args["messages"], messages)
        self.assertEqual(call_args["max_tokens"], 100)
        self.assertEqual(call_args["temperature"], 1)
        self.assertEqual(call_args["top_p"], 1)
        self.assertEqual(call_args["n"], 1)
        self.assertFalse(call_args["stream"])
        self.assertEqual(call_args["presence_penalty"], 0)
        self.assertEqual(call_args["frequency_penalty"], 0)
        self.assertFalse(call_args["logprobs"])
        self.assertEqual(call_args["response_format"], {"type": "json_object"})
        self.assertEqual(call_args["extra_body"]["top_k"], 50)
        self.assertEqual(
            call_args["extra_body"]["guided_json"],
            {"type": "object", "properties": {}}
        )

    def test_vlm_client_openai_call(self):
        client = VLMClient(model_name="openai/gpt-4o", api_key="test_key")
        
        mock_create = self.mock_openai_instance.chat.completions.create
        mock_message = MagicMock()
        mock_message.choices[0].message.content = "Image description"
        mock_message.usage.prompt_tokens = 50
        mock_message.usage.completion_tokens = 10
        mock_create.return_value = mock_message

        # Mock opening file/image or pass url
        response = client("Describe", "https://example.com/image.jpg")
        self.assertEqual(response, "Image description")
        
        # Verify call arguments structure
        call_args = mock_create.call_args[1]
        self.assertEqual(call_args['model'], "gpt-4o")
        # Structure is messages -> content -> [text, image_url]
        self.assertEqual(call_args['messages'][0]['content'][1]['type'], "image_url")

    def test_vlm_client_nebius_call(self):
        client = VLMClient(model_name="nebius/qwen2-vl-72b", api_key="test_key")

        mock_create = self.mock_openai_instance.chat.completions.create
        mock_message = MagicMock()
        mock_message.choices[0].message.content = "Nebius image description"
        mock_message.usage.prompt_tokens = 50
        mock_message.usage.completion_tokens = 10
        mock_create.return_value = mock_message

        response = client(
            "What's in this image?",
            "https://example.com/image.jpg",
            max_tokens=300,
            temperature=0.2,
            top_p=0.9,
            top_k=50,
            stop=["END"],
            response_format={"type": "json_object"},
        )

        self.assertEqual(response, "Nebius image description")
        self.mock_openai.assert_called_with(
            api_key="test_key",
            base_url="https://api.tokenfactory.nebius.com/v1/"
        )

        call_args = mock_create.call_args[1]
        self.assertEqual(call_args["model"], "Qwen/Qwen2-VL-72B-Instruct")
        self.assertEqual(call_args["max_tokens"], 300)
        self.assertEqual(call_args["temperature"], 0.2)
        self.assertEqual(call_args["top_p"], 0.9)
        self.assertFalse(call_args["stream"])
        self.assertEqual(call_args["stop"], ["END"])
        self.assertEqual(call_args["response_format"], {"type": "json_object"})
        self.assertEqual(call_args["extra_body"]["top_k"], 50)
        self.assertEqual(call_args["messages"][0]["role"], "user")
        self.assertEqual(call_args["messages"][0]["content"][0], {"type": "text", "text": "What's in this image?"})
        self.assertEqual(
            call_args["messages"][0]["content"][1],
            {
                "type": "image_url",
                "image_url": {
                    "url": "https://example.com/image.jpg"
                }
            }
        )

    def test_llm_client_groq_structured_output(self):
        client = LLMClient(model_name="groq/llama3.1-8b", api_key="test_key")

        mock_create = self.mock_groq_instance.chat.completions.create
        mock_message = MagicMock()
        mock_message.choices[0].message.content = '{"name": "Alice", "age": 30}'
        mock_message.usage.prompt_tokens = 10
        mock_message.usage.completion_tokens = 20
        mock_create.return_value = mock_message

        response = client(
            "Extract name and age",
            force_json=True,
            forced_json_schema=TestResponseModel
        )

        call_args = mock_create.call_args[1]
        self.assertEqual(call_args["model"], "llama-3.1-8b-instant")
        expected_schema = TestResponseModel.model_json_schema()
        self.assertEqual(
            call_args["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "TestResponseModel",
                    "schema": expected_schema
                }
            }
        )
        self.assertEqual(response, '{"name": "Alice", "age": 30}')

    def test_llm_client_groq_json_mode_only(self):
        client = LLMClient(model_name="groq/llama3.1-8b", api_key="test_key")

        mock_create = self.mock_groq_instance.chat.completions.create
        mock_message = MagicMock()
        mock_message.choices[0].message.content = '{"key": "value"}'
        mock_message.usage.prompt_tokens = 10
        mock_message.usage.completion_tokens = 5
        mock_create.return_value = mock_message

        response = client(
            "Give me JSON",
            force_json=True
        )

        call_args = mock_create.call_args[1]
        self.assertEqual(call_args["response_format"], {"type": "json_object"})
        self.assertEqual(response, '{"key": "value"}')

    def test_vlm_client_groq_structured_output(self):
        client = VLMClient(model_name="groq/llama4-scout-17b", api_key="test_key")

        mock_create = self.mock_groq_instance.chat.completions.create
        mock_message = MagicMock()
        mock_message.choices[0].message.content = '{"name": "Bob", "age": 25}'
        mock_message.usage.prompt_tokens = 50
        mock_message.usage.completion_tokens = 10
        mock_create.return_value = mock_message

        response = client(
            "Extract info from image",
            "https://example.com/image.jpg",
            force_json=True,
            forced_json_schema=TestResponseModel
        )

        call_args = mock_create.call_args[1]
        self.assertEqual(call_args["model"], "meta-llama/llama-4-scout-17b-16e-instruct")
        expected_schema = TestResponseModel.model_json_schema()
        self.assertEqual(
            call_args["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "TestResponseModel",
                    "schema": expected_schema
                }
            }
        )
        self.assertEqual(response, '{"name": "Bob", "age": 25}')

    def test_vlm_client_groq_json_mode_only(self):
        client = VLMClient(model_name="groq/llama4-scout-17b", api_key="test_key")

        mock_create = self.mock_groq_instance.chat.completions.create
        mock_message = MagicMock()
        mock_message.choices[0].message.content = '{"result": "ok"}'
        mock_message.usage.prompt_tokens = 50
        mock_message.usage.completion_tokens = 5
        mock_create.return_value = mock_message

        response = client(
            "Describe the image as JSON",
            "https://example.com/image.jpg",
            force_json=True
        )

        call_args = mock_create.call_args[1]
        self.assertEqual(call_args["response_format"], {"type": "json_object"})
        self.assertEqual(response, '{"result": "ok"}')


if __name__ == '__main__':
    unittest.main()
