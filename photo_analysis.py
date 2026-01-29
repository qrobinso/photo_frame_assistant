import os
from PIL import Image
import base64
import io
import requests
from datetime import datetime
import logging
from openai import OpenAI
import httpx
import json
import re
import ast
import demjson3

class PhotoAnalyzer:
    def __init__(self, app, db):
        self.app = app
        self.db = db
        self.logger = logging.getLogger(__name__)
        
        # Load settings from JSON file
        with open('photogen_settings.json') as f:
            settings = json.load(f)
            
        self.client = OpenAI(
            base_url=settings["custom_server_base_url"],
            api_key=settings["custom_server_api_key"],
            http_client=httpx.Client()
        )
        self.custom_model = settings["default_models"]["custom"]

    def analyze_photo(self, photo_id, model=None):
        """Analyze a photo using the local AI model."""
        model = model or self.custom_model
        
        with self.app.app_context():
            try:
                self.logger.info(f"Starting analysis of photo {photo_id}")
                from server import Photo
                photo = self.db.session.get(Photo, photo_id)
                if not photo:
                    raise ValueError(f"Photo {photo_id} not found")
                
                # First attempt
                try:
                    photo_path = os.path.join(self.app.config['UPLOAD_FOLDER'], photo.filename)
                    self.logger.info(f"Processing image at {photo_path}")
                    
                    with Image.open(photo_path) as img:
                        buffer = io.BytesIO()
                        img.save(buffer, format="JPEG")
                        encoded_image = base64.b64encode(buffer.getvalue()).decode() 
                    
                    # Simplified prompt focused on clean sentence output
                    structured_prompt = """CRITICAL: You MUST return ONLY a array of descriptive sentences. DO NOT add any other text:

[
    "Brief factual description of the overall scene.",
    "The scene shows [location] with [weather/lighting conditions].",
    "There are [number] people present in the image.",
    "Key objects visible include [list 3-5 main items].",
    "Likely event such as a new years eve party, baby shower, or birthday.",
    "The image has a [mood] atmosphere with prominent [colors] colors."
]

Format Rules:
- Return ONLY the array
- Each sentence should be complete, natural, concise, and factual
- NO explanations or additional text
- Keep descriptions concise and factual
- Maintain exact format with double quotes"""

                    completion = self.client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": structured_prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}}
                                ]
                            }
                        ],
                        max_tokens=300
                    )
                    
                    if not completion or not completion.choices:
                        self.logger.error(f"No response received for photo {photo_id}")
                        return False
                    
                    raw_response = completion.choices[0].message.content
                    
                    # Store raw response immediately
                    photo.ai_raw_response = raw_response
                    photo.ai_analyzed_at = datetime.utcnow()
                    
                    # Parse response
                    try:
                        json_str = raw_response.strip()
                        json_str = json_str.replace('```json', '').replace('```', '')
                        
                        try:
                            sentences = demjson3.decode(json_str)
                        except Exception:
                            sentences = [
                                s.strip() + '.'
                                for s in json_str.split('.')
                                if s.strip()
                            ]
                            sentences = [s for s in sentences if s != '.']
                        
                        if not isinstance(sentences, list):
                            sentences = [str(sentences)]
                        sentences = [str(s) for s in sentences]
                        
                        photo.ai_description = sentences
                        self.db.session.commit()
                        
                        self.logger.info(f"Successfully analyzed photo {photo_id}")
                        return True

                    except Exception as e:
                        self.logger.error(f"Failed to parse response for photo {photo_id}: {str(e)}")
                        return False
                        
                except Exception as e:
                    self.logger.error(f"Analysis failed for photo {photo_id}: {str(e)}")
                    return False
                    
            except Exception as e:
                self.logger.error(f"Error analyzing photo {photo_id}: {e}", exc_info=True)
                return False