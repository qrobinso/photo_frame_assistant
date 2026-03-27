"""
Playlists Blueprint — /playlists page and /api/custom-playlists/* routes.
"""
import logging
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request

logger = logging.getLogger(__name__)

playlists_bp = Blueprint('playlists', __name__)


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@playlists_bp.route('/playlists')
def manage_playlists():
    from model import Playlist, PhotoFrame
    playlists = Playlist.query.all()
    frames = PhotoFrame.query.order_by(PhotoFrame.order, PhotoFrame.name).all()
    return render_template('playlists/manage.html', playlists=playlists, frames=frames)


@playlists_bp.route('/playlists/<int:playlist_id>/edit')
def edit_custom_playlist(playlist_id):
    """Page for editing a playlist."""
    from model import Playlist, Photo, PlaylistEntry
    playlist = Playlist.query.get_or_404(playlist_id)

    playlist_photos = [
        entry.photo
        for entry in playlist.entries.order_by(PlaylistEntry.order).all()
        if entry.photo
    ]

    playlist_photo_ids = [photo.id for photo in playlist_photos]
    if playlist_photo_ids:
        bench_photos = Photo.query.filter(
            ~Photo.id.in_(playlist_photo_ids)
        ).order_by(Photo.uploaded_at.desc()).all()
    else:
        bench_photos = Photo.query.order_by(Photo.uploaded_at.desc()).all()

    return render_template(
        'playlists/edit.html',
        playlist=playlist,
        playlist_photos=playlist_photos,
        bench_photos=bench_photos,
    )


# ---------------------------------------------------------------------------
# API — playlist CRUD
# ---------------------------------------------------------------------------

@playlists_bp.route('/api/custom-playlists', methods=['GET'])
def get_custom_playlists():
    """Get all playlists."""
    from model import Playlist
    playlists = Playlist.query.all()
    return jsonify([{
        'id':          p.id,
        'name':        p.name,
        'photo_count': p.entries.count(),
        'frame_count': len(p.frames),
        'frames':      [{'id': f.id, 'name': f.name} for f in p.frames],
        'created_at':  p.created_at.isoformat(),
        'updated_at':  p.updated_at.isoformat(),
    } for p in playlists])


@playlists_bp.route('/api/custom-playlists', methods=['POST'])
def create_custom_playlist():
    """Create a new playlist."""
    from model import db, Playlist

    if not request.is_json:
        return jsonify({'error': 'Content-Type must be application/json'}), 400

    data = request.get_json()
    name = data.get('name')

    if not name:
        base_name = "New Playlist"
        existing_names = {p.name for p in Playlist.query.all()}
        if base_name not in existing_names:
            name = base_name
        else:
            counter = 2
            while f"{base_name} {counter}" in existing_names:
                counter += 1
            name = f"{base_name} {counter}"

    playlist = Playlist(name=name)
    db.session.add(playlist)
    db.session.commit()

    return jsonify({
        'id':         playlist.id,
        'name':       playlist.name,
        'created_at': playlist.created_at.isoformat(),
    }), 201


@playlists_bp.route('/api/custom-playlists/<int:playlist_id>', methods=['PUT'])
def update_custom_playlist(playlist_id):
    """Update a playlist's details (name, etc.)."""
    from model import db, Playlist

    try:
        playlist = Playlist.query.get_or_404(playlist_id)

        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400

        data = request.get_json()

        if 'name' in data:
            new_name = data['name'].strip()
            if not new_name:
                return jsonify({'error': 'Name cannot be empty'}), 400
            playlist.name = new_name

        playlist.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({
            'success':  True,
            'playlist': {'id': playlist.id, 'name': playlist.name},
        })

    except Exception as e:
        from model import db
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@playlists_bp.route('/api/custom-playlists/<int:playlist_id>', methods=['DELETE'])
def delete_custom_playlist(playlist_id):
    """Delete a playlist and all its entries."""
    from model import db, Playlist, PhotoFrame, PlaylistEntry

    try:
        playlist = Playlist.query.get_or_404(playlist_id)
        force = request.args.get('force', '').lower() == 'true'

        frames_using_playlist = PhotoFrame.query.filter_by(playlist_id=playlist_id).all()
        if frames_using_playlist:
            if not force:
                frame_names = [f.name or f.id for f in frames_using_playlist]
                return jsonify({
                    'error': (
                        f'Cannot delete playlist - it is being used by '
                        f'{len(frames_using_playlist)} frame(s): {", ".join(frame_names)}'
                    ),
                    'frames': [{'id': f.id, 'name': f.name} for f in frames_using_playlist],
                }), 400

            for frame in frames_using_playlist:
                frame.playlist_id = None

        PlaylistEntry.query.filter_by(playlist_id=playlist_id).delete()
        db.session.delete(playlist)
        db.session.commit()

        return jsonify({'success': True})

    except Exception as e:
        from model import db
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API — playlist entries
# ---------------------------------------------------------------------------

