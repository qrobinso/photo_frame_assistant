import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytz
from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)

from helpers.frame_helpers import (
    format_relative_time, is_in_deep_sleep, calculate_sleep_interval,
    PhotoHelper,
)
from model import db, Photo, PhotoFrame, PlaylistEntry, Playlist, EventLog
from settings.persistence import load_server_settings

logger = logging.getLogger(__name__)
frames_bp = Blueprint('frames', __name__)


# ---------------------------------------------------------------------------
# Admin UI routes
# ---------------------------------------------------------------------------

@frames_bp.route('/frame/<frame_id>')
def view_frame(frame_id):
    """Display a virtual frame in a web browser."""
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        flash('Frame not found', 'error')
        return redirect(url_for('system.index'))

    current_photo = None
    if frame.current_photo_id:
        current_photo = db.session.get(Photo, frame.current_photo_id)

    if not current_photo and frame.playlist_entries.count() > 0:
        playlist_entry = frame.playlist_entries.order_by(PlaylistEntry.order).first()
        if playlist_entry:
            current_photo = playlist_entry.photo

    next_wake_time = None
    if frame.next_wake_time:
        next_wake_time = frame.next_wake_time.timestamp() * 1000

    sleep_interval_ms = int(frame.sleep_interval * 60 * 1000)

    return render_template(
        'view_frame.html',
        frame=frame,
        current_photo=current_photo,
        next_wake_time=next_wake_time,
        sleep_interval_ms=sleep_interval_ms,
    )


@frames_bp.route('/manage_frames', methods=['GET', 'POST'])
def manage_frames():
    """Display and manage all registered frames."""
    if request.method == 'POST':
        device_id = request.form.get('device_id')
        name = request.form.get('name')
        if name and len(name) > 256:
            flash('Frame name must be 256 characters or fewer', 'error')
            return redirect(url_for('frames.manage_frames'))
        try:
            sleep_interval = float(request.form.get('sleep_interval', 5.0))
        except (TypeError, ValueError):
            sleep_interval = 5.0
        if not (0.1 <= sleep_interval <= 1440):
            flash('Sleep interval must be between 0.1 and 1440 minutes', 'error')
            return redirect(url_for('frames.manage_frames'))
        frame_type = request.form.get('frame_type', 'physical')

        if frame_type == 'virtual' and not device_id:
            device_id = f"v{uuid.uuid4().hex[:4]}"

        if not device_id:
            flash('Device ID is required for physical frames', 'error')
            return redirect(url_for('frames.manage_frames'))

        frame = db.session.get(PhotoFrame, device_id)
        if frame:
            flash('A frame with this Device ID already exists', 'error')
            return redirect(url_for('frames.manage_frames'))

        frame_name = name or f"Frame {device_id}"
        frame = PhotoFrame(
            id=device_id,
            name=frame_name,
            sleep_interval=sleep_interval,
            battery_level=None,
            last_diagnostic=None,
            frame_type=frame_type,
        )

        try:
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

            db.session.commit()

            if frame_type == 'virtual':
                frame_url = url_for('frames.view_frame', frame_id=device_id, _external=True)
                flash(
                    f'Virtual frame created successfully. Access it at: '
                    f'<a href="{frame_url}" target="_blank">{frame_url}</a>',
                    'success',
                )
            else:
                flash('Frame added successfully', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding frame: {str(e)}', 'error')

        return redirect(url_for('frames.manage_frames'))

    # GET
    frames = PhotoFrame.query.order_by(PhotoFrame.order).all()
    discovered = current_app.frame_discovery.get_discovered_frames()

    now = datetime.now(timezone.utc)
    server_settings = load_server_settings()

    for frame in frames:
        if frame.last_wake_time and frame.last_wake_time.tzinfo is None:
            frame.last_wake_time = pytz.UTC.localize(frame.last_wake_time)
        if frame.next_wake_time and frame.next_wake_time.tzinfo is None:
            frame.next_wake_time = pytz.UTC.localize(frame.next_wake_time)

        frame.last_wake_relative = format_relative_time(
            frame.last_wake_time, now, server_settings['timezone']
        )
        frame.next_wake_relative = format_relative_time(
            frame.next_wake_time, now, server_settings['timezone']
        )

    if request.args.get('format') == 'json':
        frames_data = [{
            'id': frame.id,
            'name': frame.name,
            'orientation': frame.orientation,
            'sleep_interval': frame.sleep_interval,
            'frame_type': frame.frame_type,
        } for frame in frames]
        return jsonify({'frames': frames_data})

    playlists = Playlist.query.order_by(Playlist.name).all()

    return render_template(
        'manage_frames.html',
        frames=frames,
        discovered=discovered,
        playlists=playlists,
        get_current_photo=PhotoHelper.get_current_photo,
        get_next_photo=PhotoHelper.get_next_photo,
        Photo=Photo,
        now=now,
        server_settings=server_settings,
    )


@frames_bp.route('/frames/<frame_id>/playlist', methods=['GET', 'POST'])
def edit_playlist(frame_id):
    frame = PhotoFrame.query.get_or_404(frame_id)

    if not frame.playlist:
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
        db.session.commit()

    if request.method == 'POST':
        data = request.get_json()
        photo_ids = [int(pid) for pid in data['photo_ids'].split(',') if pid.strip()]

        PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).delete()

        for order, photo_id in enumerate(photo_ids):
            playlist_entry = PlaylistEntry(
                playlist_id=frame.playlist_id,
                photo_id=photo_id,
                order=order,
                date_added=datetime.utcnow(),
            )
            db.session.add(playlist_entry)

        frame.playlist.updated_at = datetime.utcnow()
        db.session.commit()

        if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
            current_app.mqtt_integration.update_frame_options(frame)
        return jsonify({'success': True, 'message': 'Playlist updated successfully'})

    return redirect(url_for('playlists.edit_custom_playlist', playlist_id=frame.playlist_id))


