from PIL import Image, ImageDraw, ImageFont
import os
import json
import logging
from PIL.ExifTags import TAGS, GPSTAGS
from datetime import datetime

class MetadataStackManager:
    def __init__(self, styles):
        self.styles = styles
        self.stack_spacing = self._parse_size(styles.get("stack_spacing", "2%"), 100)  # Default 2% spacing
        self.max_text_width = styles.get("max_text_width", "80%")
        
    def _parse_size(self, size_str, base_size):
        """Convert percentage or pixel size to actual pixels."""
        try:
            if isinstance(size_str, (int, float)):
                return int(size_str)
            if size_str.endswith('%'):
                percentage = float(size_str.rstrip('%'))
                return int(base_size * (percentage / 100))
            return int(size_str)
        except:
            return base_size

    def calculate_positions(self, image_size, enabled_fields):
        """Calculate positions for each enabled field while maintaining spacing."""
        positions = {}
        field_groups = {
            'top-left': [], 'top-center': [], 'top-right': [],
            'bottom-left': [], 'bottom-center': [], 'bottom-right': []
        }
        
        # Group fields by position
        for field_name, field_config in enabled_fields.items():
            position = field_config.get('position', 'bottom-left')
            if position in field_groups:
                field_groups[position].append((field_name, field_config))
        
        # Sort fields within each group by stack_order
        for position in field_groups:
            field_groups[position].sort(key=lambda x: x[1].get('stack_order', 0))
        
        # Calculate positions for each group
        for position, fields in field_groups.items():
            if not fields:
                continue
                
            current_offset = 0
            base_margin = self._parse_size(fields[0][1].get('margin', '10%'), min(image_size))
            
            for field_name, field_config in fields:
                font_size = self._parse_size(field_config.get('font_size', '4%'), min(image_size))
                positions[field_name] = {
                    'position': position,
                    'offset': current_offset,
                    'font_size': font_size,
                    'margin': base_margin
                }
                current_offset += font_size + self.stack_spacing
        
        return positions

    def truncate_text(self, text, max_width, font, draw):
        """Truncate text if it exceeds max width."""
        if not text:
            return text
            
        ellipsis = "..."
        text_width, _ = draw.textsize(text, font=font)
        
        if text_width <= max_width:
            return text
            
        # Binary search for the right truncation point
        left, right = 0, len(text)
        while left < right:
            mid = (left + right + 1) // 2
            truncated = text[:mid] + ellipsis
            width, _ = draw.textsize(truncated, font=font)
            
            if width <= max_width:
                left = mid
            else:
                right = mid - 1
                
        return text[:left] + ellipsis if left > 0 else ellipsis 

