"""
Overlays Blueprint — /overlays page, /api/metadata/*, /api/qrcode/*,
and /test/overlay/<frame_id> routes.
"""
import base64
import io
import json
import logging
import os
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from PIL import Image, ImageDraw

from integrations.overlays.qrcode import QRCodeIntegration
from integrations.overlays.manager import QRCodeOverlay

logger = logging.getLogger(__name__)

overlays_bp = Blueprint('overlays', __name__)


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@overlays_bp.route('/overlays')
def overlays():
    return render_template('overlays.html')


# ---------------------------------------------------------------------------
# Metadata styles
# ---------------------------------------------------------------------------

@overlays_bp.route('/api/metadata/styles', methods=['GET'])
def get_metadata_styles():
    """Get current metadata styling configuration."""
    try:
        logger.info("Fetching metadata styles")
        styles = current_app.metadata_integration.styles
        logger.info(f"Retrieved styles: {styles}")

        if styles is None:
            logger.error("No styles found")
            return jsonify({'success': False, 'error': 'No styles configuration found'}), 404

        return jsonify({'success': True, 'styles': styles})

    except Exception as e:
        logger.error(f"Error getting metadata styles: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@overlays_bp.route('/api/metadata/styles', methods=['POST'])
def update_metadata_styles():
    """Update metadata styling configuration."""
    try:
        styles = request.json
        success = current_app.metadata_integration.save_styles(styles)
        if success:
            return jsonify({'success': True, 'message': 'Styles updated successfully'})
        else:
            raise Exception('Failed to save styles')
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@overlays_bp.route('/api/metadata/preview', methods=['POST'])
def generate_metadata_preview():
    """Generate a preview of metadata overlay with current styles."""
    from model import Photo
    from helpers.image_pipeline import create_temp_image

    try:
        photo = Photo.query.first()
        if not photo:
            return jsonify({'success': False, 'error': 'No photos available for preview'}), 404

        photo_path = os.path.join(current_app.config['UPLOAD_FOLDER'], photo.filename)

        sample_metadata = {
            'date':         datetime.now().strftime('%B %d, %Y'),
            'time':         datetime.now().strftime('%I:%M %p'),
            'camera_make':  'Sample Camera',
            'camera_model': 'Model X',
            'location':     '47.6062°N, 122.3321°W',
        }

        metadata_integration = current_app.metadata_integration
        overlay_manager = current_app.overlay_manager

        with Image.open(photo_path) as img:
            draw = ImageDraw.Draw(img)
            original_get_metadata = metadata_integration.get_metadata
            try:
                metadata_integration.get_metadata = lambda x: sample_metadata
                result_img = overlay_manager.overlays['metadata'].apply(img, draw, photo_path)

                buffered = io.BytesIO()
                result_img.save(buffered, format="PNG")
                img_str = base64.b64encode(buffered.getvalue()).decode()
            finally:
                metadata_integration.get_metadata = original_get_metadata

        return jsonify({'success': True, 'preview': f'data:image/png;base64,{img_str}'})

    except Exception as e:
        logger.error(f"Error generating preview: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@overlays_bp.route('/api/metadata/available-fonts')
def get_available_fonts():
    try:
        from integrations.overlays.manager import MetadataOverlay
        fonts = MetadataOverlay.get_available_fonts()
        return jsonify({'success': True, 'fonts': fonts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ---------------------------------------------------------------------------
# QR code settings
# ---------------------------------------------------------------------------

@overlays_bp.route('/api/qrcode/settings', methods=['GET'])
def get_qrcode_settings():
    """Get QR code settings."""
    try:
        qrcode_config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'integrations', 'overlays', 'qrcode_config.json'
        )
        qrcode_integration = QRCodeIntegration(qrcode_config_path)
        settings = qrcode_integration.load_settings()
        return jsonify({'success': True, 'settings': settings})
    except Exception as e:
        logger.error(f"Error getting QR code settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@overlays_bp.route('/api/qrcode/settings', methods=['POST'])
def update_qrcode_settings():
    """Update QR code settings."""
    try:
        if not request.is_json:
            return jsonify({'success': False, 'error': 'Request must be JSON'}), 400

        data = request.get_json()
        if not all(key in data for key in ['size', 'position', 'link_type']):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        qrcode_config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'integrations', 'overlays', 'qrcode_config.json'
        )
        qrcode_integration = QRCodeIntegration(qrcode_config_path)

        settings = qrcode_integration.load_settings()
        settings.update({
            'size':      data['size'],
            'position':  data['position'],
            'link_type': data['link_type'],
        })

        if qrcode_integration.save_settings(settings):
            qrcode_integration = QRCodeIntegration(qrcode_config_path)
            current_app.overlay_manager.overlays['qrcode'] = QRCodeOverlay(qrcode_integration)
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to save settings'}), 500

    except Exception as e:
        logger.error(f"Error updating QR code settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Test / preview overlay on a frame
# ---------------------------------------------------------------------------

@overlays_bp.route('/test/overlay/<frame_id>')
def test_overlay(frame_id):
    """Preview final image with settings and overlays applied."""
    from model import db, PhotoFrame, PlaylistEntry
    from helpers.image_pipeline import create_temp_image, serve_pil_image
    from services.photo_processor import PhotoProcessor

    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404

    playlist_entry = None
    if frame.playlist_id:
        playlist_entry = (
            PlaylistEntry.query
            .filter_by(playlist_id=frame.playlist_id)
            .order_by(PlaylistEntry.order)
            .first()
        )
    if not playlist_entry or not playlist_entry.photo:
        return jsonify({'error': 'No photos in playlist'}), 404

    photo = playlist_entry.photo
    orientation = frame.orientation or 'portrait'
    upload_folder = current_app.config['UPLOAD_FOLDER']

    if orientation == 'portrait' and photo.portrait_version:
        photo_path = os.path.join(upload_folder, photo.portrait_version)
    elif orientation == 'landscape' and photo.landscape_version:
        photo_path = os.path.join(upload_folder, photo.landscape_version)
    else:
        photo_path = os.path.join(upload_folder, photo.filename)

    if not os.path.exists(photo_path):
        return jsonify({'error': 'Photo file not found'}), 404

    # Handle preview settings from query args
    args = request.args
    if args.get('preview', 'false').lower() == 'true':
        # Use a plain namespace to avoid SQLAlchemy attribute instrumentation
        # interfering with the temporary override values.
        from types import SimpleNamespace
        use_frame = SimpleNamespace(
            id=frame.id,
            orientation=frame.orientation,
            screen_resolution=frame.screen_resolution,
            contrast_factor=float(args.get('contrast_factor', 1.0)),
            saturation=int(args.get('saturation', 100)),
            blue_adjustment=int(args.get('blue_adjustment', 0)),
            padding=int(args.get('padding', 0)),
            color_map=frame.color_map,
            overlay_preferences=json.dumps({
                'weather':  args.get('weather', '').lower() == 'true',
                'metadata': args.get('metadata', '').lower() == 'true',
                'qrcode':   args.get('qrcode', '').lower() == 'true',
            }),
        )
    else:
        use_frame = frame

    overlay_manager = current_app.overlay_manager

    try:
        with Image.open(photo_path) as img:
            processor = PhotoProcessor()
            enhanced_img = processor.enhance_image(img, use_frame)
            temp_path = create_temp_image(enhanced_img)

        overlay_prefs = json.loads(use_frame.overlay_preferences) if use_frame.overlay_preferences else {}
        final_img = overlay_manager.apply_overlays(
            image_path=temp_path,
            preferences=overlay_prefs,
            frame=use_frame,
            photo=photo,
        )

        os.remove(temp_path)
        return serve_pil_image(final_img)

    except Exception as e:
        current_app.logger.error(f"Overlay error: {str(e)}")
        return jsonify({'error': str(e)}), 500
