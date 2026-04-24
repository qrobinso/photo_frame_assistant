"""
Frame-facing hardware API — routes consumed by photo frame clients.
These are the highest-traffic endpoints; do not change URL paths.
"""
import logging
import os
import uuid
import json
import time
import pytz
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request, send_file

from helpers.frame_helpers import calculate_sleep_interval
from helpers.file_helpers import cleanup_temp_files
from helpers.image_pipeline import (
    handle_empty_playlist, get_next_entry, update_playlist_order,
    process_image_pipeline, generate_final_output,
)
from settings.persistence import load_server_settings

logger = logging.getLogger(__name__)

frame_client_bp = Blueprint('frame_client', __name__)


@frame_client_bp.route('/api/settings')
def get_settings():
    """Get settings for a specific frame, including sync timing."""
    from model import db, PhotoFrame, PlaylistEntry
    device_id = request.args.get('device_id')
    if not device_id:
        return jsonify({"error": "Missing device_id"}), 400

    frame = PhotoFrame.query.get(device_id)
    if not frame:
        return jsonify({"error": "Device not found"}), 404

    MIN_SLEEP_INTERVAL = 1
    now = datetime.now(timezone.utc)
    sleep_interval = None
    sleep_reason = None
    next_sync = None

    if frame.deep_sleep_enabled:
        deep_sleep_interval = calculate_sleep_interval(frame, now)
        if deep_sleep_interval > frame.sleep_interval:
            sleep_interval = deep_sleep_interval
            sleep_reason = "Frame is in deep sleep mode"

    if sleep_interval is None:
        sleep_interval = round(frame.sleep_interval, 1)
        sleep_reason = "Using frame's default interval"

    if sleep_interval < MIN_SLEEP_INTERVAL:
        sleep_interval = MIN_SLEEP_INTERVAL
        sleep_reason += " (adjusted to minimum)"

    image_settings = {
        'contrast_factor': frame.contrast_factor if hasattr(frame, 'contrast_factor') and frame.contrast_factor is not None else 1.0,
        'saturation': frame.saturation if hasattr(frame, 'saturation') and frame.saturation is not None else 100,
        'blue_adjustment': frame.blue_adjustment if hasattr(frame, 'blue_adjustment') and frame.blue_adjustment is not None else 0,
    }

    frame.last_wake_time = now
    frame.next_wake_time = now + timedelta(minutes=sleep_interval)
    db.session.commit()

    next_sync_str = next_sync.strftime('%Y-%m-%d %H:%M:%S UTC') if next_sync else None
    server_settings = load_server_settings()

    playlist_count = 0
    if frame.playlist_id:
        playlist_count = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).count()

    return jsonify({
        'sleep_interval': sleep_interval,
        'sleep_reason': sleep_reason,
        'next_sync': next_sync_str,
        'orientation': frame.orientation,
        'image_settings': image_settings,
        'server_time': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'timezone': server_settings.get('timezone', 'UTC'),
        'shuffle_enabled': frame.shuffle_enabled,
        'current_photo_id': frame.current_photo_id if frame.current_photo_id else None,
        'deep_sleep_enabled': frame.deep_sleep_enabled,
        'deep_sleep_start': frame.deep_sleep_start,
        'deep_sleep_end': frame.deep_sleep_end,
        'playlist_count': playlist_count,
        'has_photos': playlist_count > 0,
        'show_welcome': playlist_count == 0,
    })


@frame_client_bp.route('/api/diagnostic', methods=['POST'])
def update_diagnostic():
    """Handle frame diagnostic updates."""
    from model import db, PhotoFrame
    data = request.get_json()
    device_id = data.get('device_id')
    if not device_id:
        return jsonify({"error": "Missing device_id"}), 400

    frame = PhotoFrame.query.get(device_id)
    if not frame:
        return jsonify({"error": "Device not found"}), 404

    now = datetime.utcnow()
    frame.last_wake_time = now
    frame.last_diagnostic = now
    frame.diagnostics = data

    if 'battery_level' in data:
        frame.battery_level = data['battery_level']

    if 'next_wake' in data:
        try:
            next_wake_str = data['next_wake'].replace('Z', '+00:00')
            next_wake = datetime.fromisoformat(next_wake_str)
            if next_wake.tzinfo is not None:
                next_wake = next_wake.astimezone(timezone.utc).replace(tzinfo=None)
            frame.next_wake_time = next_wake
            logger.info(
                f"Frame {frame.id} diagnostic update (UTC): "
                f"Current time: {now.isoformat()}Z  "
                f"Next wake: {next_wake.isoformat()}Z  "
                f"Time until wake: {((next_wake - now).total_seconds() / 60):.1f} minutes"
            )
        except Exception as e:
            logger.error(f"Error parsing next_wake time for frame {frame.id}: {e}")
            logger.error(f"Received next_wake value: {data.get('next_wake')}")

    if 'capabilities' in data:
        try:
            frame.capabilities = data['capabilities']
            logger.info(f"Updated capabilities for frame {frame.id}")
        except Exception as e:
            logger.error(f"Error updating capabilities for frame {frame.id}: {e}")

    db.session.commit()
    return jsonify({"message": "Diagnostic info updated"})


