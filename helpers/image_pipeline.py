import os
import io
import json
import uuid
import random
import socket
import logging
from datetime import datetime, timedelta, timezone

from flask import current_app, send_file, Response, session
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


def generate_welcome_image(frame):
    """Generate a welcome image showing server name, frame MAC address, and server IP."""
    orientation = frame.orientation if frame else 'portrait'
    if orientation == 'landscape':
        width, height = 1600, 1200
    else:
        width, height = 1200, 1600

    img = Image.new('RGB', (width, height), color=(20, 20, 40))
    draw = ImageDraw.Draw(img)

    # Get server info
    try:
        server_name = socket.gethostname()
    except Exception:
        server_name = 'Unknown Server'
    try:
        server_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        server_ip = 'Unknown IP'
    mac_address = frame.id if frame else 'Unknown'

    font_path = os.path.join(current_app.root_path, 'static', 'fonts', 'Roboto-Regular.ttf')
    bold_font_path = os.path.join(current_app.root_path, 'static', 'fonts', 'BebasNeue-Regular.ttf')

    from PIL import ImageFont
    try:
        title_font = ImageFont.truetype(bold_font_path, size=int(height * 0.07))
        label_font = ImageFont.truetype(bold_font_path, size=int(height * 0.04))
        value_font = ImageFont.truetype(font_path, size=int(height * 0.035))
    except Exception:
        title_font = label_font = value_font = ImageFont.load_default()

    cx = width // 2
    accent = (80, 160, 255)
    white = (240, 240, 240)
    grey = (160, 160, 180)

    # Title
    title = "PHOTO FRAME ASSISTANT"
    draw.text((cx, int(height * 0.12)), title, font=title_font, fill=accent, anchor='mm')

    # Divider line
    line_y = int(height * 0.20)
    draw.line([(int(width * 0.1), line_y), (int(width * 0.9), line_y)], fill=accent, width=2)

    # Info rows
    rows = [
        ("SERVER", server_name),
        ("IP ADDRESS", server_ip),
        ("DEVICE ID", mac_address),
    ]
    start_y = int(height * 0.35)
    row_gap = int(height * 0.13)
    for i, (label, value) in enumerate(rows):
        y = start_y + i * row_gap
        draw.text((cx, y), label, font=label_font, fill=grey, anchor='mm')
        draw.text((cx, y + int(height * 0.055)), value, font=value_font, fill=white, anchor='mm')

    # Bottom divider
    line_y2 = int(height * 0.88)
    draw.line([(int(width * 0.1), line_y2), (int(width * 0.9), line_y2)], fill=accent, width=2)

    hint = "Add photos to this frame's playlist to get started"
    draw.text((cx, int(height * 0.93)), hint, font=value_font, fill=grey, anchor='mm')

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    buf.seek(0)
    return buf


def handle_empty_playlist(frame, output_type):
    """Handle case when playlist is empty."""
    welcome_buf = generate_welcome_image(frame)
    orientation = frame.orientation if frame else 'portrait'
    device_id = frame.id if frame else 'unknown'

    if output_type in ('compressed', 'epaper', 'epd') or (output_type == 'rgb565'):
        # These generators require a file path, so write to a per-device temp file
        tmp_path = os.path.join('/tmp', f'welcome_{device_id}.jpg')
        welcome_buf.seek(0)
        with open(tmp_path, 'wb') as f:
            f.write(welcome_buf.read())
        if output_type == 'compressed':
            return generate_compressed_output(tmp_path, orientation)
        elif output_type in ('epaper', 'epd') or (output_type == 'rgb565' and is_epaper_frame(frame)):
            return generate_epaper_output(tmp_path, orientation)
        elif output_type == 'rgb565':
            return generate_rgb565_output(tmp_path, frame)
    return send_file(welcome_buf, mimetype='image/jpeg')


def get_next_entry(frame, playlist):
    """Select next playlist entry based on shuffle settings."""
    from model import PlaylistEntry
    if frame.shuffle_enabled and frame.playlist_id:
        entries = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).all()
        if not entries:
            return None

        # Create a session key specific to this frame's shuffle session
        shuffle_key = f'frame_{frame.id}_shuffle'
        shown_entries = session.get(shuffle_key, [])

        # If we've shown all entries, reset the tracking
        if len(shown_entries) >= len(entries):
            shown_entries = []

        # Get available entries (those not yet shown)
        available_entries = [entry for entry in entries
                            if entry.id not in shown_entries]

        if available_entries:
            chosen_entry = random.choice(available_entries)
            shown_entries.append(chosen_entry.id)
            session[shuffle_key] = shown_entries
            return chosen_entry
    return playlist[0]


def update_playlist_order(frame, playlist, current_entry):
    """Update playlist order and frame's current photo."""
    from model import db
    # Find current entry's position in the playlist
    current_index = next((i for i, entry in enumerate(playlist) if entry.id == current_entry.id), -1)

    if current_index == -1:
        return  # Should never happen, but safety check

    # Shift subsequent entries up by one
    for entry in playlist[current_index+1:]:
        entry.order -= 1

    # Move current entry to the end
    current_entry.order = len(playlist) - 1

    # Update frame's current photo reference
    frame.current_photo_id = current_entry.photo_id
    frame.last_wake_time = datetime.now(timezone.utc)
    frame.next_wake_time = frame.last_wake_time + timedelta(minutes=frame.sleep_interval)

    db.session.commit()


