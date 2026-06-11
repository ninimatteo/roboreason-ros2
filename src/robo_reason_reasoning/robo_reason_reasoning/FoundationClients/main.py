import os
try:
    from .src.llm_client import LLMClient
    from .src.vlm_client import VLMClient
except ImportError:
    from src.llm_client import LLMClient
    from src.vlm_client import VLMClient

# Mock API keys for demonstration if not present
if not os.getenv("GROQ_API_KEY"): 
    os.environ["GROQ_API_KEY"] = "mock_groq_key"

def main():
    print("--- LLM Client Example ---")
    try:
        llm = LLMClient(model_name="groq/<any_llm>", temperature=0.7)
        print(f"Initialized LLM: {llm.model_name}")
        
        # In a real scenario, we would call:
        # response = llm("What is the capital of France?")
        # print("Response:", response)
        print("LLM Client initialized successfully.")
    except Exception as e:
        print(f"LLM Client init failed: {e}")

    print("\n--- VLM Client Example ---")
    try:
        vlm = VLMClient(model_name="groq/<any_vlm>")
        print(f"Initialized VLM: {vlm.model_name}")
        
        # response = vlm("Describe this image", "https://example.com/image.jpg")
        # print("Response:", response)
        print("VLM Client initialized successfully.")
    except Exception as e:
         print(f"VLM Client init failed: {e}")

if __name__ == "__main__":
    main()