@frame_client_bp.route('/api/discovered_frames')
def get_discovered_frames():
    """Return list of frames discovered via Zeroconf."""
    return jsonify(current_app.frame_discovery.get_discovered_frames())


@frame_client_bp.route('/api/register_frame', methods=['POST'])
def register_frame():
    """Handle frame self-registration."""
    from model import db, PhotoFrame, Playlist, PlaylistEntry
    from services.event_logger import EventLogger
    try:
        data = request.json
        device_id = data.get('device_id')
        properties = data.get('properties', {})

        if not device_id:
            return jsonify({'error': 'No device_id provided'}), 400

        print(f"Received registration for device {device_id} with properties:", properties)

        frame = db.session.get(PhotoFrame, device_id)
        if not frame:
            frame_name = data.get('name', f'Device {device_id}')
            frame = PhotoFrame(
                id=device_id,
                name=frame_name,
                manufacturer=properties.get('manufacturer'),
                model=properties.get('model'),
                hardware_rev=properties.get('hardware_rev'),
                firmware_rev=properties.get('firmware_rev'),
                screen_resolution=properties.get('screen_resolution'),
                aspect_ratio=properties.get('aspect_ratio'),
                os=properties.get('os'),
                sleep_interval=30,
            )
            db.session.add(frame)
            db.session.flush()

            playlist_name = f"{frame_name} Playlist"
            base_name = playlist_name
            counter = 1
            while Playlist.query.filter_by(name=playlist_name).first():
                counter += 1
                playlist_name = f"{base_name} ({counter})"
            playlist = Playlist(name=playlist_name)
            db.session.add(playlist)
            db.session.flush()
            frame.playlist_id = playlist.id

            print(f"Created new frame with properties:", {
                'manufacturer': frame.manufacturer, 'model': frame.model,
                'hardware_rev': frame.hardware_rev, 'firmware_rev': frame.firmware_rev,
                'screen_resolution': frame.screen_resolution, 'aspect_ratio': frame.aspect_ratio,
                'os': frame.os, 'playlist_id': frame.playlist_id,
            })
        else:
            frame.manufacturer = properties.get('manufacturer', frame.manufacturer)
            frame.model = properties.get('model', frame.model)
            frame.hardware_rev = properties.get('hardware_rev', frame.hardware_rev)
            frame.firmware_rev = properties.get('firmware_rev', frame.firmware_rev)
            frame.screen_resolution = properties.get('screen_resolution', frame.screen_resolution)
            frame.aspect_ratio = properties.get('aspect_ratio', frame.aspect_ratio)
            frame.os = properties.get('os', frame.os)

            if not frame.playlist_id:
                playlist_name = f"{frame.name} Playlist"
                base_name = playlist_name
                counter = 1
                while Playlist.query.filter_by(name=playlist_name).first():
                    counter += 1
                    playlist_name = f"{base_name} ({counter})"
                playlist = Playlist(name=playlist_name)
                db.session.add(playlist)
                db.session.flush()
                frame.playlist_id = playlist.id
                print(f"Created playlist for existing frame {frame.id}")

            print(f"Updated existing frame with properties:", {
                'manufacturer': frame.manufacturer, 'model': frame.model,
                'hardware_rev': frame.hardware_rev, 'firmware_rev': frame.firmware_rev,
                'screen_resolution': frame.screen_resolution, 'aspect_ratio': frame.aspect_ratio,
                'os': frame.os, 'playlist_id': frame.playlist_id,
            })

        if 'battery_level' in properties:
            frame.battery_level = properties['battery_level']
        if 'diagnostic_info' in properties:
            frame.diagnostic_info = json.dumps(properties['diagnostic_info'])
            frame.last_diagnostic = datetime.now(timezone.utc)
        if 'capabilities' in properties:
            try:
                frame.capabilities = properties['capabilities']
            except Exception as e:
                print(f"Error storing capabilities: {str(e)}")

        EventLogger.log_connection(frame.id, source=EventLogger.SOURCE_FRAME,
                                   details={'name': frame.name})
        db.session.commit()

        playlist_count = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).count() if frame.playlist_id else 0
        return jsonify({
            'message': 'Frame registered successfully',
            'playlist_count': playlist_count,
            'has_photos': playlist_count > 0,
            'show_welcome': playlist_count == 0,
        })
    except Exception as e:
        print(f"Error in register_frame: {str(e)}")
        from model import db
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@frame_client_bp.route('/api/current_photo')
def get_current_photo():
    """Return the current photo for a frame and cycle playlist."""
    from model import db, Photo, PhotoFrame, PlaylistEntry
    import random as _random
    device_id = request.args.get('device_id')
    logger.debug(f"Received request for device_id: {device_id}")

    if not device_id:
        logger.error("No device_id provided")
        return jsonify({'error': 'No device_id provided'}), 400

    frame = db.session.get(PhotoFrame, device_id)
    if frame:
        logger.debug(f"Found frame: {frame.id}")

        playlist = []
        if frame.playlist_id:
            playlist = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id)\
                                          .order_by(PlaylistEntry.order).all()
        logger.debug(f"Playlist entries found: {len(playlist)}")

        if not playlist:
            output_type = request.args.get('type')
            return handle_empty_playlist(frame, output_type)

        if frame.shuffle_enabled:
            current_entry = _random.choice(playlist)
        else:
            current_entry = playlist[0]

        photo = Photo.query.get(current_entry.photo_id)

        for i, entry in enumerate(playlist[1:], 0):
            entry.order = i
        current_entry.order = len(playlist) - 1
        db.session.commit()

        if photo:
            if frame.orientation == 'portrait' and photo.portrait_version:
                filename = photo.portrait_version
            elif frame.orientation == 'landscape' and photo.landscape_version:
                filename = photo.landscape_version
            else:
                filename = photo.filename

            upload_folder = current_app.config['UPLOAD_FOLDER']
            photo_path = os.path.join(upload_folder, filename)
            is_video = filename.lower().endswith('.mp4') or photo.media_type == 'video'
            mimetype = 'video/mp4' if is_video else 'image/jpeg'

            if is_video:
                return send_file(photo_path, mimetype=mimetype)

            processed_image = None
            if hasattr(frame, 'contrast_factor') and frame.contrast_factor is not None:
                logger.debug(f"Applying image settings for frame {frame.id}")
                try:
                    from PIL import Image
                    with Image.open(photo_path) as img:
                        processed_image = current_app.photo_processor.enhance_image(img, frame)
                    logger.debug("Successfully applied image enhancements")
                except Exception as e:
                    logger.error(f"Error processing image with frame settings: {e}")
                    processed_image = None

            if processed_image:
                temp_dir = os.path.join(upload_folder, 'temp')
                os.makedirs(temp_dir, exist_ok=True)
                temp_filename = f"temp_{uuid.uuid4().hex}_{os.path.basename(photo_path)}"
                temp_path = os.path.join(temp_dir, temp_filename)
                if processed_image.mode == 'P':
                    processed_image = processed_image.convert('RGB')
                processed_image.save(temp_path, quality=95)
                cleanup_temp_files(temp_dir)

                frame.current_photo_id = photo.id
                frame.last_wake_time = datetime.now(timezone.utc)
                sleep_interval = calculate_sleep_interval(frame)
                frame.next_wake_time = frame.last_wake_time + timedelta(minutes=sleep_interval)
                db.session.commit()
                return send_file(temp_path, mimetype='image/jpeg')

            frame.current_photo_id = photo.id
            frame.last_wake_time = datetime.now(timezone.utc)
            sleep_interval = calculate_sleep_interval(frame)
            frame.next_wake_time = frame.last_wake_time + timedelta(minutes=sleep_interval)
            db.session.commit()
            return send_file(photo_path, mimetype=mimetype)

    frame = db.session.get(PhotoFrame, request.args.get('device_id')) if request.args.get('device_id') else None
    output_type = request.args.get('type')
    return handle_empty_playlist(frame, output_type)