def process_image_pipeline(frame, photo):
    logger.debug(f"process_image_pipeline")
    """Unified image processing pipeline."""
    # Load base image
    img = load_base_image(frame, photo)

    # Apply enhancements
    if needs_enhancement(frame):
        img = apply_enhancements(img, frame)

    # Create temp file for remaining processing
    temp_path = create_temp_image(img)

    # Apply overlays
    if frame.overlay_preferences:
        overlay_img = apply_overlays(temp_path, frame, photo)
        overlay_img.save(temp_path)  # Overwrite temp file with overlay

    return temp_path


def load_base_image(frame, photo):
    """Load appropriate base image version."""
    filename = get_orientation_filename(frame, photo)
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    return Image.open(filepath)


def get_orientation_filename(frame, photo):
    """Get filename for correct orientation version."""
    if frame.orientation == 'portrait' and photo.portrait_version:
        return photo.portrait_version
    if frame.orientation == 'landscape' and photo.landscape_version:
        return photo.landscape_version
    return photo.filename


def needs_enhancement(frame):
    """Check if any image enhancements are needed."""
    return (frame.contrast_factor != 1.0 or
            frame.saturation != 100 or
            frame.blue_adjustment != 0)


def apply_enhancements(img, frame):
    """Apply image enhancements based on frame settings"""
    from services.photo_processor import PhotoProcessor
    processor = PhotoProcessor()
    return processor.enhance_image(img, frame)


def apply_overlays(temp_path, frame, photo):
    """Apply configured overlays to image."""
    overlay_prefs = json.loads(frame.overlay_preferences) if frame.overlay_preferences else {}
    return current_app.overlay_manager.apply_overlays(temp_path, overlay_prefs, frame, photo)


def generate_final_output(image_path, frame, output_type):
    """Generate final output based on requested type."""
    from helpers.file_helpers import cleanup_temp_files
    if output_type == 'compressed':
        return generate_compressed_output(image_path, frame.orientation)
    elif output_type in ('epaper', 'epd') or (output_type == 'rgb565' and is_epaper_frame(frame)):
        return generate_epaper_output(image_path, frame.orientation if frame else 'portrait')
    elif output_type == 'rgb565':
        return generate_rgb565_output(image_path, frame)

    # Cleanup temp files after sending
    response = send_file(image_path, mimetype='image/jpeg')
    response.call_on_close(lambda: cleanup_temp_files(os.path.dirname(image_path)))
    return response


def generate_compressed_output(image_path, orientation):
    """Generate compressed output for e-paper displays."""
    current_app.logger.debug(f"Calling imgToArray")
    from core.image_conversion import img_to_array
    with Image.open(image_path) as img:
        raw_bytes = img_to_array(img, orientation)
    from helpers.file_helpers import cleanup_temp_files
    cleanup_temp_files(os.path.dirname(image_path))
    return Response(raw_bytes, mimetype='application/octet-stream')


def is_epaper_frame(frame):
    """Determine if the frame is an e-paper device based on capabilities."""
    if not frame or not frame.capabilities:
        return False

    capabilities = frame.capabilities
    if isinstance(capabilities, str):
        try:
            capabilities = json.loads(capabilities)
        except Exception:
            return False

    if not isinstance(capabilities, dict):
        return False

    display_type = str(capabilities.get('display_type', '')).lower()
    if not display_type:
        display_type = str(capabilities.get('screen_type', '')).lower()

    return 'e-paper' in display_type or 'epaper' in display_type


def generate_epaper_output(image_path, orientation):
    """Generate 4bpp palette output for Seeed_GFX color e-paper."""
    current_app.logger.debug("Generating Seeed_GFX e-paper output")
    from core.image_conversion import img_to_epaper_4bit
    with Image.open(image_path) as img:
        raw_bytes = img_to_epaper_4bit(img, orientation)
    from helpers.file_helpers import cleanup_temp_files
    cleanup_temp_files(os.path.dirname(image_path))
    return Response(raw_bytes, mimetype='application/octet-stream')


def generate_rgb565_output(image_path, frame):
    """Generate RGB565 output for TFT/LCD displays."""
    current_app.logger.debug(f"Generating RGB565 output for frame {frame.id if frame else 'unknown'}")
    from core.image_conversion import img_to_rgb565

    # Parse screen resolution from frame if available (e.g., "320x240")
    target_width, target_height = 320, 240  # Default dimensions
    if frame and frame.screen_resolution:
        try:
            parts = frame.screen_resolution.lower().split('x')
            if len(parts) == 2:
                target_width = int(parts[0])
                target_height = int(parts[1])
        except (ValueError, AttributeError):
            current_app.logger.warning(f"Could not parse screen_resolution: {frame.screen_resolution}, using defaults")

    with Image.open(image_path) as img:
        raw_bytes = img_to_rgb565(img, target_width, target_height)
    from helpers.file_helpers import cleanup_temp_files
    cleanup_temp_files(os.path.dirname(image_path))
    return Response(raw_bytes, mimetype='application/octet-stream')


def create_temp_image(img):
    temp_dir = current_app.config['UPLOAD_FOLDER']
    temp_name = f"temp_{uuid.uuid4().hex}.jpg"
    temp_path = os.path.join(temp_dir, temp_name)
    img.save(temp_path)
    return temp_path


def serve_pil_image(pil_img):
    img_io = io.BytesIO()
    pil_img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')
