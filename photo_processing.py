from PIL import Image
import logging
import os

# Configure logging for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Prevent propagation to the root logger to avoid duplicate messages
logger.propagate = False

# Create console handler if it doesn't exist
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)

# Check for Wand library
WAND_AVAILABLE = False
try:
    from wand.image import Image as WandImage
    from wand.color import Color
    import io
    WAND_AVAILABLE = True
    logger.info("Wand library is available for image enhancement")
except ImportError:
    logger.warning("Wand library is not available. Image enhancement features will be limited.")
    logger.warning("To enable full image enhancement, install Wand: pip install Wand")
    logger.warning("You will also need to install ImageMagick: https://imagemagick.org/script/download.php")

class PhotoProcessor:
    def ensure_orientation(self, img, desired_orientation='portrait', exif_orientation=None):
        """
        Ensure image is in the desired orientation by cropping.
        Args:
            img: PIL Image object
            desired_orientation: 'portrait' or 'landscape'
            exif_orientation: Orientation value from EXIF data
        Returns: 
            PIL Image object in the correct orientation
        """
        logger.info(f"Starting orientation adjustment. EXIF orientation: {exif_orientation}")
        
        # EXIF orientation values and their meanings:
        # 1: Normal (0°)
        # 2: Mirrored horizontal
        # 3: Rotated 180°
        # 4: Mirrored vertical
        # 5: Mirrored horizontal & rotated 270° CW
        # 6: Rotated 90° CW
        # 7: Mirrored horizontal & rotated 90° CW
        # 8: Rotated 270° CW
        
        # First, apply EXIF orientation to get the image right side up
        if exif_orientation:
            logger.info(f"Applying EXIF orientation: {exif_orientation}")
            try:
                if exif_orientation == 2:
                    logger.info("Flipping image horizontally")
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)
                elif exif_orientation == 3:
                    logger.info("Rotating image 180 degrees")
                    img = img.rotate(180, expand=True)
                elif exif_orientation == 4:
                    logger.info("Flipping image vertically")
                    img = img.transpose(Image.FLIP_TOP_BOTTOM)
                elif exif_orientation == 5:
                    logger.info("Rotating image 270 degrees CW and flipping horizontally")
                    img = img.rotate(-270, expand=True)
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)
                elif exif_orientation == 6:
                    logger.info("Rotating image 90 degrees CW")
                    img = img.rotate(-90, expand=True)
                elif exif_orientation == 7:
                    logger.info("Rotating image 90 degrees CW and flipping horizontally")
                    img = img.rotate(-90, expand=True)
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)
                elif exif_orientation == 8:
                    logger.info("Rotating image 270 degrees CW")
                    img = img.rotate(-270, expand=True)
            except Exception as e:
                logger.error(f"Error applying EXIF orientation: {e}")
        
        # Get current dimensions
        width, height = img.size
        current_orientation = 'portrait' if height > width else 'landscape'
        logger.info(f"Current dimensions: {width}x{height} ({current_orientation})")
        
        # If orientations don't match, crop the image
        if current_orientation != desired_orientation:
            logger.info(f"Cropping image from {current_orientation} to {desired_orientation}")
            
            if desired_orientation == 'landscape':
                # For landscape, crop the center portion of the portrait image
                target_ratio = 4/3  # Standard landscape ratio
                new_height = int(width / target_ratio)
                if new_height > height:
                    # If height would be too large, adjust width instead
                    new_width = int(height * target_ratio)
                    left = (width - new_width) // 2
                    top = 0
                    right = left + new_width
                    bottom = height
                else:
                    # Crop height to match target ratio
                    top = (height - new_height) // 2
                    left = 0
                    bottom = top + new_height
                    right = width
            else:  # desired_orientation == 'portrait'
                # For portrait, crop the center portion of the landscape image
                target_ratio = 3/4  # Standard portrait ratio
                new_width = int(height * target_ratio)
                if new_width > width:
                    # If width would be too large, adjust height instead
                    new_height = int(width / target_ratio)
                    top = (height - new_height) // 2
                    left = 0
                    bottom = top + new_height
                    right = width
                else:
                    # Crop width to match target ratio
                    left = (width - new_width) // 2
                    top = 0
                    right = left + new_width
                    bottom = height
            
            # Crop the image
            img = img.crop((left, top, right, bottom))
            logger.info(f"Cropped to: {img.width}x{img.height}")
        else:
            logger.info("No cropping needed")
        
        # Log final dimensions
        logger.info(f"Final image dimensions: {img.width}x{img.height}")
        return img

    def process_for_orientation(self, image_path, orientation='portrait', frame=None):
        """
        Process image for target dimensions and ensure correct orientation.
        Args:
            image_path: Path to the image file
            orientation: 'portrait' or 'landscape'
            frame: PhotoFrame object with image settings
        Returns:
            Path to the processed image
        """
        logger.info(f"Processing image for {orientation}: {image_path}")
        try:
            # Open the image
            img = Image.open(image_path)
            
            # Get EXIF orientation if it exists
            exif_orientation = None
            exif_data = None
            try:
                exif = img._getexif()
                if exif:
                    exif_orientation = exif.get(274)  # 274 is the EXIF tag for orientation
                    # Store original EXIF data to preserve it
                    exif_data = img.info.get('exif')
            except (AttributeError, KeyError, IndexError, TypeError) as e:
                logger.warning(f"Could not get EXIF data: {e}")
            
            # Convert RGBA to RGB if necessary
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            
            # Determine natural orientation based on EXIF
            if exif_orientation in [5, 6, 7, 8]:
                # These orientations indicate the image is naturally portrait
                natural_orientation = 'portrait'
            elif exif_orientation in [1, 2, 3, 4]:
                # These orientations indicate the image is naturally landscape
                natural_orientation = 'landscape'
            else:
                # If no EXIF orientation, use dimensions
                natural_orientation = 'portrait' if img.height > img.width else 'landscape'
            
            logger.info(f"Natural orientation determined to be {natural_orientation} (EXIF: {exif_orientation})")
            
            # Ensure correct orientation
            img = self.ensure_orientation(img, orientation, exif_orientation)
            
            # Apply image enhancements if frame is provided
            if frame:
                logger.info(f"Applying image enhancements for frame: {frame.id}")
                img = self.enhance_image(img, frame)
            
            # Define target dimensions based on orientation
            if orientation == 'portrait':
                target_width = 1200
                target_height = 1600
            else:  # landscape
                target_width = 1600
                target_height = 1200
            
            # Calculate aspect ratios
            target_ratio = target_width / target_height
            img_ratio = img.width / img.height
            
            # Resize image maintaining aspect ratio
            if img_ratio > target_ratio:
                # Image is wider than target
                new_height = target_height
                new_width = int(img_ratio * new_height)
            else:
                # Image is taller than target
                new_width = target_width
                new_height = int(new_width / img_ratio)
            
            # Resize the image
            resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Generate output filename
            filename = os.path.basename(image_path)
            name, ext = os.path.splitext(filename)
            output_path = os.path.join(
                os.path.dirname(image_path),
                f"{name}_{orientation}{ext}"
            )
            
            # Save the processed image - with or without EXIF data
            if exif_data:
                logger.info(f"Preserved EXIF data in {orientation} version")
                resized.save(output_path, quality=95, exif=exif_data)
            else:
                logger.info(f"No EXIF data to preserve in {orientation} version")
                resized.save(output_path, quality=95)
                
            logger.info(f"Saved {orientation} version to {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Error processing image: {str(e)}")
            logger.exception("Full traceback:")
            return None

    def check_orientation(self, image_path):
        """
        Check the orientation of an image.
        Returns: 'portrait' or 'landscape'
        """
        try:
            with Image.open(image_path) as img:
                return 'portrait' if img.height > img.width else 'landscape'
        except Exception as e:
            logger.error(f"Error checking image orientation: {str(e)}")
            return None

    def enhance_image(self, img, frame=None):
        """
        Enhance image using the frame's image settings.
        Args:
            img: PIL Image object
            frame: PhotoFrame object with image settings
        Returns:
            Enhanced PIL Image object
        """
        logger.info("Starting image enhancement")
        
        # If no Wand library, return original image
        if not WAND_AVAILABLE:
            logger.warning("Wand library not available. Skipping image enhancement.")
            return img
            
        if frame is None:
            logger.warning("No frame provided for image enhancement. Using defaults.")
            contrast_factor = 1.0
            saturation = 100
            blue_adjustment = 0
            padding = 0
            color_map = None
        else:
            # Log frame object details
            logger.info(f"Frame object: id={frame.id}, type={type(frame)}")
            logger.info(f"Frame attributes: {dir(frame)}")
            
            contrast_factor = frame.contrast_factor
            saturation = frame.saturation
            blue_adjustment = frame.blue_adjustment
            padding = frame.padding if hasattr(frame, 'padding') else 0
            color_map = frame.color_map
            
            # Log the image settings being used
            logger.info(f"Image enhancement settings: contrast={contrast_factor}, saturation={saturation}, blue_adjustment={blue_adjustment}, padding={padding}")
            logger.info(f"Color map: {color_map if color_map else 'None'}")
            
            # Skip enhancement if using default values
            if (contrast_factor == 1.0 and 
                saturation == 100 and 
                blue_adjustment == 0 and
                padding == 0 and
                (color_map is None or len(color_map) == 0)):
                logger.info("Frame is using default settings, skipping enhancement")
                return img
        
        try:
            # Get original dimensions
            orig_width, orig_height = img.size
            
            # Get frame dimensions if available
            frame_width = None
            frame_height = None
            if frame and hasattr(frame, 'screen_resolution') and frame.screen_resolution:
                try:
                    # Parse resolution string (e.g., "800x600")
                    resolution_parts = frame.screen_resolution.split('x')
                    if len(resolution_parts) == 2:
                        frame_width = int(resolution_parts[0])
                        frame_height = int(resolution_parts[1])
                        logger.info(f"Using frame dimensions: {frame_width}x{frame_height}")
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Could not parse frame resolution: {e}")
            
            # If we don't have frame dimensions, use default dimensions
            if not frame_width or not frame_height:
                # Use orientation to determine default dimensions
                if frame and hasattr(frame, 'orientation') and frame.orientation == 'portrait':
                    frame_width = 1200
                    frame_height = 1600
                else:  # landscape or default
                    frame_width = 1600
                    frame_height = 1200
                logger.info(f"Using default dimensions based on orientation: {frame_width}x{frame_height}")
            
            # First resize the image to fit the frame dimensions (without padding)
            # This saves resources for subsequent processing
            img_aspect = orig_width / orig_height
            frame_aspect = frame_width / frame_height
            
            if img_aspect > frame_aspect:
                # Image is wider than frame, fit to width
                resize_width = frame_width
                resize_height = int(resize_width / img_aspect)
            else:
                # Image is taller than frame, fit to height
                resize_height = frame_height
                resize_width = int(resize_height * img_aspect)
            
            # Ensure resize dimensions are at least 1px
            resize_width = max(1, resize_width)
            resize_height = max(1, resize_height)
            
            # Resize original image
            img = img.resize((resize_width, resize_height), Image.LANCZOS)
            
            # Convert to RGB if necessary and save as JPEG
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Convert PIL image to bytes for Wand processing using JPEG
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='JPEG', quality=95, subsampling=0)  # Highest quality JPEG
            img_byte_arr.seek(0)
            
            # Process with ImageMagick via Wand using JPEG format
            with WandImage(blob=img_byte_arr.getvalue(), format='jpeg') as wand_img:
                # Apply padding if needed using ImageMagick's border functionality
                if padding > 0:
                    logger.info(f"Applying padding of {padding}px to image using ImageMagick border")
                    wand_img.border(Color('black'), width=padding, height=padding)
                    logger.info(f"Padding applied. New dimensions: {wand_img.width}x{wand_img.height}")
                
                # Apply auto gamma correction for better tonal balance
                #logger.info("Applying auto gamma correction")
                #wand_img.auto_gamma()
                
                # Apply light noise reduction (0.5 is a conservative value that reduces noise while preserving detail)
                #logger.info("Applying noise reduction")
                #wand_img.noise('gaussian', attenuate=0.0)
                
                # Calculate black and white points based on contrast factor
                black_point = 0.10 * contrast_factor
                white_point = 1.0 - (0.10 * contrast_factor)
                # Ensure values stay in valid range
                black_point = min(0.3, max(0.0, black_point))
                white_point = max(0.7, min(1.0, white_point))
                
                wand_img.contrast_stretch(black_point=black_point, white_point=white_point)
                
                # Apply saturation and hue adjustment
                wand_img.modulate(brightness=103, saturation=saturation, hue=100 - blue_adjustment)
                
                # Apply color quantization if color map is provided and not empty
                if color_map and len(color_map) > 0:
                    # Create color map for quantization
                    color_map_wand = []
                    for color in color_map:
                        color_map_wand.append(Color(color))
                    
                    # Log color map information
                    logger.info(f"Using color map with {len(color_map)} colors for image enhancement")
                    logger.info(f"First few colors in map: {color_map[:5] if len(color_map) > 5 else color_map}")
                    
                    try:
                        logger.info(f"Applying color quantization with {len(color_map)} colors")
                        wand_img.quantize(number_colors=len(color_map), dither=True)
                        logger.info("Color quantization with Floyd-Steinberg dithering applied successfully")
                    except Exception as e:
                        logger.warning(f"Floyd-Steinberg dithering failed: {e}, falling back to no dithering")
                        try:
                            wand_img.quantize(number_colors=len(color_map), dither=False)
                            logger.info("Color quantization without dithering applied as fallback")
                        except Exception as e2:
                            logger.error(f"Color quantization failed completely: {e2}")
                
                # Convert back to PIL image as JPEG
                img_data = wand_img.make_blob(format='jpeg')
                enhanced_img = Image.open(io.BytesIO(img_data))
                
                return enhanced_img
                
        except Exception as e:
            logger.error(f"Error enhancing image: {str(e)}")
            logger.exception("Full traceback:")
            return img  # Return original image if enhancement fails