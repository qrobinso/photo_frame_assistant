from PIL import Image
import io
import numpy as np
import os
from datetime import datetime

def img_to_array(image, orientation='portrait'):
    """
    Convert an image to a format suitable for e-paper displays.
    
    Args:
        image: PIL Image object
        orientation: The desired orientation ('portrait' or 'landscape')
        
    Returns:
        bytes: Raw bytes of the image data in the format expected by the e-paper display
    """
    
    # Ensure image is in RGB mode (not RGBA)
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # For landscape orientation, always rotate 90 degrees
    if orientation.lower() == 'landscape':
        try:
            # For newer versions of PIL
            from PIL.Image import Transpose
            image = image.transpose(Transpose.ROTATE_90)
        except (ImportError, AttributeError):
            # For older versions of PIL
            image = image.transpose(Image.ROTATE_90)
    
    # Target dimensions for the e-paper display
    target_width, target_height = 1200, 1600
    
    # Resize to FILL the target dimensions (will crop if necessary)
    if image.size != (target_width, target_height):
        # Calculate the resize ratio for both dimensions
        width_ratio = target_width / image.width
        height_ratio = target_height / image.height
        
        # Use the LARGER ratio to ensure the image fills the target dimensions
        # This will result in cropping, but no white bars
        resize_ratio = max(width_ratio, height_ratio)
        
        # Calculate new dimensions
        new_width = int(image.width * resize_ratio)
        new_height = int(image.height * resize_ratio)
        
        # Resize the image to fill or exceed the target dimensions
        image = image.resize((new_width, new_height), Image.LANCZOS)
        
        # If the image is now larger than the target, crop it to the center
        if new_width > target_width or new_height > target_height:
            # Calculate crop box (centered)
            left = (new_width - target_width) // 2
            top = (new_height - target_height) // 2
            right = left + target_width
            bottom = top + target_height
            
            # Crop the image to the target dimensions
            image = image.crop((left, top, right, bottom))
    
    # Create a palette image with the 7 colors used by the e-paper display
    pal_image = Image.new('P', (1, 1))
    # The palette order is: Black, White, Yellow, Red, Black(duplicate), Blue, Green
    pal_image.putpalette((0,0,0, 255,255,255, 255,255,0, 255,0,0, 0,0,0, 0,0,255, 0,255,0) + (0,0,0)*249)
    
    # Convert the source image to the 7 colors, dithering if needed
    image_7color = image.convert("RGB").quantize(palette=pal_image)
    
    # Get the raw bytes of the quantized image
    buf_7color = bytearray(image_7color.tobytes('raw'))
    
    # PIL does not support 4 bit color, so pack the 4 bits of color
    # into a single byte to transfer to the panel
    buf = bytearray(int(image.width * image.height / 2))
    idx = 0
    
    # Pack two 4-bit color values into each byte
    for i in range(0, len(buf_7color), 2):
        if i + 1 < len(buf_7color):
            buf[idx] = (buf_7color[i] << 4) | buf_7color[i+1]
        else:
            # If we have an odd number of pixels, pad with white (1)
            buf[idx] = (buf_7color[i] << 4) | 1
        idx += 1
    
    return bytes(buf) 

def img_to_rgb565(image, target_width=320, target_height=240, swap_bytes=True):
    """
    Convert an image to RGB565 format (16-bit color) for TFT/LCD displays.
    
    RGB565 format:
    - 5 bits for red (bits 15-11)
    - 6 bits for green (bits 10-5)
    - 5 bits for blue (bits 4-0)
    
    Args:
        image: PIL Image object
        target_width: Target width in pixels (default: 320)
        target_height: Target height in pixels (default: 240)
        swap_bytes: If True, swap bytes within each 16-bit pixel (default: True)
                    This is required for TFT_eSPI and most ESP32/Arduino displays.
                    Matches the output of rgb565-converter and LVGL image converter.
        
    Returns:
        bytes: Raw bytes of the image data in RGB565 format (2 bytes per pixel)
    """
    
    # Ensure image is in RGB mode (not RGBA)
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # Resize to FILL the target dimensions (will crop if necessary)
    if image.size != (target_width, target_height):
        # Calculate the resize ratio for both dimensions
        width_ratio = target_width / image.width
        height_ratio = target_height / image.height
        
        # Use the LARGER ratio to ensure the image fills the target dimensions
        resize_ratio = max(width_ratio, height_ratio)
        
        # Calculate new dimensions
        new_width = int(image.width * resize_ratio)
        new_height = int(image.height * resize_ratio)
        
        # Resize the image to fill or exceed the target dimensions
        image = image.resize((new_width, new_height), Image.LANCZOS)
        
        # If the image is now larger than the target, crop it to the center
        if new_width > target_width or new_height > target_height:
            # Calculate crop box (centered)
            left = (new_width - target_width) // 2
            top = (new_height - target_height) // 2
            right = left + target_width
            bottom = top + target_height
            
            # Crop the image to the target dimensions
            image = image.crop((left, top, right, bottom))
    
    # Convert to numpy array for efficient processing
    img_array = np.array(image)
    
    # Extract RGB channels using the same formula as rgb565-converter
    # (r & 0xF8) << 8 | (g & 0xFC) << 3 | b >> 3
    r = img_array[:, :, 0].astype(np.uint16)
    g = img_array[:, :, 1].astype(np.uint16)
    b = img_array[:, :, 2].astype(np.uint16)
    
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    
    if swap_bytes:
        # Swap high and low bytes within each 16-bit pixel
        # This is what TFT_eSPI expects (same as rgb565-converter with swap=True)
        rgb565 = ((rgb565 & 0xFF) << 8) | ((rgb565 >> 8) & 0xFF)
    
    # Output as native byte order (after swap, this gives correct result)
    return rgb565.astype(np.uint16).tobytes()

