import os
import json
import requests
from datetime import datetime
from PIL import Image
import io
import logging

logger = logging.getLogger(__name__)

class UnsplashIntegration:
    def __init__(self, config_path):
        self.config_path = config_path
        self.settings = self.load_settings()
        self.base_url = "https://api.unsplash.com"

    def load_settings(self):
        """Load Unsplash settings from config file."""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading Unsplash settings: {e}")
        return {'api_key': None}

    def save_settings(self, settings):
        """Save Unsplash settings to config file."""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(settings, f, indent=4)
            self.settings = settings
            return True
        except Exception as e:
            logger.error(f"Error saving Unsplash settings: {e}")
            return False

    def get_random_photos(self, query=None, orientation=None, count=1):
        """Get random photos from Unsplash, optionally filtered by query and orientation."""
        if not self.settings.get('api_key'):
            raise ValueError("Unsplash API key not configured")

        params = {
            'count': min(count, 5),  # Unsplash limits to 30 photos per request
            'content_filter': 'high'
        }
        if query:
            params['query'] = query
        # Only add orientation if it's a valid value
        if orientation and orientation in ['landscape', 'portrait', 'squarish']:
            params['orientation'] = orientation
        elif orientation:
            logger.warning(f"Invalid orientation value: {orientation}, not including in request")

        headers = {
            'Authorization': f"Client-ID {self.settings['api_key']}",
            'Accept-Version': 'v1'
        }

        try:
            logger.info(f"Making Unsplash API request with params: {params}")
            
            # Log the request URL and headers (without the API key)
            request_url = f"{self.base_url}/photos/random"
            logger.info(f"Unsplash API URL: {request_url}")
            logger.info(f"Request parameters: {params}")
            
            response = requests.get(
                request_url,
                params=params,
                headers=headers
            )
            
            # Log response status code
            logger.info(f"Unsplash API response status code: {response.status_code}")
            
            # Check for error responses
            response.raise_for_status()
            
            # Try to parse the JSON response
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Response content: {response.text[:500]}...")  # Log first 500 chars
                raise ValueError(f"Invalid JSON response from Unsplash API: {e}")
            
            # If count=1, the API returns a single object instead of an array
            if count == 1 and not isinstance(data, list):
                data = [data]
                
            logger.info(f"Successfully retrieved {len(data)} photos from Unsplash")
            return data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting random Unsplash photos: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response status code: {e.response.status_code}")
                logger.error(f"Response content: {getattr(e.response, 'content', 'No content')[:500]}...")
                if e.response.status_code == 400:
                    logger.error("Bad request - check API parameters")
                    # Log each parameter to help diagnose the issue
                    for key, value in params.items():
                        logger.error(f"Parameter {key}: {value}")
            raise

    def download_photo(self, photo_data, upload_folder):
        """Download a photo from Unsplash and save it locally."""
        try:
            # Track the download with Unsplash
            requests.get(
                photo_data['links']['download_location'],
                headers={'Authorization': f"Client-ID {self.settings['api_key']}"}
            )

            # Download the photo
            response = requests.get(photo_data['urls']['full'])
            response.raise_for_status()

            # Create a unique filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"unsplash_{timestamp}_{photo_data['id']}.jpg"
            filepath = os.path.join(upload_folder, filename)

            # Save the photo
            with open(filepath, 'wb') as f:
                f.write(response.content)

            # Prepare metadata
            photographer = photo_data['user']['name']
            unsplash_link = photo_data['links']['html']
            heading = f"Photo by {photographer} on Unsplash"

            return {
                'filename': filename,
                'heading': heading,
                'photographer': photographer,
                'unsplash_link': unsplash_link
            }

        except Exception as e:
            logger.error(f"Error downloading Unsplash photo: {e}")
            raise 