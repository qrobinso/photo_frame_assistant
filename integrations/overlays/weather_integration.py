import requests
import json
import os
from datetime import datetime, timedelta
import logging

class WeatherIntegration:
    def __init__(self, config_path):
        self.config_path = os.path.abspath(config_path)  # Convert to absolute path
        self.settings = self.load_settings()
        self.last_update = None
        self.cached_weather = None
        logging.info(f"WeatherIntegration initialized with config path: {self.config_path}")
        
    def load_settings(self):
        """Load settings from config file."""
        default_settings = {
            'enabled': False,
            'zipcode': '',
            'api_key': '',
            'units': 'F',
            'update_interval': 6,  # hours
            'style': {
                'format': '{temp}° {units}',
                'position': 'top-left',
                'font_family': 'BebasNeue-Regular.ttf',  # Default font
                'font_size': '5%',
                'margin': '5%',
                'color': 'white',
                'background': {
                    'enabled': True,
                    'color': '#ffffff',
                    'opacity': 30
                }
            }
        }
        
        try:
            if os.path.exists(self.config_path):
                logging.info(f"Loading weather settings from: {self.config_path}")
                with open(self.config_path, 'r') as f:
                    saved_settings = json.load(f)
                    # Deep merge the saved settings with defaults
                    return self._deep_merge(default_settings, saved_settings)
            else:
                logging.warning(f"Config file not found at {self.config_path}, creating with defaults")
                # If config file doesn't exist, create it with defaults
                os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
                with open(self.config_path, 'w') as f:
                    json.dump(default_settings, f, indent=4)
                return default_settings
        except Exception as e:
            logging.error(f"Error loading weather settings from {self.config_path}: {e}")
            return default_settings

    def _deep_merge(self, default, override):
        """Deep merge two dictionaries."""
        result = default.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get_weather(self, force_update=False):
        """Get current weather data, using cache if available."""
        # Reload settings before checking weather to ensure we have latest config
        self.settings = self.load_settings()
        
        now = datetime.now()
        
        # Check if we need to update
        if not force_update and self.cached_weather and self.last_update:
            age = now - self.last_update
            if age < timedelta(hours=self.settings.get('update_interval', 6)):
                logging.info("Using cached weather data")
                return self.cached_weather
        
        logging.info(f"Weather settings: {self.settings}")
        
        if not self.settings.get('enabled'):
            logging.error("Weather integration is not enabled")
            return None
        
        if not self.settings.get('api_key'):
            logging.error("Weather API key is not set")
            return None
        
        if not self.settings.get('zipcode'):
            logging.error("Weather zipcode is not set")
            return None
            
        try:
            logging.info("Fetching new weather data...")
            response = requests.get(
                'http://api.openweathermap.org/data/2.5/weather',
                params={
                    'zip': self.settings['zipcode'],
                    'appid': self.settings['api_key'],
                    'units': 'imperial' if self.settings['units'] == 'F' else 'metric'
                }
            )
            
            logging.info(f"Weather API response status: {response.status_code}")
            
            if response.status_code == 200:
                self.cached_weather = response.json()
                self.last_update = now
                logging.info(f"Weather data fetched successfully: {self.cached_weather}")
                return self.cached_weather
            else:
                logging.error(f"Weather API error: {response.text}")
                
        except Exception as e:
            logging.error(f"Error fetching weather: {e}", exc_info=True)
            
        return None

    def save_settings(self, settings):
        """Save settings to config file."""
        try:
            logging.info(f"Saving weather settings to: {self.config_path}")
            # Create a new settings dictionary with all fields
            new_settings = {
                'enabled': settings.get('enabled', False),
                'zipcode': settings.get('zipcode', ''),
                'api_key': settings.get('api_key', ''),
                'units': settings.get('units', 'F'),
                'update_interval': settings.get('update_interval', 6),
                'style': settings.get('style', self.settings.get('style', {}))
            }

            # Ensure style settings are complete
            default_style = {
                'position': 'top-left',
                'font_family': 'BebasNeue-Regular.ttf',
                'font_size': '5%',
                'margin': '5%',
                'color': 'white',
                'background': {
                    'enabled': True,
                    'color': '#ffffff',
                    'opacity': 30
                }
            }

            # Deep merge style settings
            new_settings['style'] = self._deep_merge(default_style, new_settings['style'])

            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            # Save to file
            with open(self.config_path, 'w') as f:
                json.dump(new_settings, f, indent=4)
            
            # Update current settings
            self.settings = new_settings
            logging.info(f"Weather settings saved successfully to {self.config_path}")
            return True
        except Exception as e:
            logging.error(f"Error saving weather settings to {self.config_path}: {e}")
            return False

    def _parse_color(self, color_str, alpha=255):
        """Parse color string to RGBA tuple."""
        if color_str.startswith('#'):
            # Convert hex to RGB
            color_str = color_str.lstrip('#')
            if len(color_str) == 3:
                color_str = ''.join([c*2 for c in color_str])
            r = int(color_str[0:2], 16)
            g = int(color_str[2:4], 16)
            b = int(color_str[4:6], 16)
            return (r, g, b, alpha)
        elif color_str in ['white', 'black']:
            # Handle common color names
            return (255, 255, 255, alpha) if color_str == 'white' else (0, 0, 0, alpha)
        else:
            # Default to white
            return (255, 255, 255, alpha)
            
    def _parse_size(self, size_str, reference_size):
        """Parse size string (e.g., '5%') to pixels."""
        if isinstance(size_str, int):
            return size_str
        if '%' in size_str:
            percentage = float(size_str.strip('%'))
            return int(reference_size * percentage / 100)
        return int(size_str) 