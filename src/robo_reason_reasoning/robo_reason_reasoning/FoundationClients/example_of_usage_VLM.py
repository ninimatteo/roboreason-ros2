import os
try:
    from .src.vlm_client import VLMClient
except ImportError:
    from src.vlm_client import VLMClient
from traceback import format_exc
from PIL import Image
import json

def test_groq_vlm():

    return {
        "model_name": "groq/llama4-scout-17b",
        'temperature': 0.7,
        'max_tokens': 2048,
        'top_p': 0.9
    }

def test_nebius_vlm():

    return {
        "model_name": "nebius/qwen3-2.5-70b",
        'temperature': 0.7,
        'max_tokens': 2048,
        'top_p': 0.9,
    }

def test_response_with_url_image():
    """Passes back an url of an image."""
    return "https://commons.wikimedia.org/wiki/File:Valentino_Rossi_2017.jpg#/media/File:Valentino_Rossi_2017.jpg"
        
def test_response_with_local_image(image_path: str=None):
    try:
        with Image.open(image_path) as image:
            pixels_width, pixels_height = image.size
            return image_path, pixels_width, pixels_height
    except Exception as e:
        print(format_exc())
        return None, None, None
    

if __name__ == "__main__":
        
        use_nebius = True
        use_groq = False

        if use_nebius:
            model_parameters = test_nebius_vlm()
        elif use_groq:
            model_parameters = test_groq_vlm()
             
        vlm = VLMClient(**model_parameters)
        task = 'Find all the faces in the image. If there are specific known, please label them with their names.'
        test_image_path = os.path.join(os.path.dirname(__file__), 'test_images', 'test_silvio.jpg')   # adapt to your local path
        
        bb_prompt = """
        Task: {task}.
        The image is provided in the size of {pixels_width} x {pixels_height}.
        Strictly use the following json format for the response, avoid any additional text or explanation.

        {{
        "bounding_boxes": [
            {{
                "label": "detection-label",
                "x_min": top-left-x-pixel,
                "y_min": top-left-y-pixel,
                "x_max": bottom-right-x-pixel,
                "y_max": bottom-right-y-pixel
            }}, 
            ]
        }}
        """

        image_path, pixels_width, pixels_height = test_response_with_local_image(
            test_image_path
        )

        bb_prompt = bb_prompt.format(
            task=task, pixels_width=pixels_width, pixels_height=pixels_height
        )

        response = vlm(
            text_prompt=bb_prompt,
            image=image_path,
            force_json_response=True
        )

        print("VLM Response:\n", response)
        
        response_data = json.loads(response) if isinstance(response, str) else response
        vlm._draw_bbs(response_data.get("bounding_boxes", []), image_path, print=True)
        vlm.get_total_usage()
