from PIL import Image, ImageOps
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
    def ensure_orientation(self, img, desired_orientation='portrait', crop_anchor='smart'):
        """
        Ensure image is in the desired orientation by cropping.
        Args:
            img: PIL Image object
            desired_orientation: 'portrait' or 'landscape'
            crop_anchor: 'smart' (default) — face-aware crop, falls back to center when no
                         faces are detected; 'center' — geometric center crop; 'top' — anchor
                         the crop at the top of the image (used by plugins like front_pages).
        Returns:
            PIL Image object in the correct orientation
        """
        logger.debug("Starting orientation adjustment.")

        # Get current dimensions
        width, height = img.size
        current_orientation = 'portrait' if height > width else 'landscape'
        logger.debug(f"Current dimensions: {width}x{height} ({current_orientation})")

        # If orientations don't match, crop the image
        if current_orientation != desired_orientation:
            logger.debug(f"Cropping image from {current_orientation} to {desired_orientation}")
            target_ratio = 4/3 if desired_orientation == 'landscape' else 3/4

            smart_box = None
            if crop_anchor == 'smart':
                try:
                    from helpers.smart_crop import find_face_crop_box
                    smart_box = find_face_crop_box(img, target_ratio)
                except Exception as e:
                    logger.warning(f"Smart crop failed, falling back to center crop: {e}")

            if smart_box is not None:
                left, top, right, bottom = smart_box
            elif desired_orientation == 'landscape':
                # Fallback: center (or top-anchored) crop of a portrait image.
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
                    top = 0 if crop_anchor == 'top' else (height - new_height) // 2
                    left = 0
                    bottom = top + new_height
                    right = width
            else:  # desired_orientation == 'portrait'
                # Fallback: center crop of a landscape image.
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
            logger.debug(f"Cropped to: {img.width}x{img.height}")
        else:
            logger.debug("No cropping needed")
        
        # Log final dimensions
        logger.debug(f"Final image dimensions: {img.width}x{img.height}")
        return img

    def process_for_orientation(self, image_path, orientation='portrait', frame=None, crop_anchor='smart'):
        """
        Process image for target dimensions and ensure correct orientation.
        Args:
            image_path: Path to the image file
            orientation: 'portrait' or 'landscape'
            frame: PhotoFrame object with image settings
            crop_anchor: 'smart' (default), 'center', or 'top' — passed to ensure_orientation
        Returns:
            Path to the processed image
        """
        logger.info(f"Processing image for {orientation}: {image_path}")
        img = None
        resized = None
        try:
            # Open the image
            img = Image.open(image_path)
            
            # Normalize orientation once at load time based on EXIF metadata.
            img = ImageOps.exif_transpose(img)
            
            # Convert RGBA to RGB if necessary
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            
            # Determine natural orientation from normalized dimensions.
            natural_orientation = 'portrait' if img.height > img.width else 'landscape'
            logger.info(f"Natural orientation determined to be {natural_orientation}")
            
            # Ensure correct orientation
            img = self.ensure_orientation(img, orientation, crop_anchor=crop_anchor)
            
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
            
            # Save the processed image without EXIF orientation metadata.
            # Derivatives should be pixel-correct and deterministic for frames.
            resized.save(output_path, quality=95)
                
            logger.info(f"Saved {orientation} version to {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Error processing image: {str(e)}")
            logger.exception("Full traceback:")
            return None
        finally:
            # Close images to free memory
            if img is not None:
                try:
                    img.close()
                except Exception:
                    pass
            if resized is not None and resized is not img:
                try:
                    resized.close()
                except Exception:
                    pass

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
        logger.debug("Starting image enhancement")
        
        if frame is None:
            contrast_factor = 1.0
            saturation = 100
            blue_adjustment = 0
            padding = 0
            color_map = None
        else:
            contrast_factor = frame.contrast_factor
            saturation = frame.saturation
            blue_adjustment = frame.blue_adjustment
            padding = frame.padding if hasattr(frame, 'padding') else 0
            color_map = frame.color_map

            logger.debug(f"Image enhancement settings: contrast={contrast_factor}, saturation={saturation}, blue_adjustment={blue_adjustment}, padding={padding}")

            # Skip enhancement if using default values
            if (contrast_factor == 1.0 and
                saturation == 100 and
                blue_adjustment == 0 and
                padding == 0 and
                (color_map is None or len(color_map) == 0)):
                return img

        # PIL-based fallback when Wand/ImageMagick is not available
        if not WAND_AVAILABLE:
            from PIL import ImageEnhance
            if img.mode != 'RGB':
                img = img.convert('RGB')
            if contrast_factor != 1.0:
                img = ImageEnhance.Contrast(img).enhance(contrast_factor)
            if saturation != 100:
                img = ImageEnhance.Color(img).enhance(saturation / 100.0)
            if blue_adjustment:
                # Match Wand's modulate(hue=100 - blue_adjustment): each unit ≈ 1.8°
                # of hue rotation. PIL's HSV hue channel is 0-255 over 360°, so scale
                # by 256/360 ≈ 0.711 → ~1.28 PIL units per slider step.
                shift = -int(round(blue_adjustment * 1.28))
                h, s, v = img.convert('HSV').split()
                h = h.point(lambda p: (p + shift) % 256)
                img = Image.merge('HSV', (h, s, v)).convert('RGB')
            if padding and padding > 0:
                img = ImageOps.expand(img, border=int(padding), fill=(0, 0, 0))
            return img
        
        enhanced = self._apply_wand_pipeline(
            img, contrast_factor, saturation, blue_adjustment,
            padding, color_map, frame,
        )

        guard_on = getattr(frame, 'overshoot_guard_enabled', True) if frame else False
        boosting = contrast_factor > 1.0 or saturation > 100
        if guard_on and boosting:
            try:
                from helpers.overshoot_guard import (
                    clipped_fraction, scale_boosts,
                    CLIPPED_FRACTION_THRESHOLD, OVERSHOOT_SCALE,
                )
                cf = clipped_fraction(enhanced)
                if cf > CLIPPED_FRACTION_THRESHOLD:
                    cf2, sat2 = scale_boosts(contrast_factor, saturation, OVERSHOOT_SCALE)
                    logger.info(
                        f"Overshoot: clipped {cf*100:.1f}% > "
                        f"{CLIPPED_FRACTION_THRESHOLD*100:.0f}%, re-rendering "
                        f"(contrast {contrast_factor:.2f}→{cf2:.2f}, "
                        f"saturation {saturation}→{sat2})"
                    )
                    enhanced = self._apply_wand_pipeline(
                        img, cf2, sat2, blue_adjustment,
                        padding, color_map, frame,
                    )
            except Exception as e:
                logger.warning(f"Overshoot guard failed, keeping first render: {e}")

        return enhanced

    def _apply_wand_pipeline(self, img, contrast_factor, saturation,
                             blue_adjustment, padding, color_map, frame):
        """Run the Wand enhancement pass once. Safe to call twice on the same
        source image — it operates on a local copy after resize."""
        try:
            # Get original dimensions
            orig_width, orig_height = img.size

            # Get frame dimensions if available
            frame_width = None
            frame_height = None
            if frame and hasattr(frame, 'screen_resolution') and frame.screen_resolution:
                try:
                    resolution_parts = frame.screen_resolution.split('x')
                    if len(resolution_parts) == 2:
                        frame_width = int(resolution_parts[0])
                        frame_height = int(resolution_parts[1])
                        logger.info(f"Using frame dimensions: {frame_width}x{frame_height}")
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Could not parse frame resolution: {e}")

            if not frame_width or not frame_height:
                if frame and hasattr(frame, 'orientation') and frame.orientation == 'portrait':
                    frame_width = 1200
                    frame_height = 1600
                else:
                    frame_width = 1600
                    frame_height = 1200
                logger.info(f"Using default dimensions based on orientation: {frame_width}x{frame_height}")

            # Resize to fit the frame dimensions before Wand processing.
            img_aspect = orig_width / orig_height
            frame_aspect = frame_width / frame_height
            if img_aspect > frame_aspect:
                resize_width = frame_width
                resize_height = int(resize_width / img_aspect)
            else:
                resize_height = frame_height
                resize_width = int(resize_height * img_aspect)
            resize_width = max(1, resize_width)
            resize_height = max(1, resize_height)

            working = img.resize((resize_width, resize_height), Image.LANCZOS)
            if working.mode != 'RGB':
                working = working.convert('RGB')

            img_byte_arr = io.BytesIO()
            working.save(img_byte_arr, format='JPEG', quality=95, subsampling=0)
            img_byte_arr.seek(0)

            with WandImage(blob=img_byte_arr.getvalue(), format='jpeg') as wand_img:
                if padding > 0:
                    logger.info(f"Applying padding of {padding}px to image using ImageMagick border")
                    wand_img.border(Color('black'), width=padding, height=padding)
                    logger.info(f"Padding applied. New dimensions: {wand_img.width}x{wand_img.height}")

                black_point = 0.10 * contrast_factor
                white_point = 1.0 - (0.10 * contrast_factor)
                black_point = min(0.3, max(0.0, black_point))
                white_point = max(0.7, min(1.0, white_point))
                wand_img.contrast_stretch(black_point=black_point, white_point=white_point)

                wand_img.modulate(brightness=103, saturation=saturation, hue=100 - blue_adjustment)

                if color_map and len(color_map) > 0:
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

                img_data = wand_img.make_blob(format='jpeg')
                return Image.open(io.BytesIO(img_data))

        except Exception as e:
            logger.error(f"Error enhancing image: {str(e)}")
            logger.exception("Full traceback:")
            return img