@frame_client_bp.route('/api/next_photo')
def get_next_photo():
    """Return the next photo for a frame using the unified processing pipeline."""
    from model import db, PhotoFrame, PlaylistEntry
    from services.event_logger import EventLogger
    device_id = request.args.get('device_id')
    output_type = request.args.get('type')

    frame = db.session.get(PhotoFrame, device_id)

    if not frame or not (playlist := frame.playlist_entries.order_by(PlaylistEntry.order).all()):
        return handle_empty_playlist(frame, output_type)

    try:
        current_entry = get_next_entry(frame, playlist)
        photo = current_entry.photo
        update_playlist_order(frame, playlist, current_entry)

        temp_path = process_image_pipeline(frame, photo)

        headers = {
            'X-Photo-ID': str(photo.id),
            'X-Photo-Filename': photo.filename,
            'Content-Type': 'application/octet-stream' if output_type in ('compressed', 'rgb565', 'epaper', 'epd') else 'image/jpeg',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        }

        response = generate_final_output(temp_path, frame, output_type)
        for key, value in headers.items():
            response.headers[key] = value

        EventLogger.log_photo_request(frame.id, photo_id=photo.id if photo else None)
        return response

    except Exception as e:
        logger.error(f"Error in get_next_photo: {e}")
        return jsonify({'error': str(e)}), 500


@frame_client_bp.route('/api/server-time')
def get_server_time():
    """Return server timezone and current time information."""
    now = datetime.utcnow()
    local = datetime.now()
    return jsonify({
        'utc_time': now.isoformat(),
        'local_time': local.isoformat(),
        'timezone': time.tzname,
        'utc_offset': (local - now).total_seconds() / 3600,
    })