@frames_bp.route('/frames/<frame_id>/settings', methods=['GET', 'POST'])
def edit_frame_settings(frame_id):
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        flash('Frame not found.', 'error')
        return redirect(url_for('frames.manage_frames'))

    server_settings = load_server_settings()
    local_tz = pytz.timezone(server_settings['timezone'])

    if request.method == 'POST':
        old_frame_name = frame.name
        new_frame_name = request.form.get('name')
        if new_frame_name and len(new_frame_name) > 256:
            flash('Frame name must be 256 characters or fewer', 'error')
            return redirect(url_for('frames.edit_frame_settings', frame_id=frame_id))
        frame.name = new_frame_name
        try:
            sleep_interval = float(request.form.get('sleep_interval', 5.0))
        except (TypeError, ValueError):
            sleep_interval = 5.0
        if not (0.1 <= sleep_interval <= 1440):
            flash('Sleep interval must be between 0.1 and 1440 minutes', 'error')
            return redirect(url_for('frames.edit_frame_settings', frame_id=frame_id))
        frame.sleep_interval = sleep_interval

        if old_frame_name and new_frame_name and old_frame_name != new_frame_name:
            old_playlist_name = f"{old_frame_name} Playlist"
            new_playlist_name = f"{new_frame_name} Playlist"
            matching_playlist = Playlist.query.filter_by(name=old_playlist_name).first()
            if matching_playlist:
                existing_playlist = Playlist.query.filter_by(name=new_playlist_name).first()
                if not existing_playlist:
                    matching_playlist.name = new_playlist_name
                    current_app.logger.info(f"Renamed playlist '{old_playlist_name}' to '{new_playlist_name}'")

        frame.orientation = request.form.get('orientation', 'portrait')

        playlist_id = request.form.get('playlist_id')
        if playlist_id:
            frame.playlist_id = int(playlist_id)

        frame.contrast_factor = float(request.form.get('contrast_factor', 1.0))
        frame.saturation = int(request.form.get('saturation', 100))
        frame.blue_adjustment = int(request.form.get('blue_adjustment', 0))
        frame.padding = int(request.form.get('padding', 0))

        color_map_text = request.form.get('color_map', '')
        if color_map_text:
            try:
                colors = [color.strip() for color in color_map_text.split('\n') if color.strip()]
                frame.color_map = colors
            except Exception as e:
                current_app.logger.error(f"Error parsing color map: {e}")
        else:
            frame.color_map = None

        frame.shuffle_enabled = request.form.get('shuffle_enabled') == 'on'
        frame.snap_to_hour = request.form.get('snap_to_hour') == 'on'

        frame.deep_sleep_enabled = request.form.get('deep_sleep_enabled') == 'on'
        if frame.deep_sleep_enabled:
            local_start = int(request.form.get('deep_sleep_start', 0))
            local_end = int(request.form.get('deep_sleep_end', 0))

            now = datetime.now()
            local_time = local_tz.localize(now.replace(hour=local_start, minute=0))
            utc_start = local_time.astimezone(pytz.UTC).hour

            local_time = local_tz.localize(now.replace(hour=local_end, minute=0))
            utc_end = local_time.astimezone(pytz.UTC).hour

            frame.deep_sleep_start = utc_start
            frame.deep_sleep_end = utc_end

        try:
            preferences = json.loads(frame.overlay_preferences) if frame.overlay_preferences else {}
            preferences['weather'] = request.form.get('weather_overlay') == 'on'
            preferences['metadata'] = request.form.get('metadata_overlay') == 'on'
            preferences['qrcode'] = request.form.get('qrcode_overlay') == 'on'
            frame.overlay_preferences = json.dumps(preferences)
        except Exception as e:
            current_app.logger.error(f"Error updating overlay preferences: {e}")
            preferences = {'weather': False, 'metadata': False, 'qrcode': False}
            frame.overlay_preferences = json.dumps(preferences)

        db.session.commit()
        flash('Settings updated successfully.')

        if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
            current_app.mqtt_integration.publish_state(frame)

        return redirect(url_for('frames.manage_frames'))

    now = datetime.now(timezone.utc)

    if frame.last_wake_time and frame.last_wake_time.tzinfo is None:
        frame.last_wake_time = pytz.UTC.localize(frame.last_wake_time)
    if frame.next_wake_time and frame.next_wake_time.tzinfo is None:
        frame.next_wake_time = pytz.UTC.localize(frame.next_wake_time)

    if frame.deep_sleep_enabled:
        now_naive = datetime.now()
        utc_time = pytz.UTC.localize(now_naive.replace(hour=frame.deep_sleep_start, minute=0))
        frame.deep_sleep_start = utc_time.astimezone(local_tz).hour

        utc_time = pytz.UTC.localize(now_naive.replace(hour=frame.deep_sleep_end, minute=0))
        frame.deep_sleep_end = utc_time.astimezone(local_tz).hour

    playlists = Playlist.query.order_by(Playlist.name).all()

    weather_enabled = False
    if hasattr(current_app, 'weather_integration') and current_app.weather_integration:
        weather_enabled = current_app.weather_integration.settings.get('enabled', False)

    return render_template(
        'edit_settings.html',
        frame=frame,
        frames=PhotoFrame.query.all(),
        playlists=playlists,
        now=datetime.now(timezone.utc),
        timedelta=timedelta,
        weather_enabled=weather_enabled,
        metadata_enabled=True,
        server_settings=server_settings,
    )