class MetadataIntegration:
    def __init__(self, config_path):
        self.config_path = config_path
        self.styles_path = config_path
        self.styles = self._get_default_styles()
        
        # Load or create styles file
        try:
            if os.path.exists(self.styles_path):
                with open(self.styles_path, 'r') as f:
                    self.styles = json.load(f)
            else:
                self.save_styles(self.styles)
        except Exception as e:
            logging.error(f"Error loading styles file: {e}. Using defaults.")
            self.save_styles(self.styles)

    def _get_default_styles(self):
        """Return default metadata styling configuration."""
        return {
            "fields": {
                "heading": {
                    "enabled": True,
                    "font_family": "BebasNeue-Regular",
                    "font_size": "8%",
                    "color": "#FFFFFF",
                    "position": "top-center",
                    "margin": "10%",
                    "format": "{heading}",
                    "stack_order": 0
                },
                "date": {
                    "enabled": True,
                    "font_family": "BebasNeue-Regular",
                    "font_size": "6%",
                    "color": "#FFFFFF",
                    "position": "bottom-left",
                    "margin": "10%",
                    "format": "{date}",
                    "stack_order": 1
                },
                "location": {
                    "enabled": True,
                    "font_family": "BebasNeue-Regular",
                    "font_size": "5%",
                    "color": "#FFFFFF",
                    "position": "bottom-left",
                    "margin": "10%",
                    "format": "{location}",
                    "stack_order": 2
                }
            },
            "stack_spacing": "2%",
            "max_text_width": "80%",
            "background": {
                "enabled": False,
                "color": "#000000",
                "opacity": "50"
            }
        }

    def parse_metadata(self, photo):
        """Extract metadata from photo database object."""
        if not photo:
            return None

        try:
            metadata = {}
            
            # Add heading if available
            if photo.heading:
                metadata['heading'] = photo.heading

            # Parse EXIF metadata if available
            if photo.exif_metadata:
                try:
                    # Check if exif_metadata is already a dict
                    exif_data = photo.exif_metadata if isinstance(photo.exif_metadata, dict) else json.loads(photo.exif_metadata)
                    
                    # Extract date/time
                    if 'DateTime' in exif_data:
                        date = datetime.strptime(exif_data['DateTime'], '%Y:%m:%d %H:%M:%S')
                        metadata['date'] = date.strftime('%B %d, %Y')
                    
                    # Use pre-formatted location if available
                    if 'formatted_location' in exif_data:
                        metadata['location'] = exif_data['formatted_location']
                    # Otherwise try to use GPS coordinates
                    elif 'GPSInfo' in exif_data:
                        location = self._format_gps_location(exif_data['GPSInfo'])
                        if location:
                            metadata['location'] = location
                            
                except Exception as e:
                    logging.error(f"Error parsing EXIF metadata: {e}")

            return metadata if metadata else None

        except Exception as e:
            logging.error(f"Error extracting metadata: {e}")
            return None

    def _format_gps_location(self, gps_info):
        """Format GPS coordinates into a readable string."""
        try:
            if isinstance(gps_info, str):
                gps_info = json.loads(gps_info.replace("'", '"'))

            lat_data = gps_info.get('GPSLatitude')
            lat_ref = gps_info.get('GPSLatitudeRef', 'N')
            lon_data = gps_info.get('GPSLongitude')
            lon_ref = gps_info.get('GPSLongitudeRef', 'E')

            if not (lat_data and lon_data):
                return None

            # Convert coordinates to decimal degrees
            lat = self._convert_to_degrees(lat_data)
            lon = self._convert_to_degrees(lon_data)

            if lat == 0.0 and lon == 0.0:
                return None

            if lat_ref == 'S':
                lat = -lat
            if lon_ref == 'W':
                lon = -lon

            return f"{abs(lat):.4f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.4f}°{'E' if lon >= 0 else 'W'}"

        except Exception as e:
            logging.error(f"Error formatting GPS location: {e}")
            return None

    def _convert_to_degrees(self, value):
        """Convert GPS coordinates to degrees."""
        try:
            if isinstance(value, str):
                value = tuple(float(x.strip()) for x in value.strip('()').split(','))
            elif isinstance(value, tuple):
                value = tuple(float(x) if isinstance(x, (int, float, str)) else 0.0 for x in value)
            else:
                return 0.0

            if len(value) < 3:
                return 0.0

            d, m, s = value
            return d + (m / 60.0) + (s / 3600.0)

        except Exception as e:
            logging.error(f"Error converting GPS value to degrees: {e}")
            return 0.0

    def _parse_size(self, size_str, base_size):
        """Convert percentage or pixel size to actual pixels."""
        try:
            if isinstance(size_str, (int, float)):
                return int(size_str)
            if size_str.endswith('%'):
                percentage = float(size_str.rstrip('%'))
                return int(base_size * (percentage / 100))
            return int(size_str)
        except:
            return base_size

    def _parse_position(self, position, img_size, text_size, margin, offset=0):
        """Calculate pixel position based on position string and margin."""
        width, height = img_size
        text_width, text_height = text_size
        
        # Convert margin to integer if it's a percentage string
        if isinstance(margin, str) and margin.endswith('%'):
            margin = int(float(margin.rstrip('%')) * min(width, height) / 100)
        else:
            margin = int(margin)
        
        positions = {
            'top-left': (margin, margin + offset),
            'top-right': (width - text_width - margin, margin + offset),
            'bottom-left': (margin, height - text_height - margin - offset),
            'bottom-right': (width - text_width - margin, height - text_height - margin - offset),
            'top-center': ((width - text_width) // 2, margin + offset),
            'bottom-center': ((width - text_width) // 2, height - text_height - margin - offset)
        }
        
        return positions.get(position, positions['bottom-left'])

    def render_metadata(self, image, metadata):
        """Render metadata onto the image using the stack manager."""
        if not metadata or not self.styles:
            return image
            
        # Create a copy of the image to work with
        img = image.copy()
        draw = ImageDraw.Draw(img)
        
        # Get enabled fields with available metadata
        enabled_fields = {}
        for field_name, field_config in self.styles['fields'].items():
            if not field_config.get('enabled', False):
                continue
                
            # Format the text if metadata is available
            text = self.format_metadata_text(metadata, field_config)
            if text:
                enabled_fields[field_name] = {**field_config, 'text': text}
        
        # Calculate positions for all enabled fields
        positions = self.stack_manager.calculate_positions(img.size, enabled_fields)
        
        # Render each field
        for field_name, position_data in positions.items():
            field_config = enabled_fields[field_name]
            text = field_config['text']
            
            # Get font
            font_size = position_data['font_size']
            try:
                font = ImageFont.truetype(field_config['font_family'], font_size)
            except:
                font = ImageFont.load_default()
            
            # Calculate maximum width for this position
            max_width = self.stack_manager._parse_size(self.stack_manager.max_text_width, img.size[0])
            
            # Truncate text if necessary
            text = self.stack_manager.truncate_text(text, max_width, font, draw)
            
            # Get text size
            text_size = draw.textsize(text, font=font)
            
            # Calculate position
            x, y = self._parse_position(
                position_data['position'],
                img.size,
                text_size,
                position_data['margin'],
                position_data['offset']
            )
            
            # Draw text
            color = field_config.get('color', '#FFFFFF')
            if isinstance(color, str):
                color = tuple(int(color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4)) + (255,)
            
            # Draw background if enabled
            if self.styles.get('background', {}).get('enabled', False):
                bg_color = self.styles['background'].get('color', '#000000')
                if isinstance(bg_color, str):
                    bg_color = tuple(int(bg_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
                opacity = int(float(self.styles['background'].get('opacity', '50')) * 2.55)
                bg_color = (*bg_color, opacity)
                
                # Add padding around text
                padding = font_size // 4
                bg_bbox = [
                    x - padding,
                    y - padding,
                    x + text_size[0] + padding,
                    y + text_size[1] + padding
                ]
                draw.rectangle(bg_bbox, fill=bg_color)
            
            # Draw text
            draw.text((x, y), text, font=font, fill=color)
        
        return img

    def format_metadata_text(self, metadata, field_config):
        """Format metadata text according to field configuration."""
        try:
            format_str = field_config.get('format', '')
            if not format_str:
                return None
                
            # Create a dictionary of available format variables
            format_vars = {}
            for key in metadata:
                format_vars[key] = metadata[key]
            
            # Try to format the string
            try:
                return format_str.format(**format_vars)
            except KeyError:
                return None
                
        except Exception as e:
            logging.error(f"Error formatting metadata text: {e}")
            return None

    def _parse_color(self, color_str, alpha=255):
        """Convert hex color string to RGBA tuple."""
        try:
            # Remove '#' if present
            color_str = color_str.lstrip('#')
            # Convert hex to RGB
            rgb = tuple(int(color_str[i:i+2], 16) for i in (0, 2, 4))
            # Return RGBA tuple
            return (*rgb, alpha)
        except Exception as e:
            logging.error(f"Error parsing color {color_str}: {e}")
            return (255, 255, 255, alpha)  # Default to white 

    def save_styles(self, styles):
        """Save metadata styling configuration."""
        try:           
            os.makedirs(os.path.dirname(self.styles_path), exist_ok=True)
            with open(self.styles_path, 'w') as f:
                json.dump(styles, f, indent=4)
            self.styles = styles
            return True
        except Exception as e:
            logging.error(f"Error saving metadata styles: {e}")
            return False 