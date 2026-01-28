from abc import ABC, abstractmethod
import json
import os
import logging

logger = logging.getLogger(__name__)

class Integration(ABC):
    """Base class for all photo server integrations."""
    
    @abstractmethod
    def initialize(self):
        """Initialize the integration."""
        pass
        
    @abstractmethod
    def shutdown(self):
        """Clean up resources when shutting down."""
        pass
    
    @property
    @abstractmethod
    def name(self):
        """Return the name of the integration."""
        pass
        
    @property
    @abstractmethod
    def version(self):
        """Return the version of the integration."""
        pass
        
    @property
    @abstractmethod
    def description(self):
        """Return a description of the integration."""
        pass
        
    @property
    @abstractmethod
    def config_schema(self):
        """Return a JSON schema for the integration's configuration."""
        pass
    
    def is_enabled(self):
        """Check if the integration is enabled."""
        settings = self.get_settings()
        return settings.get('enabled', False)
        
    @abstractmethod
    def get_settings(self):
        """Get the current settings for this integration."""
        pass
        
    @abstractmethod
    def update_settings(self, settings):
        """Update the settings for this integration."""
        pass
        
    @abstractmethod
    def test_connection(self):
        """Test the connection to the service."""
        pass

    def load_settings_from_file(self, config_path):
        """Load settings from a config file."""
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading settings from {config_path}: {e}")
        return {}

    def save_settings_to_file(self, config_path, settings):
        """Save settings to a config file."""
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w') as f:
                json.dump(settings, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"Error saving settings to {config_path}: {e}")
            return False


class PhotoSourceIntegration(Integration):
    """Base class for integrations that provide photos."""
    
    @abstractmethod
    def search_photos(self, query=None, **kwargs):
        """Search for photos based on criteria."""
        pass
        
    @abstractmethod
    def download_photo(self, photo_data, upload_folder):
        """Download a photo and save it to the upload folder."""
        pass


class SmartHomeIntegration(Integration):
    """Base class for smart home integrations."""
    
    @abstractmethod
    def publish_state(self, frame=None):
        """Publish the current state to the smart home platform."""
        pass
        
    @abstractmethod
    def register_frame(self, frame):
        """Register a frame with the smart home platform."""
        pass
        
    @abstractmethod
    def handle_command(self, command, payload):
        """Handle a command from the smart home platform."""
        pass


class OverlayIntegration(Integration):
    """Base class for integrations that provide overlays for photos."""
    
    @abstractmethod
    def get_overlay(self, frame_id, photo_path):
        """Get an overlay for a photo."""
        pass
        
    @abstractmethod
    def get_available_overlays(self):
        """Get a list of available overlays."""
        pass 