@playlists_bp.route('/api/custom-playlists/<int:playlist_id>/entries', methods=['POST'])
def add_to_custom_playlist(playlist_id):
    """Add photos to a playlist."""
    from model import db, Playlist, PlaylistEntry

    playlist = Playlist.query.get_or_404(playlist_id)

    if not request.is_json:
        return jsonify({'error': 'Content-Type must be application/json'}), 400

    data = request.get_json()
    photo_ids = data.get('photo_ids', [])
    position = data.get('position', 'end')

    if not photo_ids:
        return jsonify({'error': 'No photos specified'}), 400

    try:
        existing_photo_ids = set(
            entry.photo_id
            for entry in PlaylistEntry.query.filter_by(playlist_id=playlist_id).all()
        )

        new_photo_ids = [pid for pid in photo_ids if pid not in existing_photo_ids]
        skipped_count = len(photo_ids) - len(new_photo_ids)

        entries_added = []

        if new_photo_ids:
            if position == 'start':
                num_new = len(new_photo_ids)
                PlaylistEntry.query.filter_by(playlist_id=playlist_id).update(
                    {PlaylistEntry.order: PlaylistEntry.order + num_new}
                )
                for order, photo_id in enumerate(new_photo_ids):
                    entry = PlaylistEntry(
                        playlist_id=playlist_id,
                        photo_id=photo_id,
                        order=order,
                    )
                    db.session.add(entry)
                    entries_added.append(entry)
            else:
                next_order = (
                    db.session.query(db.func.max(PlaylistEntry.order))
                    .filter(PlaylistEntry.playlist_id == playlist_id)
                    .scalar() or 0
                )
                next_order += 1

                for photo_id in new_photo_ids:
                    entry = PlaylistEntry(
                        playlist_id=playlist_id,
                        photo_id=photo_id,
                        order=next_order,
                    )
                    db.session.add(entry)
                    entries_added.append(entry)
                    next_order += 1

            db.session.commit()

        return jsonify({
            'success':       True,
            'added_count':   len(entries_added),
            'skipped_count': skipped_count,
            'entries': [{
                'id':        entry.id,
                'photo_id':  entry.photo_id,
                'order':     entry.order,
                'thumbnail': entry.photo.thumbnail,
                'filename':  entry.photo.filename,
            } for entry in entries_added],
        })

    except Exception as e:
        from model import db
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@playlists_bp.route('/api/custom-playlists/<int:playlist_id>/entries', methods=['DELETE'])
def clear_playlist_entries(playlist_id):
    """Clear all entries from a playlist."""
    from model import db, Playlist, PlaylistEntry

    try:
        playlist = Playlist.query.get_or_404(playlist_id)
        PlaylistEntry.query.filter_by(playlist_id=playlist_id).delete()
        playlist.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        from model import db
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@playlists_bp.route('/api/custom-playlists/<int:playlist_id>/entries/<int:entry_id>', methods=['DELETE'])
def delete_playlist_entry(playlist_id, entry_id):
    """Delete a single entry from a playlist."""
    from model import db, Playlist, PlaylistEntry

    try:
        playlist = Playlist.query.get_or_404(playlist_id)
        entry = PlaylistEntry.query.get_or_404(entry_id)

        if entry.playlist_id != playlist_id:
            return jsonify({'error': 'Entry does not belong to this playlist'}), 400

        db.session.delete(entry)

        remaining_entries = PlaylistEntry.query.filter_by(
            playlist_id=playlist_id
        ).order_by(PlaylistEntry.order).all()

        for i, e in enumerate(remaining_entries):
            e.order = i

        db.session.commit()
        return jsonify({'success': True})

    except Exception as e:
        from model import db
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@playlists_bp.route('/api/custom-playlists/<int:playlist_id>/entries/reorder', methods=['POST'])
def reorder_playlist_entries(playlist_id):
    """Update the order of entries in a playlist."""
    from model import db, Playlist, PlaylistEntry

    try:
        playlist = Playlist.query.get_or_404(playlist_id)

        data = request.get_json()
        if not data or 'entries' not in data:
            return jsonify({'error': 'No entries provided'}), 400

        entries = data['entries']
        entry_ids = [entry['id'] for entry in entries]

        db_entries = PlaylistEntry.query.filter(
            PlaylistEntry.id.in_(entry_ids),
            PlaylistEntry.playlist_id == playlist_id,
        ).all()

        if len(db_entries) != len(entry_ids):
            return jsonify({'error': 'Invalid entry IDs provided'}), 400

        for entry_data in entries:
            entry = next(e for e in db_entries if e.id == entry_data['id'])
            entry.order = entry_data['order']

        db.session.commit()
        return jsonify({'success': True})

    except Exception as e:
        from model import db
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Apply playlist to frame
# ---------------------------------------------------------------------------

@playlists_bp.route('/api/frames/<frame_id>/apply-playlist/<int:playlist_id>', methods=['POST'])
def apply_playlist_to_frame(frame_id, playlist_id):
    """Apply a playlist to a frame by setting the frame's playlist_id."""
    from model import db, PhotoFrame, Playlist

    try:
        frame = PhotoFrame.query.get_or_404(frame_id)
        playlist = Playlist.query.get_or_404(playlist_id)

        frame.playlist_id = playlist_id
        db.session.commit()

        mqtt = current_app.mqtt_integration
        if mqtt:
            mqtt.update_frame_options(frame)

        return jsonify({
            'success':       True,
            'message':       f'Playlist "{playlist.name}" assigned to frame',
            'playlist_id':   playlist_id,
            'playlist_name': playlist.name,
        })

    except Exception as e:
        from model import db
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