# ---------------------------------------------------------------------------
# Frame API — CRUD
# ---------------------------------------------------------------------------

@frames_bp.route('/api/frames/list', methods=['GET'])
def list_frames():
    """Get a simple list of frames for dropdowns."""
    try:
        frames = PhotoFrame.query.order_by(PhotoFrame.order, PhotoFrame.name).all()
        frame_list = [{
            'id': frame.id,
            'name': frame.name,
            'type': frame.frame_type,
            'status': frame.get_status()[0],
        } for frame in frames]
        return jsonify({'frames': frame_list})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@frames_bp.route('/api/frames/<frame_id>', methods=['PUT'])
def update_frame(frame_id):
    """Update a frame's details (name, etc.)."""
    try:
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return jsonify({'error': 'Frame not found'}), 404

        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400

        data = request.get_json()

        if 'name' in data:
            new_name = data['name'].strip()
            if not new_name:
                return jsonify({'error': 'Name cannot be empty'}), 400
            if len(new_name) > 256:
                return jsonify({'error': 'Name must be 256 characters or fewer'}), 400

            old_name = frame.name
            frame.name = new_name

            if old_name and old_name != new_name:
                old_playlist_name = f"{old_name} Playlist"
                new_playlist_name = f"{new_name} Playlist"
                matching_playlist = Playlist.query.filter_by(name=old_playlist_name).first()
                if matching_playlist:
                    existing_playlist = Playlist.query.filter_by(name=new_playlist_name).first()
                    if not existing_playlist:
                        matching_playlist.name = new_playlist_name
                        current_app.logger.info(f"Renamed playlist '{old_playlist_name}' to '{new_playlist_name}'")

        db.session.commit()

        return jsonify({'success': True, 'frame': {'id': frame.id, 'name': frame.name}})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@frames_bp.route('/api/frames/<frame_id>/playlist', methods=['PUT'])