def img_to_epaper_4bit(image, orientation='portrait'):
    """
    Convert an image to 4bpp palette indices for Seeed_GFX color e-paper.

    The e-paper palette uses 4-bit values for these 6 colors:
    - White: 0x0
    - Black: 0xF
    - Yellow: 0xB
    - Red: 0x6
    - Blue: 0xD
    - Green: 0x2
    """

    # Ensure image is in RGB mode (not RGBA)
    if image.mode != 'RGB':
        image = image.convert('RGB')

    # For landscape orientation, always rotate 90 degrees
    if orientation and orientation.lower() == 'landscape':
        try:
            # For newer versions of PIL
            from PIL.Image import Transpose
            image = image.transpose(Transpose.ROTATE_90)
        except (ImportError, AttributeError):
            # For older versions of PIL
            image = image.transpose(Image.ROTATE_90)

    # Target dimensions for the 13.3" e-paper display
    target_width, target_height = 1200, 1600

    # Resize to FILL the target dimensions (will crop if necessary)
    if image.size != (target_width, target_height):
        # Calculate the resize ratio for both dimensions
        width_ratio = target_width / image.width
        height_ratio = target_height / image.height

        # Use the LARGER ratio to ensure the image fills the target dimensions
        resize_ratio = max(width_ratio, height_ratio)

        # Calculate new dimensions
        new_width = int(image.width * resize_ratio)
        new_height = int(image.height * resize_ratio)

        # Resize the image to fill or exceed the target dimensions
        image = image.resize((new_width, new_height), Image.LANCZOS)

        # If the image is now larger than the target, crop it to the center
        if new_width > target_width or new_height > target_height:
            # Calculate crop box (centered)
            left = (new_width - target_width) // 2
            top = (new_height - target_height) // 2
            right = left + target_width
            bottom = top + target_height

            # Crop the image to the target dimensions
            image = image.crop((left, top, right, bottom))

    # Build a palette image with 6 colors in a fixed order
    # Order: White, Black, Yellow, Red, Blue, Green
    pal_image = Image.new('P', (1, 1))
    pal_image.putpalette(
        (255, 255, 255,   # White
         0, 0, 0,         # Black
         255, 255, 0,     # Yellow
         255, 0, 0,       # Red
         0, 0, 255,       # Blue
         0, 255, 0)       # Green
        + (0, 0, 0) * (256 - 6)
    )

    # Quantize the image to the palette (dither helps with gradients)
    indexed = image.convert("RGB").quantize(palette=pal_image, dither=Image.FLOYDSTEINBERG)

    # Map palette indices to Seeed_GFX e-paper 4-bit codes
    # Index: 0=White, 1=Black, 2=Yellow, 3=Red, 4=Blue, 5=Green
    index_to_code = np.array([0x0, 0xF, 0xB, 0x6, 0xD, 0x2], dtype=np.uint8)

    # Convert indexed image to mapped codes
    idx_array = np.array(indexed, dtype=np.uint8)
    code_array = index_to_code[idx_array]

    # Pack two 4-bit pixels into each byte (even pixel in high nibble)
    flat = code_array.flatten()
    buf = np.empty((flat.size + 1) // 2, dtype=np.uint8)
    buf[:flat.size // 2] = (flat[0::2] << 4) | flat[1::2]

    # If odd number of pixels (shouldn't happen), pad with white
    if flat.size % 2:
        buf[-1] = (flat[-1] << 4) | 0x0

    return bytes(buf)

def generate_demonstration_images(input_image_path, output_dir="upload/temp"):
    """
    Process an image through the conversion pipeline and save visualizations 
    of each step to help understand the process.
    
    Args:
        input_image_path: Path to the input image
        output_dir: Directory to save output images (default: "upload/temp")
    
    Returns:
        list: Paths to all generated images
    """
    # Make sure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate timestamp for unique filenames
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Load the original image
    original_image = Image.open(input_image_path)
    
    # Save the original image
    original_path = os.path.join(output_dir, f"{timestamp}_1_original.jpg")
    original_image.save(original_path)
    
    # Ensure RGB mode
    if original_image.mode != 'RGB':
        original_image = original_image.convert('RGB')
    
    # Determine orientation and rotate if needed
    is_image_landscape = original_image.width > original_image.height
    oriented_image = original_image
    
    if is_image_landscape:
        try:
            # For newer versions of PIL
            from PIL.Image import Transpose
            oriented_image = original_image.transpose(Transpose.ROTATE_270)
        except (ImportError, AttributeError):
            # For older versions of PIL
            oriented_image = original_image.transpose(Image.ROTATE_270)
        
        oriented_path = os.path.join(output_dir, f"{timestamp}_2_rotated.jpg")
        oriented_image.save(oriented_path)
    
    # Target dimensions for the e-paper display
    target_width, target_height = 1200, 1600
    
    # Resize image to fill target dimensions
    width_ratio = target_width / oriented_image.width
    height_ratio = target_height / oriented_image.height
    resize_ratio = max(width_ratio, height_ratio)
    
    new_width = int(oriented_image.width * resize_ratio)
    new_height = int(oriented_image.height * resize_ratio)
    
    resized_image = oriented_image.resize((new_width, new_height), Image.LANCZOS)
    resized_path = os.path.join(output_dir, f"{timestamp}_3_resized.jpg")
    resized_image.save(resized_path)
    
    # Crop if needed
    if new_width > target_width or new_height > target_height:
        left = (new_width - target_width) // 2
        top = (new_height - target_height) // 2
        right = left + target_width
        bottom = top + target_height
        
        cropped_image = resized_image.crop((left, top, right, bottom))
        cropped_path = os.path.join(output_dir, f"{timestamp}_4_cropped.jpg")
        cropped_image.save(cropped_path)
    else:
        cropped_image = resized_image
        cropped_path = resized_path
    
    # Create a palette image with the 7 colors used by the e-paper display
    pal_image = Image.new('P', (1, 1))
    # The palette order is: Black, White, Yellow, Red, Black(duplicate), Blue, Green
    pal_image.putpalette((0,0,0, 255,255,255, 255,255,0, 255,0,0, 0,0,0, 0,0,255, 0,255,0) + (0,0,0)*249)
    
    # Convert the source image to the 7 colors
    quantized_image = cropped_image.quantize(palette=pal_image)
    quantized_path = os.path.join(output_dir, f"{timestamp}_5_quantized.jpg")
    quantized_image.convert('RGB').save(quantized_path)
    
    # Create a visualization of the final byte array
    buf_7color = bytearray(quantized_image.tobytes('raw'))
    
    # This converts the byte array back to a viewable image to show what is sent to display
    reconstructed = Image.new('P', (target_width, target_height))
    reconstructed.putpalette((0,0,0, 255,255,255, 255,255,0, 255,0,0, 0,0,0, 0,0,255, 0,255,0) + (0,0,0)*249)
    reconstructed.putdata(buf_7color)
    
    final_path = os.path.join(output_dir, f"{timestamp}_6_final.jpg")
    reconstructed.convert('RGB').save(final_path)
    
    # Also save a visualization of how the actual bytes would look
    # (unpacking the 4-bit values that would be packed in the actual data)
    final_bytes_path = os.path.join(output_dir, f"{timestamp}_7_byte_representation.jpg")
    
    # Create an array to represent unpacked bytes
    width = target_width
    height = target_height
    unpacked_data = []
    
    # Simulate the packing/unpacking process
    buf = bytearray(int(width * height / 2))
    idx = 0
    
    for i in range(0, len(buf_7color), 2):
        if i + 1 < len(buf_7color):
            buf[idx] = (buf_7color[i] << 4) | buf_7color[i+1]
            unpacked_data.append(buf_7color[i])
            unpacked_data.append(buf_7color[i+1])
        else:
            buf[idx] = (buf_7color[i] << 4) | 1
            unpacked_data.append(buf_7color[i])
            unpacked_data.append(1)  # white padding
        idx += 1
    
    # Create image from unpacked data
    unpacked_image = Image.new('P', (width, height))
    unpacked_image.putpalette((0,0,0, 255,255,255, 255,255,0, 255,0,0, 0,0,0, 0,0,255, 0,255,0) + (0,0,0)*249)
    unpacked_image.putdata(unpacked_data)
    unpacked_image.convert('RGB').save(final_bytes_path)
    
    # Return paths to all generated images
    return [
        original_path,
        oriented_path if is_image_landscape else None,
        resized_path,
        cropped_path if cropped_path != resized_path else None,
        quantized_path,
        final_path,
        final_bytes_path
    ]

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python imgToArray.py <input_image_path> [output_directory]")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "upload/temp"
    
    generated_images = generate_demonstration_images(input_path, output_dir)
    
    print(f"Generated demonstration images in {output_dir}:")
    for img_path in generated_images:
        if img_path:
            print(f"- {img_path}")