from PIL import Image, ImageDraw, ImageFont
import os
import json
import requests
from io import BytesIO
import logging
import traceback
from abc import ABC, abstractmethod
from photo_processing import PhotoProcessor
from .qrcode_integration import QRCodeIntegration
from datetime import datetime

# Configure more detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('overlay_manager')

class BaseOverlay(ABC):
    """Abstract base class for all overlays."""
    @abstractmethod
    def apply(self, img: Image, draw: ImageDraw, image_path: str, frame=None, photo=None) -> Image:
        """Apply this overlay to the image."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the overlay."""
        pass

    @property
    def enabled(self) -> bool:
        """Check if this overlay is enabled."""
        return True

class WeatherOverlay(BaseOverlay):
    def __init__(self, weather_integration):
        self.weather = weather_integration
        # Use static/fonts directory
        self.font_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                                    'static', 'fonts')
        logging.info(f"Weather overlay initialized with font path: {self.font_path}")
        
    @property
    def name(self) -> str:
        return "weather"

    @property
    def enabled(self) -> bool:
        enabled = self.weather.settings.get('enabled', False)
        logger.debug(f"Weather overlay enabled: {enabled}")
        return enabled
        
    def apply(self, img: Image, draw: ImageDraw, image_path: str, frame=None, photo=None) -> Image:
        """Add weather overlay to image."""
        logger.debug(f"Applying weather overlay to image: {image_path}")
        weather_data = self.weather.get_weather()
        if not weather_data:
            logger.warning("No weather data available, skipping overlay")
            return img
            
        try:
            # Get style settings
            style = self.weather.settings.get('style', {})
            format_str = style.get('format', '{temp}° {units}')
            
            # Parse weather data
            replacements = {
                'temp': int(weather_data['main']['temp']),
                'temp_min': int(weather_data['main']['temp_min']),
                'temp_max': int(weather_data['main']['temp_max']),
                'feels_like': int(weather_data['main']['feels_like']),
                'humidity': weather_data['main']['humidity'],
                'pressure': weather_data['main']['pressure'],
                'description': weather_data['weather'][0]['description'].title(),
                'wind_speed': weather_data['wind']['speed'],
                'wind_deg': weather_data['wind']['deg'],
                'clouds': weather_data['clouds']['all'],
                'visibility': weather_data['visibility'],
                'sunrise': datetime.fromtimestamp(weather_data['sys']['sunrise']).strftime('%H:%M'),
                'sunset': datetime.fromtimestamp(weather_data['sys']['sunset']).strftime('%H:%M'),
                'city': weather_data['name'],
                'units': 'F' if self.weather.settings['units'] == 'F' else 'C'
            }
            
            # Format the text
            text = format_str
            for key, value in replacements.items():
                text = text.replace('{' + key + '}', str(value))
            
            # Load font
            font_size = self.weather._parse_size(style.get('font_size', '5%'), img.height)
            font_family = style.get('font_family', 'BebasNeue-Regular.ttf')
            if not font_family.endswith('.ttf'):
                font_family += '.ttf'
            font_path = os.path.join(self.font_path, font_family)
            
            try:
                font = ImageFont.truetype(font_path, font_size)
                logger.debug(f"Successfully loaded font: {font_path}")
            except Exception as e:
                logger.error(f"Failed to load font {font_path}: {e}")
                font = ImageFont.load_default()
                logger.debug("Using default font as fallback")
            
            # Calculate text size
            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            # Calculate position
            position = style.get('position', 'top-left')
            margin = self.weather._parse_size(style.get('margin', '5%'), min(img.width, img.height))
            pos = self._calculate_position(position, img.size, (text_width, text_height), margin)
            
            # Apply background if enabled
            if style.get('background', {}).get('enabled', False):
                bg_width = text_width + 40
                bg_height = text_height + 20
                bg_color = style['background'].get('color', '#ffffff')
                bg_opacity = int(style['background'].get('opacity', 30) * 255 / 100)
                bg_color_rgba = self.weather._parse_color(bg_color, bg_opacity)
                
                bg_pos = self._calculate_position(position, img.size, (bg_width, bg_height), margin)
                bg = Image.new('RGBA', (bg_width, bg_height), bg_color_rgba)
                img.paste(bg, bg_pos, bg)
                
                # Adjust text position relative to background
                text_pos = (
                    bg_pos[0] + 20,
                    bg_pos[1] + 10
                )
            else:
                text_pos = pos
            
            # Draw text
            draw.text(text_pos, text, font=font, fill=style.get('color', 'white'))
            
            return img
            
        except Exception as e:
            logger.error(f"Error adding weather overlay: {e}")
            logger.error(traceback.format_exc())
            return img
            
    def _calculate_position(self, position_str, img_size, element_size, margin):
        """Calculate position coordinates based on position string.
        
        Args:
            position_str: Position identifier (e.g., 'top-left', 'bottom-right')
            img_size: Tuple of (width, height) for the image
            element_size: Tuple of (width, height) for the element to position
            margin: Margin in pixels
            
        Returns:
            Tuple of (x, y) coordinates
        """
        width, height = img_size
        elem_width, elem_height = element_size
        
        positions = {
            'top-left': (margin, margin),
            'top-right': (width - elem_width - margin, margin),
            'top-center': ((width - elem_width) // 2, margin),
            'bottom-left': (margin, height - elem_height - margin),
            'bottom-right': (width - elem_width - margin, height - elem_height - margin),
            'bottom-center': ((width - elem_width) // 2, height - elem_height - margin),
            'center': ((width - elem_width) // 2, (height - elem_height) // 2)
        }
        
        # Get the position or default to top-left
        pos = positions.get(position_str, positions['top-left'])
        logger.debug(f"Calculated position {position_str}: {pos} for element size {element_size} with margin {margin}")
        return pos

class MetadataOverlay(BaseOverlay):
    def __init__(self, metadata_integration):
        self.metadata = metadata_integration
        self.font_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                    'static', 'fonts')
        logger.info(f"Metadata overlay initialized with font path: {self.font_path}")
        
    @property
    def name(self) -> str:
        return "metadata"

    @property
    def enabled(self) -> bool:
        return True
        
    def apply(self, img: Image, draw: ImageDraw, image_path: str, frame=None, photo=None) -> Image:
        """Add metadata overlay to image."""
        logger.debug(f"Applying metadata overlay to image: {image_path}")
        try:
            # Get metadata from photo object
            metadata = self.metadata.parse_metadata(photo)
            if not metadata:
                logger.warning("No metadata found for image")
                return img

            # Get styles configuration
            styles = self.metadata.styles
            logger.debug(f"Using styles: {styles}")
            
            # Calculate background if enabled
            if styles['background']['enabled']:
                # Create semi-transparent overlay
                overlay = Image.new('RGBA', img.size, 
                                  self.metadata._parse_color(styles['background']['color'], 
                                  int(255 * float(styles['background']['opacity']) / 100)))
                img = Image.alpha_composite(img.convert('RGBA'), overlay)
                draw = ImageDraw.Draw(img)
                logger.debug("Applied background overlay")

            # Fixed 30px spacing between fields
            spacing = 30

            # Get global padding from styles (in pixels)
            global_padding = styles.get('global_padding', 0)
            logger.debug(f"Global padding: {global_padding}px")

            # First pass: calculate total height of all enabled fields
            total_height = 0
            field_heights = {}
            enabled_fields = []
            
            for field_name, field_config in styles['fields'].items():
                if not field_config['enabled']:
                    logger.debug(f"Field '{field_name}' is disabled, skipping")
                    continue

                # Format the text using metadata
                text = self.metadata.format_metadata_text(metadata, field_config)
                if not text:
                    logger.debug(f"No text for field '{field_name}', skipping")
                    continue

                try:
                    # Load font with calculated size
                    font_size = self.metadata._parse_size(field_config['font_size'], img.height)
                    font_file = field_config['font_family'] if field_config['font_family'].endswith('.ttf') else f"{field_config['font_family']}.ttf"
                    font_path = os.path.join(self.font_path, font_file)
                    logger.debug(f"Loading font: {font_path} at size {font_size}")
                    font = ImageFont.truetype(font_path, font_size)
                except Exception as e:
                    logger.error(f"Font error for {field_name}: {e}")
                    font = ImageFont.load_default()
                    logger.debug(f"Using default font for field '{field_name}'")

                # Get text size
                text_bbox = draw.textbbox((0, 0), text, font=font)
                text_height = text_bbox[3] - text_bbox[1]
                
                field_heights[field_name] = {
                    'text': text,
                    'font': font,
                    'height': text_height,
                    'bbox': text_bbox,
                    'config': field_config
                }
                enabled_fields.append(field_name)
                total_height += text_height
                logger.debug(f"Field '{field_name}' calculated size: {text_bbox}")

            # Add spacing between fields to total height
            if len(enabled_fields) > 1:
                total_height += spacing * (len(enabled_fields) - 1)
                
            logger.debug(f"Total content height: {total_height}px for {len(enabled_fields)} fields")

            # Second pass: draw fields with proper vertical positioning
            current_y_offset = 0
            
            for field_name in enabled_fields:
                field_data = field_heights[field_name]
                text = field_data['text']
                font = field_data['font']
                text_bbox = field_data['bbox']
                field_config = field_data['config']
                
                text_size = (text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1])

                # Calculate base position with global padding
                x, base_y = self.metadata._parse_position(
                    field_config['position'],
                    img.size,
                    text_size,
                    field_config['margin']
                )

                # Adjust y position based on vertical alignment and current offset
                if 'top' in field_config['position']:
                    y = base_y + current_y_offset
                elif 'bottom' in field_config['position']:
                    y = base_y - (total_height - field_data['height'] - current_y_offset)
                else:  # center
                    y = base_y - (total_height // 2) + current_y_offset

                logger.debug(f"Drawing field '{field_name}' at position ({x}, {y})")
                
                # Draw text
                draw.text((x, y), text, font=font, fill=field_config['color'])

                # Update offset for next field
                current_y_offset += field_data['height'] + spacing

            logger.info("Metadata overlay successfully applied")
            return img
            
        except Exception as e:
            logger.error(f"Error adding metadata overlay: {e}")
            logger.error(traceback.format_exc())
            return img

    def _parse_position(self, position_str, img_size, text_size, margin_str):
        """Parse position string and return x,y coordinates."""
        # Convert margin string (e.g., '10%') to pixels
        margin = int(float(margin_str.strip('%')) * min(img_size) / 100)
        
        # Get global padding from styles (in pixels)
        padding = int(self.metadata.styles.get('global_padding', 0))
        
        width, height = img_size
        text_width, text_height = text_size
        
        # Add padding to all positions
        positions = {
            'top-left': (padding + margin, padding + margin),
            'top-right': (width - text_width - margin - padding, padding + margin),
            'top-center': ((width - text_width) // 2, padding + margin),
            'bottom-left': (padding + margin, height - text_height - margin - padding),
            'bottom-right': (width - text_width - margin - padding, height - text_height - margin - padding),
            'bottom-center': ((width - text_width) // 2, height - text_height - margin - padding),
            'center': ((width - text_width) // 2, (height - text_height) // 2)
        }
        
        # Get the base position
        base_pos = positions.get(position_str, positions['bottom-left'])
        
        # Log the position calculation for debugging
        logger.debug(f"Position calculation for {position_str}:")
        logger.debug(f"  Image size: {width}x{height}")
        logger.debug(f"  Text size: {text_width}x{text_height}")
        logger.debug(f"  Margin: {margin}px")
        logger.debug(f"  Padding: {padding}px")
        logger.debug(f"  Final position: {base_pos}")
        
        return base_pos

    @staticmethod
    def get_available_fonts():
        """Get list of available TTF fonts in the static/fonts directory."""
        try:
            font_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                  'static', 'fonts')
            # Get all .ttf files in the fonts directory
            fonts = [f for f in os.listdir(font_dir) if f.lower().endswith('.ttf')]
            # Ensure BebasNeue-Regular.ttf is included and first in the list
            if 'BebasNeue-Regular.ttf' not in fonts:
                fonts.insert(0, 'BebasNeue-Regular.ttf')
            else:
                # Move BebasNeue to the front if it exists
                fonts.remove('BebasNeue-Regular.ttf')
                fonts.insert(0, 'BebasNeue-Regular.ttf')
            logger.debug(f"Available fonts: {fonts}")
            return fonts  # Return all fonts including Bebas
        except Exception as e:
            logger.error(f"Error getting available fonts: {e}")
            return ['BebasNeue-Regular.ttf']  # Return default font if error occurs

class QRCodeOverlay(BaseOverlay):
    def __init__(self, qrcode_integration):
        self.qrcode = qrcode_integration
        logger.info("QR Code overlay initialized")
        
    @property
    def name(self) -> str:
        return "qrcode"

    @property
    def enabled(self) -> bool:
        enabled = self.qrcode.settings.get('enabled', True)
        logger.debug(f"QR Code overlay enabled: {enabled}")
        return enabled
        
    def apply(self, img: Image, draw: ImageDraw, image_path: str, frame=None, photo=None) -> Image:
        """Add QR code overlay to image."""
        logger.debug(f"Applying QR code overlay to image: {image_path}")
        try:
            # Get frame ID if available
            frame_id = frame.id if frame else None
            logger.debug(f"Using frame_id: {frame_id}")
            
            # Calculate QR code size based on image height
            qr_size = int(img.height * 0.2)  # Base size for QR code
            margin = int(img.height * 0.05)   # Margin will be 5% of image height
            logger.debug(f"QR code parameters: size={qr_size}, margin={margin}")
            
            # Generate QR code with frame_id
            qr_img = self.qrcode.generate_qr_code(qr_size, frame_id)
            if not qr_img:
                logger.warning("Failed to generate QR code")
                return img
            
            # Get actual QR code size after generation
            qr_size = qr_img.size[0]
            
            # Calculate position based on settings
            positions = {
                'top-left': (margin, margin),
                'top-right': (img.width - qr_size - margin, margin),
                'top-center': ((img.width - qr_size) // 2, margin),
                'bottom-left': (margin, img.height - qr_size - margin),
                'bottom-right': (img.width - qr_size - margin, img.height - qr_size - margin),
                'bottom-center': ((img.width - qr_size) // 2, img.height - qr_size - margin),
                'center': ((img.width - qr_size) // 2, (img.height - qr_size) // 2)
            }
            
            position = self.qrcode.settings.get('position', 'bottom-right')
            qr_pos = positions.get(position, positions['bottom-right'])
            logger.debug(f"QR code position: {position} at coordinates {qr_pos}")
            
            # Create white background for QR code
            bg_size = (qr_size + 20, qr_size + 20)  # Add padding
            bg = Image.new('RGBA', bg_size, (255, 255, 255, 230))  # Semi-transparent white
            
            # Calculate background position
            bg_pos = (qr_pos[0] - 10, qr_pos[1] - 10)  # Adjust for padding
            
            # Paste background and QR code
            img.paste(bg, bg_pos, bg)
            img.paste(qr_img, qr_pos)
            logger.info("QR code successfully applied")
            
            return img
            
        except Exception as e:
            logger.error(f"Error adding QR code overlay: {e}")
            logger.error(traceback.format_exc())
            return img

class OverlayManager:
    """Manages the application of multiple overlays to images."""
    
    def __init__(self, weather_integration, metadata_integration):
        """Initialize with available overlays."""
        qrcode_config_path = os.path.join(os.path.dirname(__file__), 'qrcode_config.json')
        qrcode_integration = QRCodeIntegration(qrcode_config_path)
        
        self.overlays = {
            "weather": WeatherOverlay(weather_integration),
            "metadata": MetadataOverlay(metadata_integration),
            "qrcode": QRCodeOverlay(qrcode_integration)
        }
        logger.info(f"OverlayManager initialized with overlays: {', '.join(self.overlays.keys())}")
        
    def apply_overlays(self, image_path, preferences, frame=None, photo=None):
        """Apply overlays to an image.
        
        Args:
            image_path (str): Path to the image file
            preferences (str or dict): Overlay preferences (JSON string or dictionary)
            frame (PhotoFrame, optional): Frame object containing frame settings
            photo (Photo, optional): Photo database object containing metadata
        """
        try:
            logger.info(f"Starting overlay application for: {image_path}")
            
            # Validate the image path exists
            if not os.path.exists(image_path):
                logger.error(f"Image path does not exist: {image_path}")
                return None
                
            # Get orientation from frame if provided, otherwise default to portrait
            desired_orientation = frame.orientation if frame else 'portrait'
            logger.info(f"Using frame orientation: {desired_orientation}")
            
            # Convert preferences to dictionary if it's a JSON string
            if isinstance(preferences, str):
                try:
                    preferences = json.loads(preferences)
                    logger.debug(f"Parsed JSON preferences: {preferences}")
                except json.JSONDecodeError:
                    logger.error("Invalid JSON string for preferences")
                    preferences = {}
            elif preferences is None:
                preferences = {}
                
            logger.info(f"Applying overlays with preferences: {preferences}")
            
            # Open and prepare the image
            try:
                img = Image.open(image_path)
                logger.debug(f"Opened image: {image_path}, size: {img.size}, mode: {img.mode}")
                
                # Convert P mode (palette) to RGB immediately after opening
                if img.mode == 'P':
                    img = img.convert('RGB')
                    logger.debug("Converted Palette (P) mode image to RGB")
            except Exception as e:
                logger.error(f"Failed to open image {image_path}: {e}")
                return None
            
            # Extract EXIF data before any processing to preserve it
            exif_data = None
            try:
                if hasattr(img, '_getexif') and img._getexif():
                    exif_data = img.info.get('exif')
                    logger.info("Extracted EXIF data from original image")
            except Exception as e:
                logger.error(f"Error extracting EXIF data: {e}")
            
            # Ensure correct orientation before applying overlays
            try:
                photo_processor = PhotoProcessor()
                img = photo_processor.ensure_orientation(img, desired_orientation)
                logger.debug(f"Orientation adjusted to: {desired_orientation}")
            except Exception as e:
                logger.error(f"Error ensuring orientation: {e}")
            
            # Create a fresh copy and convert to RGBA
            img = img.copy()
            img.load()  # Ensure the image data is loaded
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
                logger.debug("Converted image to RGBA mode")
            
            # Apply each enabled overlay
            for overlay_name, overlay in self.overlays.items():
                logger.info(f"Checking overlay: {overlay_name}")
                
                # Debug the overlay state
                logger.debug(f"Overlay {overlay_name}: preference={preferences.get(overlay_name, False)}, enabled={overlay.enabled}")
                
                if preferences.get(overlay_name, False) and overlay.enabled:
                    try:
                        logger.info(f"Applying {overlay_name} overlay")
                        # Create a fresh draw object for each overlay
                        draw = ImageDraw.Draw(img)
                        new_img = overlay.apply(img, draw, image_path, frame, photo)
                        
                        # Ensure we have a valid image after overlay
                        if new_img is not None:
                            img = new_img.copy()
                            img.load()  # Ensure the image data is loaded
                            
                            # Handle various image modes that might be problematic when saving
                            if img.mode == 'P':
                                img = img.convert('RGB')
                                logger.debug(f"Converted result from P mode to RGB after {overlay_name} overlay")
                            elif img.mode != 'RGBA' and img.mode != 'RGB':
                                img = img.convert('RGB')
                                logger.debug(f"Converted result from {img.mode} mode to RGB after {overlay_name} overlay")
                            
                            logger.debug(f"Successfully applied {overlay_name} overlay")
                        else:
                            logger.warning(f"{overlay_name} overlay returned None")
                    except Exception as e:
                        logger.error(f"Error applying {overlay_name} overlay: {e}")
                        logger.error(traceback.format_exc())
                        continue
                else:
                    logger.info(f"Skipping {overlay_name} overlay (enabled: {overlay.enabled}, preference: {preferences.get(overlay_name, False)})")
            
            # Ensure image is in RGB mode for JPEG compatibility
            try:
                if img.mode != 'RGB':
                    # Handle transparency if exists
                    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                        # Create white background for transparent images
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        background.paste(img, mask=img.split()[-1])  # Use alpha channel as mask
                        img = background
                        logger.debug(f"Converted {img.mode} with transparency to RGB")
                    else:
                        # Direct conversion for other modes
                        img = img.convert('RGB')
                        logger.debug(f"Converted {img.mode} to RGB")
            except Exception as conversion_error:
                logger.error(f"Error during final mode conversion: {conversion_error}")
                # Fallback to RGB conversion
                img = img.convert('RGB')
            
            # If we have EXIF data, reattach it
            if exif_data:
                # Create a new image with the original EXIF data
                img_with_exif = img.copy()
                img_with_exif.info['exif'] = exif_data
                logger.info("Reattached EXIF data to modified image")
                return img_with_exif
            
            logger.info("Overlay application completed successfully")
            return img
            
        except Exception as e:
            logger.error(f"Error applying overlays: {e}")
            logger.error(traceback.format_exc())
            return None

    def get_available_overlays(self):
        """Return a list of available overlays and their enabled status."""
        overlays = {
            name: {
                'enabled': overlay.enabled,
                'name': overlay.name
            }
            for name, overlay in self.overlays.items()
        }
        logger.debug(f"Available overlays: {overlays}")
        return overlays 