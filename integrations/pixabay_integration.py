import os
import json
import requests
import random
from datetime import datetime
from PIL import Image
import io
import logging

logger = logging.getLogger(__name__)

class PixabayIntegration:
    # Valid categories from Pixabay API
    CATEGORIES = [
        'backgrounds', 'fashion', 'nature', 'science', 'education', 'feelings',
        'health', 'people', 'religion', 'places', 'animals', 'industry',
        'computer', 'food', 'sports', 'transportation', 'travel', 'buildings',
        'business', 'music'
    ]

    # Valid colors from Pixabay API
    COLORS = [
        'grayscale', 'transparent', 'red', 'orange', 'yellow', 'green',
        'turquoise', 'blue', 'lilac', 'pink', 'white', 'gray', 'black', 'brown'
    ]

    def __init__(self, config_path):
        self.config_path = config_path
        self.settings = self.load_settings()
        self.base_url = "https://pixabay.com/api/"

    def load_settings(self):
        """Load Pixabay settings from config file."""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading Pixabay settings: {e}")
        return {'api_key': None}

    def save_settings(self, settings):
        """Save Pixabay settings to config file."""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(settings, f, indent=4)
            self.settings = settings
            return True
        except Exception as e:
            logger.error(f"Error saving Pixabay settings: {e}")
            return False

    def get_random_photos(self, query=None, category=None, colors=None, 
                         orientation=None, editors_choice=False, 
                         image_type='photo', safesearch=True, count=1):
        """Get random photos from Pixabay with specified parameters."""
        if not self.settings.get('api_key'):
            raise ValueError("Pixabay API key not configured")

        # Set a higher per_page value to get more options for randomization
        per_page = min(200, max(count * 3, 20))  # At least 20, at most 200, ideally 3x the requested count
        
        # Randomly choose between popular and latest ordering
        order_options = ['popular', 'latest']
        chosen_order = random.choice(order_options)
        
        params = {
            'key': self.settings['api_key'],
            'per_page': per_page,
            'safesearch': 'true' if safesearch else 'false',
            'image_type': image_type,
            'order': chosen_order
        }

        # Add optional parameters if provided
        if query:
            params['q'] = query
        if category and category in self.CATEGORIES:
            params['category'] = category
        if colors and colors in self.COLORS:
            params['colors'] = colors
        # Only add orientation if it's a valid value
        if orientation and orientation in ['horizontal', 'vertical']:
            params['orientation'] = orientation
        elif orientation:
            logger.warning(f"Invalid orientation value: {orientation}, not including in request")
        if editors_choice:
            params['editors_choice'] = 'true'

        try:
            logger.info(f"Making Pixabay API request with params: {params}")
            
            # First make a request to get total hits to determine max page number
            initial_response = requests.get(self.base_url, params=params)
            
            # Log the actual URL being requested (for debugging)
            logger.info(f"Pixabay API URL: {initial_response.url}")
            
            initial_response.raise_for_status()
            data = initial_response.json()
            
            total_hits = data.get('totalHits', 0)
            
            if total_hits == 0:
                logger.warning("No photos found for the given criteria")
                return []
            
            # Calculate max page number (Pixabay limits to 500 results max)
            max_results = min(total_hits, 500)
            max_page = (max_results + per_page - 1) // per_page
            
            # If we have multiple pages, randomly select one
            if max_page > 1:
                # Choose a random page
                random_page = random.randint(1, max_page)
                params['page'] = random_page
                
                # Make the actual request with the random page
                response = requests.get(self.base_url, params=params)
                
                # Log the actual URL being requested (for debugging)
                logger.info(f"Pixabay API URL (page {random_page}): {response.url}")
                
                response.raise_for_status()
                data = response.json()
            
            # If we have more results than needed, randomly select 'count' items
            hits = data.get('hits', [])
            if len(hits) > count:
                selected_hits = random.sample(hits, count)
                logger.info(f"Selected {count} random photos from {len(hits)} results")
                return selected_hits
            else:
                logger.info(f"Returning all {len(hits)} photos found")
                return hits

        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting photos from Pixabay: {e}")
            if hasattr(e, 'response') and e.response:
                logger.error(f"Response status code: {e.response.status_code}")
                logger.error(f"Response content: {getattr(e.response, 'content', 'No content')}")
                if e.response.status_code == 400:
                    logger.error("Bad request - check API parameters")
                    # Log each parameter to help diagnose the issue
                    for key, value in params.items():
                        logger.error(f"Parameter {key}: {value}")
            raise

    def download_photo(self, photo_data, upload_folder):
        """Download a photo from Pixabay and save it locally."""
        try:
            # Log the available keys in photo_data for debugging
            logger.info(f"Available keys in photo_data: {list(photo_data.keys())}")
            
            # Try to get the best available image URL
            # Check for all possible URL keys in order of preference
            possible_url_keys = [
                'largeImageURL', 
                'fullHDURL', 
                'webformatURL',
                'imageURL',
                'previewURL',
                'original_data'  # This might be a nested structure
            ]
            
            photo_url = None
            
            # First try direct keys
            for key in possible_url_keys:
                if key in photo_data and photo_data[key]:
                    if key == 'original_data' and isinstance(photo_data[key], dict):
                        # If it's original_data, look for URLs inside it
                        original_data = photo_data[key]
                        for url_key in possible_url_keys[:-1]:  # Exclude 'original_data' to avoid recursion
                            if url_key in original_data and original_data[url_key]:
                                photo_url = original_data[url_key]
                                logger.info(f"Found URL in original_data.{url_key}: {photo_url}")
                                break
                    else:
                        photo_url = photo_data[key]
                        logger.info(f"Found URL in {key}: {photo_url}")
                        break
            
            # If still no URL found, check if there's any key ending with 'URL'
            if not photo_url:
                for key in photo_data.keys():
                    if key.endswith('URL') and photo_data[key] and isinstance(photo_data[key], str):
                        photo_url = photo_data[key]
                        logger.info(f"Found URL in alternative key {key}: {photo_url}")
                        break
            
            if not photo_url:
                # Log the entire photo_data for debugging
                logger.error(f"No suitable image URL found. Photo data: {photo_data}")
                raise ValueError("No suitable image URL found")

            # Download the photo
            logger.info(f"Downloading photo from URL: {photo_url}")
            response = requests.get(photo_url)
            response.raise_for_status()

            # Create a unique filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"pixabay_{timestamp}_{photo_data['id']}.jpg"
            filepath = os.path.join(upload_folder, filename)

            # Save the photo
            with open(filepath, 'wb') as f:
                f.write(response.content)

            # Prepare metadata
            photographer = photo_data.get('user', 'Unknown')
            pixabay_link = photo_data.get('pageURL', '')
            heading = f"Photo by {photographer} on Pixabay"

            return {
                'filename': filename,
                'heading': heading,
                'photographer': photographer,
                'pixabay_link': pixabay_link
            }

        except Exception as e:
            logger.error(f"Error downloading Pixabay photo: {e}")
            raise 