def change_frame_playlist(frame_id):
    """Change the playlist assigned to a frame."""
    try:
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return jsonify({'error': 'Frame not found'}), 404

        data = request.get_json()
        playlist_id = data.get('playlist_id')

        if playlist_id:
            playlist = Playlist.query.get(playlist_id)
            if not playlist:
                return jsonify({'error': 'Playlist not found'}), 404
            frame.playlist_id = playlist_id
        else:
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

        db.session.commit()

        if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
            current_app.mqtt_integration.update_frame_options(frame)

        return jsonify({
            'success': True,
            'playlist_id': frame.playlist_id,
            'playlist_name': frame.playlist.name,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@frames_bp.route('/api/frames/<frame_id>/delete', methods=['DELETE'])
def delete_frame(frame_id):
    """Delete a frame and its associated data."""
    try:
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return jsonify({'success': False, 'error': 'Frame not found'}), 404

        if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
            try:
                discovery_prefix = current_app.mqtt_integration.discovery_prefix
                device_components = ['select', 'number', 'sensor']
                for component in device_components:
                    topic = f"{discovery_prefix}/{component}/photo_frame/{frame_id}_next_up/config"
                    current_app.mqtt_integration.client.publish(topic, '', retain=True)
                    topic = f"{discovery_prefix}/{component}/photo_frame/{frame_id}_sleep_interval/config"
                    current_app.mqtt_integration.client.publish(topic, '', retain=True)
                    topic = f"{discovery_prefix}/{component}/photo_frame/{frame_id}_last_wake/config"
                    current_app.mqtt_integration.client.publish(topic, '', retain=True)
            except Exception as e:
                current_app.logger.error(f"Error cleaning up MQTT entities: {e}")

        playlist_id = frame.playlist_id

        EventLog.query.filter_by(frame_id=frame_id).delete()

        db.session.delete(frame)

        if playlist_id:
            other_frames_using_playlist = PhotoFrame.query.filter_by(playlist_id=playlist_id).count()
            if other_frames_using_playlist == 0:
                PlaylistEntry.query.filter_by(playlist_id=playlist_id).delete()
                playlist = Playlist.query.get(playlist_id)
                if playlist:
                    db.session.delete(playlist)

        db.session.commit()

        return jsonify({'success': True, 'message': 'Frame deleted successfully'})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting frame: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@frames_bp.route('/api/frames/<frame_id>/import-settings', methods=['POST'])
def import_frame_settings(frame_id):
    """Import settings and playlist from another frame."""
    try:
        data = request.get_json()
        source_frame_id = data.get('source_frame_id')

        target_frame = db.session.get(PhotoFrame, frame_id)
        source_frame = db.session.get(PhotoFrame, source_frame_id)

        if not target_frame or not source_frame:
            return jsonify({'success': False, 'error': 'Frame not found'}), 404

        target_frame.sleep_interval = source_frame.sleep_interval
        target_frame.orientation = source_frame.orientation
        target_frame.overlay_preferences = source_frame.overlay_preferences

        if hasattr(source_frame, 'contrast_factor'):
            target_frame.contrast_factor = source_frame.contrast_factor
        if hasattr(source_frame, 'saturation'):
            target_frame.saturation = source_frame.saturation
        if hasattr(source_frame, 'blue_adjustment'):
            target_frame.blue_adjustment = source_frame.blue_adjustment
        if hasattr(source_frame, 'padding'):
            target_frame.padding = source_frame.padding
        if hasattr(source_frame, 'color_map'):
            target_frame.color_map = source_frame.color_map

        if source_frame.playlist_id:
            target_frame.playlist_id = source_frame.playlist_id

        db.session.commit()

        if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
            current_app.mqtt_integration.update_frame_options(target_frame)

        return jsonify({'success': True, 'message': 'Settings imported successfully'})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error importing frame settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@frames_bp.route('/api/frames/<frame_id>/force_update', methods=['POST'])
def force_frame_update(frame_id):
    """Force a frame to update its current photo."""
    try:
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return jsonify({'success': False, 'error': 'Frame not found'}), 404

        status = frame.get_status()[0]
        if status != 'online':
            return jsonify({
                'success': False,
                'error': f'Frame is {status}. Cannot force update.',
            }), 400

        if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
            current_app.mqtt_integration.publish_state(frame)

        return jsonify({'success': True})

    except Exception as e:
        current_app.logger.error(f"Error forcing frame update: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@frames_bp.route('/api/frames/<frame_id>/toggle_shuffle', methods=['POST'])
def toggle_frame_shuffle(frame_id):
    frame = PhotoFrame.query.get_or_404(frame_id)
    frame.shuffle_enabled = not frame.shuffle_enabled
    db.session.commit()

    if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
        current_app.mqtt_integration.update_frame_options(frame)

    return jsonify({'success': True, 'shuffle_enabled': frame.shuffle_enabled})


@frames_bp.route('/api/frames/<frame_id>/clear_playlist', methods=['POST'])
def clear_frame_playlist(frame_id):
    """Clear all photos from a frame's playlist."""
    frame = PhotoFrame.query.get_or_404(frame_id)

    if frame.playlist_id:
        PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).delete()
        db.session.commit()

    if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
        current_app.mqtt_integration.update_frame_options(frame)

    return jsonify({'success': True, 'message': 'Playlist cleared successfully'})


@frames_bp.route('/api/frames/reorder', methods=['POST'])
def reorder_frames():
    try:
        frame_order = request.json.get('frame_order', [])
        for index, frame_id in enumerate(frame_order):
            frame = PhotoFrame.query.get(frame_id)
            if frame:
                frame.order = index
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})


