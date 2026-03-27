import qrcode
from PIL import Image, ImageDraw, ImageFont
import socket
import os
import logging
import json

class QRCodeIntegration:
    def __init__(self, config_path=None):
        """Initialize QR code integration."""
        self.config_path = config_path or os.path.join(
            os.path.dirname(__file__), 
            'qrcode_config.json'
        )
        self.settings = self.load_settings()
        self.font_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'static', 'fonts', 'BebasNeue-Regular.ttf'
        )

    def load_settings(self):
        """Load QR code settings from config file."""
        default_settings = {
            'enabled': True,
            'port': 5000,
            'custom_url': None,
            'size': 'medium',  # 'small', 'medium', 'large'
            'position': 'bottom-right',  # same options as metadata overlay
            'link_type': 'frame_playlist'  # 'frame_playlist' or 'server_home'
        }
        
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    settings = json.load(f)
                    # Ensure all default settings exist
                    for key, value in default_settings.items():
                        if key not in settings:
                            settings[key] = value
                    return settings
        except Exception as e:
            logging.error(f"Error loading QR code settings: {e}")
        
        return default_settings

    def save_settings(self, settings):
        """Save QR code settings to config file."""
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            # Validate required fields
            required_fields = ['size', 'position', 'link_type']
            for field in required_fields:
                if field not in settings:
                    logging.error(f"Missing required field: {field}")
                    return False

            # Write settings to file
            with open(self.config_path, 'w') as f:
                json.dump(settings, f, indent=4)
            
            # Update instance settings
            self.settings = settings
            return True
            
        except Exception as e:
            logging.error(f"Error saving QR code settings: {e}")
            return False

    def get_server_url(self, frame_id=None):
        """Get the server URL for QR code.
        
        Args:
            frame_id (str, optional): The ID of the frame to generate URL for.
        """
        try:
            if self.settings.get('custom_url'):
                base_url = self.settings['custom_url']
            else:
                # Get server IP address
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip_address = s.getsockname()[0]
                s.close()
                
                port = self.settings.get('port', 5000)
                base_url = f"http://{ip_address}:{port}"
            
            # If frame_id is provided, append the frames/playlist path
            if frame_id:
                return f"{base_url}/frames/{frame_id}/playlist"
            return base_url
            
        except Exception as e:
            logging.error(f"Error getting server URL: {e}")
            return None

    def generate_qr_code(self, size, frame_id=None):
        """Generate QR code image of specified size.
        
        Args:
            size (int): The size of the QR code image in pixels
            frame_id (str, optional): The ID of the frame to generate QR code for
        """
        try:
            # Determine URL based on link_type setting
            if self.settings['link_type'] == 'server_home':
                url = self.get_server_url()
            else:  # frame_playlist
                url = self.get_server_url(frame_id)
            
            if not url:
                return None
            
            # Adjust size based on size setting
            size_multipliers = {
                'small': 0.2,    # 10% of input size
                'medium': 0.4,  # 15% of input size
                'large': 1.0     # 20% of input size
            }
            multiplier = size_multipliers.get(self.settings['size'], 0.15)  # default to medium
            adjusted_size = int(size * multiplier)
            
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)
            
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_img = qr_img.resize((adjusted_size, adjusted_size), Image.Resampling.LANCZOS)
            
            return qr_img
        except Exception as e:
            logging.error(f"Error generating QR code: {e}")
            return None 