import os
import logging
import base64
import requests
from openai import OpenAI
import uuid
from PIL import Image
from io import BytesIO
from datetime import datetime
import time 
import secrets

logger = logging.getLogger(__name__)

class PhotoGenerator:
    def __init__(self, upload_folder):
        self.upload_folder = upload_folder
        self.gallery_folder = os.path.join(upload_folder, 'gallery')
        
        # Ensure gallery folder exists
        if not os.path.exists(self.gallery_folder):
            os.makedirs(self.gallery_folder)
        
    def generate_images(self, service, model, prompt, orientation, api_key, base_url=None, style_preset=None):
        """Generate images using the specified service."""
        try:
            # Validate and correct model names
            if service == 'dalle':
                if not model or model not in ['dall-e-2', 'dall-e-3']:
                    model = 'dall-e-3'  # default DALL-E model
                # For DALL-E, incorporate style into prompt if provided
                if style_preset:
                    prompt = f"{prompt} in {style_preset.replace('-', ' ')} style"
                return self._generate_dalle(model, prompt, orientation, api_key)
            elif service == 'stability':
                if not model or 'dall-e' in model:  # Check if wrong model is being used
                    model = 'stable-diffusion-xl-1024-v1-0'  # default Stability model
                return self._generate_stability(model, prompt, orientation, api_key, base_url, style_preset)
            else:
                raise ValueError(f"Unsupported service: {service}") 
        except Exception as e:
            logging.error(f"Error in generate_images: service={service}, model={model}")
            raise
            
    def _generate_dalle(self, model, prompt, orientation, api_key):
        """Generate images using DALL-E."""
        try:
            client = OpenAI(api_key=api_key)
            
            # Set size based on orientation
            if orientation == 'portrait':
                size = "1200x1600"
            elif orientation == 'landscape':
                size = "1600x1200"
            else:  # square
                size = "1024x1024"
            
            response = client.images.generate(
                model=model,
                prompt=prompt,
                n=1,
                size=size,
                response_format="b64_json"
            )
            
            return {'images': [response.data[0].b64_json]}
            
        except Exception as e:
            logging.error(f"DALL-E generation error: {str(e)}")
            raise
            
    def _generate_stability(self, model, prompt, orientation, api_key, base_url=None, style_preset=None):
        """Generate images using Stability AI."""
        try:
            # Set dimensions based on orientation
            if orientation == 'portrait':
                stability_size = {"width": 1200, "height": 1600}
            elif orientation == 'landscape':
                stability_size = {"width": 1600, "height": 1200}
            else:  # square
                stability_size = {"width": 1200, "height": 1200}

            # Use the correct model name
            if not model or 'dall-e' in model:  # Check if wrong model is being used
                model = 'ultra'  # default to 'ultra' model

            # Use the v2beta endpoint format with the correct model
            url = f"https://api.stability.ai/v2beta/stable-image/generate/{model}"
            
            headers = {
                "Accept": "image/*",
                "Authorization": f"Bearer {api_key.strip()}"
            }
            
            # Set aspect_ratio based on orientation
            if orientation == 'portrait':
                aspect_ratio = "4:5"
            elif orientation == 'landscape':
                aspect_ratio = "5:4"
            else:  # square
                aspect_ratio = "1:1"
            
            form_data = {
                "prompt": prompt,
                "width": str(stability_size["width"]),
                "height": str(stability_size["height"]),
                "aspect_ratio": aspect_ratio,
                "output_format": "jpeg",
            }
            
            # Add style_preset if provided
            if style_preset:
                form_data["style_preset"] = style_preset
            
            # Debug logging for request
            logging.info(f"Request headers: {headers}") 
            logging.info(f"Request data: {form_data}") 
            
            response = requests.post(
                url,
                headers=headers,
                files={"none": ""},
                data=form_data
            )
            
            if response.status_code != 200:
                logging.error(f"Stability API Error Response: {response.text}")
                logging.error(f"Status Code: {response.status_code}")
                raise Exception(f"Stability AI API Error: {response.text}")
            
            image_data = base64.b64encode(response.content).decode('utf-8')
            return {'images': [image_data]}
            
        except Exception as e:
            logging.error(f"Stability AI generation error: {str(e)}")
            raise
            
    def save_image(self, image_data, frame_id=None):
        """Save generated image and optionally add to frame."""
        try:
            # Decode base64 image
            image_bytes = base64.b64decode(image_data)
            with Image.open(BytesIO(image_bytes)) as image:
                # Generate unique filename
                filename = f"generated_{uuid.uuid4()}.jpg"
                filepath = os.path.join(self.upload_folder, filename)
                
                # Save image 
                image.save(filepath, "JPEG")
            
            return filename
            
        except Exception as e:
            logging.error(f"Error saving generated image: {str(e)}")
            raise

    def save_to_gallery(self, image_data, base64_encoded=True):
        """Save an image to the gallery folder, converting to JPG."""
        image = None
        background = None
        try:
            logging.info("Starting save_to_gallery process")
            
            # If the image is base64 encoded, decode it
            if base64_encoded:
                # Remove data URI prefix if present
                if ',' in image_data:
                    image_data = image_data.split(',')[1]
                image_binary = base64.b64decode(image_data)
            else:
                image_binary = image_data
            
            # Open the image with PIL
            image = Image.open(BytesIO(image_binary))
            logging.info(f"Original image mode: {image.mode}")
            
            # Convert to RGB mode if necessary
            if image.mode in ('RGBA', 'LA'):
                background = Image.new('RGB', image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image.close()
                image = background
                background = None  # Now owned by image
            elif image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Create unique filename with jpg extension
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'generated_image_{timestamp}.jpg'  # Explicitly use .jpg extension
            file_path = os.path.join(self.upload_folder, filename)
            
            # Save as JPG with high quality
            image.save(file_path, format='JPEG', quality=95)
            logging.info(f"Saved image as JPG: {file_path}")
            
            return filename
            
        except Exception as e:
            logging.error(f"Error saving image to gallery: {str(e)}")
            raise
        finally:
            if image is not None:
                try:
                    image.close()
                except Exception:
                    pass
            if background is not None:
                try:
                    background.close()
                except Exception:
                    pass

    def generate_photo(self, prompt, service, model, orientation):
        """Generate a photo using the specified AI service."""
        try:
            # Load API keys and base URLs from environment or config
            if service == 'dalle':
                # Use DALL-E service
                api_key = os.getenv('DALLE_API_KEY')
                base_url = os.getenv('DALLE_BASE_URL', 'https://api.openai.com/v1')
                
                # Set image size based on orientation
                if orientation == 'portrait':
                    size = "1024x1792"
                elif orientation == 'landscape':
                    size = "1792x1024"
                else:  # square
                    size = "1024x1024"
                
                # Make API request to DALL-E
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                data = {
                    "model": model,
                    "prompt": prompt,
                    "size": size,
                    "n": 1
                }
                
                response = requests.post(
                    f"{base_url}/images/generations",
                    headers=headers,
                    json=data
                )
                
                if response.status_code != 200:
                    raise Exception(f"DALL-E API error: {response.text}")
                
                # Get image URL from response
                image_url = response.json()['data'][0]['url']
                
                # Download the image
                image_response = requests.get(image_url)
                if image_response.status_code != 200:
                    raise Exception("Failed to download generated image")
                
                # Save the image
                filename = f"generated_{int(time.time())}_{secrets.token_hex(4)}.png"
                filepath = os.path.join(self.upload_folder, filename)
                
                with open(filepath, 'wb') as f:
                    f.write(image_response.content)
                
                return {
                    'filename': filename,
                    'filepath': filepath
                }
                
            elif service == 'stability':
                # Use Stability AI service
                api_key = os.getenv('STABILITY_API_KEY')
                base_url = os.getenv('STABILITY_BASE_URL', 'https://api.stability.ai/v2beta/stable-image/generate')
                
                # Set image size based on orientation
                if orientation == 'portrait':
                    width, height = 1200, 1600
                elif orientation == 'landscape':
                    width, height = 1600, 1200
                else:  # square
                    width = height = 1200
                
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                data = {
                    "text_prompts": [{"text": prompt}],
                    "cfg_scale": 7,
                    "height": height,
                    "width": width,
                    "samples": 1,
                    "steps": 50,
                }
                
                response = requests.post(
                    f"{base_url}/{model}",
                    headers=headers,
                    json=data
                )
                
                if response.status_code != 200:
                    raise Exception(f"Stability AI API error: {response.text}")
                
                # Save the image
                filename = f"generated_{int(time.time())}_{secrets.token_hex(4)}.png"
                filepath = os.path.join(self.upload_folder, filename)
                
                # First image from response
                image_data = response.json()['artifacts'][0]['base64']
                
                # Decode and save the image
                with open(filepath, 'wb') as f:
                    f.write(base64.b64decode(image_data))
                
                return {
                    'filename': filename,
                    'filepath': filepath
                }
            
            else:
                raise ValueError(f"Unsupported service: {service}")
                
        except Exception as e:
            logging.error(f"Error generating photo: {e}")
            return {'error': str(e)} 