# ---------------------------------------------------------------------------
# Frame detail API
# ---------------------------------------------------------------------------

@frames_bp.route('/api/frame/<frame_id>')
def get_frame(frame_id):
    """Get frame information."""
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404

    next_photo = PhotoHelper.get_current_photo(frame.id)

    next_photo_info = None
    if next_photo:
        if frame.orientation == 'portrait' and next_photo.portrait_version:
            next_photo_info = next_photo.portrait_version
        elif frame.orientation == 'landscape' and next_photo.landscape_version:
            next_photo_info = next_photo.landscape_version
        else:
            next_photo_info = next_photo.filename

    current_photo = None
    if frame.current_photo_id:
        current_photo = db.session.get(Photo, frame.current_photo_id)

    current_photo_info = None
    if current_photo:
        if frame.orientation == 'portrait' and current_photo.portrait_version:
            current_photo_info = current_photo.portrait_version
        elif frame.orientation == 'landscape' and current_photo.landscape_version:
            current_photo_info = current_photo.landscape_version
        else:
            current_photo_info = current_photo.filename

    now = datetime.now(timezone.utc)
    status = frame.get_status(now)

    next_wake_time = None
    if frame.next_wake_time:
        next_wake_time = frame.next_wake_time.isoformat()

    return jsonify({
        'id': frame.id,
        'name': frame.name,
        'sleep_interval': frame.sleep_interval,
        'orientation': frame.orientation,
        'frame_type': frame.frame_type,
        'current_photo': current_photo_info,
        'next_photo': next_photo_info,
        'status': status,
        'last_wake_time': frame.last_wake_time.isoformat() if frame.last_wake_time else None,
        'next_wake_time': next_wake_time,
    })


@frames_bp.route('/api/frame/<frame_id>/next')
def next_photo(frame_id):
    """Navigate to the next photo in the playlist."""
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404

    playlist = []
    if frame.playlist_id:
        playlist = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id)\
                                    .order_by(PlaylistEntry.order).all()

    if not playlist:
        return jsonify({'error': 'Playlist is empty'}), 404

    if frame.frame_type == 'virtual':
        result = current_app.frame_timing_manager.force_transition(frame_id, direction='next')
        if isinstance(result, tuple):
            return jsonify(result[0]), result[1]

        frame = db.session.get(PhotoFrame, frame_id)
        if not frame or not frame.current_photo_id:
            return jsonify({'error': 'Frame update failed'}), 500

        if frame.playlist_id:
            playlist = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id)\
                                        .order_by(PlaylistEntry.order).all()

        current_entry = None
        for entry in playlist:
            if entry.photo_id == frame.current_photo_id:
                current_entry = entry
                break

        if current_entry:
            current_order = current_entry.order
            for entry in playlist:
                if entry.photo_id != current_entry.photo_id and entry.order > current_order:
                    entry.order -= 1
            current_entry.order = len(playlist) - 1
            db.session.commit()
            current_app.logger.info(f"Updated playlist order for virtual frame {frame_id} after transition")

        return jsonify(result)

    current_position = 0
    if frame.current_photo_id:
        for i, entry in enumerate(playlist):
            if entry.photo_id == frame.current_photo_id:
                current_position = i
                break

    next_position = (current_position + 1) % len(playlist)
    next_entry = playlist[next_position]

    frame.current_photo_id = next_entry.photo_id

    current_entry = playlist[current_position]
    current_order = current_entry.order

    for entry in playlist:
        if entry.order > current_order:
            entry.order -= 1
    current_entry.order = len(playlist) - 1

    db.session.commit()

    photo = db.session.get(Photo, next_entry.photo_id)

    now = datetime.now(timezone.utc)
    frame.last_wake_time = now
    frame.next_wake_time = now + timedelta(minutes=frame.sleep_interval)
    db.session.commit()

    return jsonify({
        'success': True,
        'current_photo': photo.filename if photo else None,
        'next_wake_time': frame.next_wake_time.isoformat() if frame.next_wake_time else None,
        'last_wake_time': frame.last_wake_time.isoformat() if frame.last_wake_time else None,
    })


@frames_bp.route('/api/frame/<frame_id>/prev')
def prev_photo(frame_id):
    """Navigate to the previous photo in the playlist."""
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404

    playlist = []
    if frame.playlist_id:
        playlist = PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id)\
                                    .order_by(PlaylistEntry.order).all()

    if not playlist:
        return jsonify({'error': 'Playlist is empty'}), 404

    if frame.frame_type == 'virtual':
        result = current_app.frame_timing_manager.force_transition(frame_id, direction='prev')
        if isinstance(result, tuple):
            return jsonify(result[0]), result[1]

        frame = db.session.get(PhotoFrame, frame_id)
        if not frame or not frame.current_photo_id:
            return jsonify({'error': 'Frame update failed'}), 500

        current_entry = None
        for entry in playlist:
            if entry.photo_id == frame.current_photo_id:
                current_entry = entry
                break

        if current_entry:
            current_order = current_entry.order
            for entry in playlist:
                if entry.photo_id != current_entry.photo_id and entry.order > current_order:
                    entry.order -= 1
            current_entry.order = len(playlist) - 1
            db.session.commit()
            current_app.logger.info(f"Updated playlist order for virtual frame {frame_id} after transition")

        return jsonify(result)

    current_position = 0
    if frame.current_photo_id:
        for i, entry in enumerate(playlist):
            if entry.photo_id == frame.current_photo_id:
                current_position = i
                break

    prev_position = (current_position - 1) % len(playlist)
    prev_entry = playlist[prev_position]

    frame.current_photo_id = prev_entry.photo_id

    current_entry = playlist[current_position]
    current_order = current_entry.order

    for entry in playlist:
        if entry.order > current_order:
            entry.order -= 1
    current_entry.order = len(playlist) - 1

    db.session.commit()

    photo = db.session.get(Photo, prev_entry.photo_id)

    now = datetime.now(timezone.utc)
    frame.last_wake_time = now
    frame.next_wake_time = now + timedelta(minutes=frame.sleep_interval)
    db.session.commit()

    return jsonify({
        'success': True,
        'current_photo': photo.filename if photo else None,
        'next_wake_time': frame.next_wake_time.isoformat() if frame.next_wake_time else None,
        'last_wake_time': frame.last_wake_time.isoformat() if frame.last_wake_time else None,
    })


@frames_bp.route('/api/frame/<frame_id>/status')
def get_frame_status(frame_id):
    """Get detailed status information about a frame for debugging."""
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404

    status = current_app.frame_timing_manager.check_frame_status(frame_id)

    if status.get('needs_transition') and frame.frame_type == 'virtual':
        logger.info(f"Frame {frame_id} needs transition, forcing transition now")
        result = current_app.frame_timing_manager.force_transition(frame_id, direction='next')
        status['forced_transition'] = True
        status['transition_result'] = result

    return jsonify(status)


# ---------------------------------------------------------------------------
# Discovery routes
# ---------------------------------------------------------------------------

@frames_bp.route('/api/discovery/status')
def discovery_status():
    """Return current discovery enabled state and running state."""
    settings = load_server_settings()
    return jsonify({
        'discovery_enabled': settings.get('discovery_enabled', True),
        'running': current_app.frame_discovery._running,
    })


@frames_bp.route('/api/restart_discovery', methods=['POST'])
def restart_discovery():
    """Restart the frame discovery service (only when discovery is enabled)."""
    try:
        settings = load_server_settings()
        if not settings.get('discovery_enabled', True):
            return jsonify({'success': False, 'error': 'Discovery is disabled'}), 400
        current_app.frame_discovery.stop()
        current_app.frame_discovery.start()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error restarting discovery: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

