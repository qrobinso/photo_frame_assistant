import base64
import io
import logging
import os
import secrets
import subprocess
import uuid
from datetime import datetime
from flask import (Blueprint, current_app, jsonify, redirect,
                   render_template, request, send_from_directory, url_for, flash)
from PIL import Image, ImageDraw, ImageEnhance, ImageOps
from werkzeug.utils import secure_filename

from helpers.file_helpers import (
    allowed_file, extract_exif_metadata, generate_video_thumbnail,
    cleanup_temp_files, ALLOWED_EXTENSIONS,
)
from helpers.frame_helpers import add_photo_to_frame_playlist
from model import db, Photo, PhotoFrame, PlaylistEntry, Playlist

logger = logging.getLogger(__name__)
photos_bp = Blueprint('photos', __name__)


# ---------------------------------------------------------------------------
# Admin web-interface routes
# ---------------------------------------------------------------------------

@photos_bp.route('/upload', methods=['GET', 'POST'])
def upload_photo():
    if request.method == 'POST':
        is_api_request = request.headers.get('accept') == 'application/json'

        if 'photo' not in request.files:
            if is_api_request:
                return jsonify({'success': False, 'error': 'No photo file provided'}), 400
            flash('No photo file provided.')
            return redirect(request.url)

        file = request.files['photo']
        if file.filename == '':
            if is_api_request:
                return jsonify({'success': False, 'error': 'No selected file'}), 400
            flash('No selected file.')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            frame_id = request.form.get('frame_id')

            thumbnails_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'thumbnails')
            os.makedirs(thumbnails_dir, exist_ok=True)

            filename = secure_filename(file.filename)
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            exif_metadata = extract_exif_metadata(filepath)
            if exif_metadata:
                current_app.logger.info(f"Successfully extracted EXIF metadata from {filename}")
            else:
                current_app.logger.info(f"No EXIF metadata found in {filename}")

            # Convert HEIC/HEIF to JPG
            try:
                if filename.lower().endswith(('.heic', '.heif')):
                    import pyheif
                    heif_file = pyheif.read(filepath)
                    img = Image.frombytes(
                        heif_file.mode,
                        heif_file.size,
                        heif_file.data,
                        "raw",
                        heif_file.mode,
                        heif_file.stride,
                    )
                    metadata = None
                    try:
                        for metadata in heif_file.metadata or []:
                            if metadata['type'] == 'Exif':
                                current_app.logger.info("Found EXIF metadata in HEIC file")
                                metadata = metadata['data']
                                break
                    except Exception as e:
                        current_app.logger.error(f"Error extracting metadata from HEIC: {e}")

                    new_filename = f"{os.path.splitext(filename)[0]}.jpg"
                    new_filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], new_filename)
                    if metadata:
                        img.save(new_filepath, "JPEG", quality=95, exif=metadata)
                        current_app.logger.info("Preserved EXIF metadata during HEIC conversion")
                    else:
                        img.save(new_filepath, "JPEG", quality=95)

                    os.remove(filepath)
                    filename = new_filename
                    filepath = new_filepath

                    if not exif_metadata:
                        exif_metadata = extract_exif_metadata(filepath)
                        if exif_metadata:
                            current_app.logger.info(f"Successfully extracted EXIF metadata from converted {filename}")

                elif filename.lower().endswith('.avif'):
                    with Image.open(filepath) as img:
                        metadata = None
                        try:
                            metadata = img.info.get('exif')
                        except Exception as e:
                            current_app.logger.error(f"Error extracting metadata from AVIF: {e}")

                        new_filename = f"{os.path.splitext(filename)[0]}.jpg"
                        new_filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], new_filename)
                        if metadata:
                            img.save(new_filepath, "JPEG", quality=95, exif=metadata)
                            current_app.logger.info("Preserved EXIF metadata during AVIF conversion")
                        else:
                            img.save(new_filepath, "JPEG", quality=95)

                    os.remove(filepath)
                    filename = new_filename
                    filepath = new_filepath

                    if not exif_metadata:
                        exif_metadata = extract_exif_metadata(filepath)
                        if exif_metadata:
                            current_app.logger.info(f"Successfully extracted EXIF metadata from converted {filename}")

            except Exception as e:
                current_app.logger.error(f"HEIC/AVIF conversion error: {e}")
                flash('Error converting file')
                return redirect(request.url)

            is_video = filename.lower().endswith(('.mp4', '.mov'))
            thumb_filename = None
            duration = None
            portrait_path = None
            landscape_path = None

            if is_video:
                thumb_filename = f"thumb_{filename}.jpg"
                thumb_path = os.path.join(thumbnails_dir, thumb_filename)

                if filename.lower().endswith('.mov'):
                    mp4_filename = os.path.splitext(filename)[0] + '.mp4'
                    mp4_filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], mp4_filename)
                    progress_file = 'ffmpeg_progress.txt'

                    try:
                        probe = subprocess.run([
                            'ffprobe', '-v', 'error',
                            '-select_streams', 'v:0',
                            '-show_entries', 'stream=width,height',
                            '-of', 'csv=s=x:p=0',
                            filepath
                        ], capture_output=True, text=True)
                        width, height = map(int, probe.stdout.strip().split('x'))
                        new_width = width // 2
                        new_height = height // 2

                        subprocess.run([
                            'ffmpeg', '-y',
                            '-i', filepath,
                            '-vf', f'scale={new_width}:{new_height}',
                            '-c:v', 'libx264',
                            '-preset', 'ultrafast',
                            '-tune', 'fastdecode',
                            '-crf', '28',
                            '-an',
                            '-movflags', '+faststart',
                            '-progress', progress_file,
                            mp4_filepath
                        ], check=True)

                        os.remove(filepath)
                        filepath = mp4_filepath
                        filename = mp4_filename

                    except Exception as e:
                        logger.error(f"Error converting MOV to MP4: {e}")

                    if os.path.exists(progress_file):
                        os.remove(progress_file)

                if generate_video_thumbnail(filepath, thumb_path):
                    try:
                        probe = subprocess.run([
                            'ffprobe', '-v', 'error',
                            '-show_entries', 'format=duration',
                            '-of', 'default=noprint_wrappers=1:nokey=1',
                            filepath
                        ], capture_output=True, text=True)
                        duration = float(probe.stdout)
                    except Exception as e:
                        logger.error(f"Error getting video duration: {e}")

                photo = Photo(
                    filename=filename,
                    portrait_version=filename,
                    landscape_version=filename,
                    thumbnail=thumb_filename,
                    media_type='video',
                    duration=duration,
                    heading=request.form.get('heading', ''),
                    exif_metadata=exif_metadata,
                )
            else:
                try:
                    with Image.open(filepath) as img:
                        normalized_img = ImageOps.exif_transpose(img)

                        thumb_img = normalized_img.copy()
                        thumb_img.thumbnail((400, 400))
                        # JPEG can't encode alpha/palette — flatten to RGB.
                        if thumb_img.mode != 'RGB':
                            thumb_img = thumb_img.convert('RGB')
                        thumb_filename = f"thumb_{filename}"
                        thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                        thumb_img.save(thumb_path, "JPEG")
                except Exception as e:
                    current_app.logger.error(f"Error generating thumbnail: {e}")
                    thumb_filename = None

                try:
                    portrait_path = current_app.photo_processor.process_for_orientation(filepath, 'portrait')
                    if portrait_path:
                        current_app.logger.info(f"Successfully created portrait version: {portrait_path}")
                    else:
                        current_app.logger.error(f"Failed to create portrait version for {filename}")

                    landscape_path = current_app.photo_processor.process_for_orientation(filepath, 'landscape')
                    if landscape_path:
                        current_app.logger.info(f"Successfully created landscape version: {landscape_path}")
                    else:
                        current_app.logger.error(f"Failed to create landscape version for {filename}")
                except Exception as e:
                    current_app.logger.error(f"Error processing image orientations: {e}")
                    portrait_path = None
                    landscape_path = None

                photo = Photo(
                    filename=filename,
                    portrait_version=os.path.basename(portrait_path) if portrait_path else None,
                    landscape_version=os.path.basename(landscape_path) if landscape_path else None,
                    thumbnail=thumb_filename,
                    media_type='video' if is_video else 'photo',
                    duration=duration,
                    heading=request.form.get('heading', ''),
                    exif_metadata=exif_metadata,
                )

            current_app.logger.info(
                f"Saving photo record: filename={filename}, "
                f"portrait={os.path.basename(portrait_path) if portrait_path else None}, "
                f"landscape={os.path.basename(landscape_path) if landscape_path else None}"
            )
            db.session.add(photo)
            db.session.commit()

            target_playlist_id = request.form.get('playlist_id')
            if target_playlist_id:
                try:
                    target_playlist_id = int(target_playlist_id)
                    PlaylistEntry.query.filter_by(playlist_id=target_playlist_id)\
                        .update({PlaylistEntry.order: PlaylistEntry.order + 1})
                    entry = PlaylistEntry(
                        playlist_id=target_playlist_id,
                        photo_id=photo.id,
                        order=0,
                    )
                    db.session.add(entry)
                    db.session.commit()
                except Exception as e:
                    current_app.logger.error(f"Error adding photo to playlist: {e}")
                    if is_api_request:
                        return jsonify({'success': False, 'error': f'Error adding to playlist: {str(e)}'}), 500
            elif frame_id:
                try:
                    frame = db.session.get(PhotoFrame, frame_id)
                    if frame and frame.playlist_id:
                        PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id)\
                            .update({PlaylistEntry.order: PlaylistEntry.order + 1})
                        entry = PlaylistEntry(
                            playlist_id=frame.playlist_id,
                            photo_id=photo.id,
                            order=0,
                        )
                        db.session.add(entry)
                        db.session.commit()
                except Exception as e:
                    current_app.logger.error(f"Error adding photo to playlist: {e}")
                    if is_api_request:
                        return jsonify({'success': False, 'error': f'Error adding to playlist: {str(e)}'}), 500

            if is_api_request:
                return jsonify({
                    'success': True,
                    'photo_id': photo.id,
                    'message': 'Photo uploaded successfully',
                })

            flash('Photo uploaded successfully!')
            return redirect(url_for('photos.upload_photo'))
        else:
            if is_api_request:
                return jsonify({'success': False, 'error': 'File type not allowed'}), 400
            flash('File type not allowed.')
            return redirect(request.url)

    # GET request
    photos = Photo.query.order_by(Photo.uploaded_at.desc()).all()

    playlists = Playlist.query.order_by(Playlist.name).all()
    playlists_with_previews = []
    for playlist in playlists:
        recent_entries = playlist.entries.order_by(PlaylistEntry.order.desc()).limit(4).all()
        recent_photos = [entry.photo for entry in recent_entries if entry.photo]
        playlists_with_previews.append({
            'playlist': playlist,
            'recent_photos': recent_photos,
        })

    photo_playlists = {}
    for entry in PlaylistEntry.query.all():
        if entry.photo_id not in photo_playlists:
            photo_playlists[entry.photo_id] = []
        if entry.playlist_id:
            photo_playlists[entry.photo_id].append(entry.playlist_id)

    last_playlist_id = None
    latest_playlist_entry = PlaylistEntry.query.order_by(PlaylistEntry.id.desc()).first()
    if latest_playlist_entry and latest_playlist_entry.playlist_id:
        last_playlist_id = latest_playlist_entry.playlist_id

    return render_template(
        'upload.html',
        photos=photos,
        playlists=playlists_with_previews,
        photo_playlists=photo_playlists,
        last_playlist_id=last_playlist_id,
    )


def _validate_filename(filename):
    """Reject path traversal attempts; return 404 for suspicious filenames."""
    safe = secure_filename(filename)
    if not safe or safe != filename:
        from flask import abort
        abort(404)


@photos_bp.route('/photos/<filename>')
def serve_photo(filename):
    """Serve uploaded photos."""
    _validate_filename(filename)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@photos_bp.route('/photos/thumbnails/<filename>')
def serve_thumbnail(filename):
    """Serve photo thumbnails."""
    _validate_filename(filename)
    return send_from_directory(
        os.path.join(current_app.config['UPLOAD_FOLDER'], 'thumbnails'), filename
    )


@photos_bp.route('/photos/<int:photo_id>/delete', methods=['DELETE'])
def delete_photo(photo_id):
    try:
        photo = Photo.query.get_or_404(photo_id)

        if photo.source == 'plugin':
            return jsonify({'error': 'This photo is managed by a plugin. Delete the plugin instance to remove it.'}), 400

        PlaylistEntry.query.filter_by(photo_id=photo_id).delete()

        files_to_delete = [
            (photo.filename, 'original file'),
            (photo.portrait_version, 'portrait version'),
            (photo.landscape_version, 'landscape version'),
            (photo.thumbnail, 'thumbnail'),
        ]

        for filename, file_type in files_to_delete:
            if filename:
                if file_type == 'thumbnail':
                    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'thumbnails', filename)
                else:
                    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        current_app.logger.debug(f"Deleted {file_type}: {file_path}")
                except Exception as e:
                    current_app.logger.error(f"Error deleting {file_type} at {file_path}: {e}")

        db.session.delete(photo)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Photo and all versions deleted successfully',
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting photo {photo_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@photos_bp.route('/photos/<photo_id>/edit', methods=['POST'])
def edit_photo(photo_id):
    try:
        data = request.json
        photo = Photo.query.get_or_404(photo_id)
        image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], photo.filename)

        if 'heading' in data:
            photo.heading = data['heading']

        if 'dateTime' in data and data['dateTime']:
            if not photo.exif_metadata:
                photo.exif_metadata = {}
            photo.exif_metadata['DateTime'] = data['dateTime']
            photo.exif_metadata['DateTimeOriginal'] = data['dateTime']
            photo.exif_metadata['DateTimeDigitized'] = data['dateTime']

            try:
                date_parts = data['dateTime'].split(' ')[0].split(':')
                time_parts = data['dateTime'].split(' ')[1]

                year = int(date_parts[0])
                month = int(date_parts[1])
                day = int(date_parts[2])

                dt = datetime(year, month, day)
                photo.exif_metadata['formatted_date'] = dt.strftime('%B %d, %Y')

                time_parts = time_parts.split(':')
                hour = int(time_parts[0])
                minute = int(time_parts[1])

                am_pm = 'AM' if hour < 12 else 'PM'
                hour_12 = hour % 12
                if hour_12 == 0:
                    hour_12 = 12

                photo.exif_metadata['formatted_time'] = f"{hour_12}:{minute:02d} {am_pm}"
            except Exception as e:
                print(f"Error formatting date/time: {e}")
        elif 'dateTime' in data and data['dateTime'] is None:
            if photo.exif_metadata:
                for field in ['DateTime', 'DateTimeOriginal', 'DateTimeDigitized', 'formatted_date', 'formatted_time']:
                    if field in photo.exif_metadata:
                        del photo.exif_metadata[field]

        db.session.commit()

        with Image.open(image_path) as img_file:
            img = ImageOps.exif_transpose(img_file)
            exif_data = img.info.get('exif')

            if 'brightness' in data:
                enhancer = ImageEnhance.Brightness(img)
                img = enhancer.enhance(float(data['brightness']))

            if 'contrast' in data:
                enhancer = ImageEnhance.Contrast(img)
                img = enhancer.enhance(float(data['contrast']))

            if 'saturation' in data:
                enhancer = ImageEnhance.Color(img)
                img = enhancer.enhance(float(data['saturation']))

            if 'sharpness' in data:
                enhancer = ImageEnhance.Sharpness(img)
                img = enhancer.enhance(float(data['sharpness']))

            if 'rotation' in data and data['rotation'] != 0:
                img = img.rotate(float(data['rotation']), expand=True)

            try:
                img.save(image_path, quality=95, exif=exif_data)
            except Exception:
                img.save(image_path, quality=95)

            thumbnails_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'thumbnails')
            os.makedirs(thumbnails_dir, exist_ok=True)

            if hasattr(photo, 'thumbnail') and photo.thumbnail:
                thumb_path = os.path.join(thumbnails_dir, photo.thumbnail)
                thumb_img = img.copy()
                thumb_img = ImageOps.exif_transpose(thumb_img)
                thumb_img.thumbnail((400, 400))
                try:
                    thumb_img.save(thumb_path, quality=85, exif=exif_data)
                except Exception:
                    thumb_img.save(thumb_path, quality=85)

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error editing photo: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


# ---------------------------------------------------------------------------
# Photo API routes
# ---------------------------------------------------------------------------

@photos_bp.route('/api/gallery/add', methods=['POST'])
def add_to_gallery():
    try:
        data = request.json
        if not data or 'image' not in data:
            logger.error("No image data provided in request")
            return jsonify({'success': False, 'error': 'No image data provided'}), 400

        base64_image = data['image']
        image_data = base64.b64decode(
            base64_image.split(',')[1] if ',' in base64_image else base64_image
        )

        filename = f"gallery_{secrets.token_hex(8)}.jpg"

        if allowed_file(filename):
            thumbnails_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'thumbnails')
            os.makedirs(thumbnails_dir, exist_ok=True)

            filename = secure_filename(filename)
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            with open(filepath, 'wb') as f:
                f.write(image_data)

            try:
                with Image.open(filepath) as img:
                    img = ImageOps.exif_transpose(img)
                    img.thumbnail((400, 400))
                    # JPEG can't encode alpha/palette — flatten to RGB.
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    thumb_filename = f"thumb_{filename}"
                    thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                    img.save(thumb_path, "JPEG")
            except Exception as e:
                current_app.logger.error(f"Error generating thumbnail: {e}")
                thumb_filename = None

            portrait_path = current_app.photo_processor.process_for_orientation(filepath, 'portrait')
            landscape_path = current_app.photo_processor.process_for_orientation(filepath, 'landscape')

            photo = Photo(
                filename=filename,
                portrait_version=os.path.basename(portrait_path) if portrait_path else None,
                landscape_version=os.path.basename(landscape_path) if landscape_path else None,
                thumbnail=thumb_filename,
            )
            db.session.add(photo)
            db.session.commit()

            return jsonify({
                'success': True,
                'message': 'Image added to gallery and database successfully',
                'filename': filename,
            })

    except Exception as e:
        logger.error(f"Error adding image to gallery: {str(e)}")
        logger.exception("Full traceback:")
        return jsonify({
            'success': False,
            'error': f'Failed to add image to gallery: {str(e)}',
        }), 500


@photos_bp.route('/api/playlist/add', methods=['POST'])
def add_to_playlist():
    try:
        data = request.get_json()
        frame_id = data.get('frame_id')
        photo_id = data.get('photo_id')

        if not frame_id or not photo_id:
            return jsonify({'error': 'Missing frame_id or photo_id'}), 400

        frame = PhotoFrame.query.get(frame_id)
        photo = Photo.query.get(photo_id)

        if not frame or not photo:
            return jsonify({'error': 'Frame or photo not found'}), 404

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

        existing_entry = PlaylistEntry.query.filter_by(
            playlist_id=frame.playlist_id,
            photo_id=photo_id,
        ).first()

        if existing_entry:
            current_order = existing_entry.order
            db.session.query(PlaylistEntry)\
                .filter(PlaylistEntry.playlist_id == frame.playlist_id)\
                .filter(PlaylistEntry.order < current_order)\
                .update({PlaylistEntry.order: PlaylistEntry.order + 1})
            existing_entry.order = 0
            db.session.commit()

            if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
                current_app.mqtt_integration.update_frame_options(frame)

            return jsonify({
                'success': True,
                'message': f"Photo moved to the top of {frame.name or frame.id}'s playlist",
            })

        db.session.query(PlaylistEntry)\
            .filter_by(playlist_id=frame.playlist_id)\
            .update({PlaylistEntry.order: PlaylistEntry.order + 1})

        playlist_entry = PlaylistEntry(
            playlist_id=frame.playlist_id,
            photo_id=photo_id,
            order=0,
        )
        db.session.add(playlist_entry)
        db.session.commit()

        if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
            current_app.mqtt_integration.update_frame_options(frame)

        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@photos_bp.route('/api/photos')
def get_all_photos():
    """Get all photos for the photo selector."""
    photos = Photo.query.order_by(Photo.uploaded_at.desc()).all()
    return jsonify([{
        'id': photo.id,
        'filename': photo.filename,
        'thumbnail': photo.thumbnail,
    } for photo in photos])


@photos_bp.route('/api/photos/<int:photo_id>', methods=['GET'])
def api_get_photo(photo_id):
    """Get a single photo's details."""
    try:
        photo = Photo.query.get_or_404(photo_id)
        return jsonify({
            'id': photo.id,
            'filename': photo.filename,
            'portrait_version': photo.portrait_version,
            'landscape_version': photo.landscape_version,
            'thumbnail': photo.thumbnail,
            'media_type': photo.media_type,
        })
    except Exception as e:
        current_app.logger.error(f"Error fetching photo {photo_id}: {e}")
        return jsonify({'error': str(e)}), 500


@photos_bp.route('/api/photos/<int:photo_id>/regenerate', methods=['POST'])
def api_regenerate_photo(photo_id):
    """Regenerate portrait/landscape derivatives and thumbnail from the original.

    Applies the current processing pipeline (including smart crop) to an already
    uploaded photo. Useful for re-cropping existing photos after processing
    changes land.
    """
    try:
        photo = Photo.query.get_or_404(photo_id)

        if photo.source == 'plugin':
            return jsonify({'error': 'Plugin-managed photos cannot be regenerated here. Re-run the plugin instead.'}), 400
        if photo.media_type == 'video':
            return jsonify({'error': 'Regeneration is only supported for photos, not videos.'}), 400

        upload_folder = current_app.config['UPLOAD_FOLDER']
        thumbnails_dir = os.path.join(upload_folder, 'thumbnails')
        os.makedirs(thumbnails_dir, exist_ok=True)

        original_path = os.path.join(upload_folder, photo.filename)
        if not os.path.isfile(original_path):
            return jsonify({'error': f'Original file not found on disk: {photo.filename}'}), 404

        thumb_filename = f"thumb_{photo.filename}"
        try:
            with Image.open(original_path) as img:
                normalized_img = ImageOps.exif_transpose(img)
                thumb_img = normalized_img.copy()
                thumb_img.thumbnail((400, 400))
                # JPEG can't encode alpha/palette — flatten to RGB.
                if thumb_img.mode != 'RGB':
                    thumb_img = thumb_img.convert('RGB')
                thumb_img.save(os.path.join(thumbnails_dir, thumb_filename), "JPEG")
        except Exception as e:
            current_app.logger.error(f"Error regenerating thumbnail for photo {photo_id}: {e}")
            return jsonify({'error': f'Thumbnail generation failed: {e}'}), 500

        portrait_path = current_app.photo_processor.process_for_orientation(original_path, 'portrait')
        landscape_path = current_app.photo_processor.process_for_orientation(original_path, 'landscape')
        if not portrait_path or not landscape_path:
            return jsonify({'error': 'Orientation variant generation failed; check server logs.'}), 500

        photo.portrait_version = os.path.basename(portrait_path)
        photo.landscape_version = os.path.basename(landscape_path)
        photo.thumbnail = thumb_filename
        db.session.commit()

        return jsonify({
            'success': True,
            'id': photo.id,
            'filename': photo.filename,
            'portrait_version': photo.portrait_version,
            'landscape_version': photo.landscape_version,
            'thumbnail': photo.thumbnail,
        })
    except Exception as e:
        current_app.logger.error(f"Error regenerating photo {photo_id}: {e}")
        current_app.logger.exception("Full traceback:")
        return jsonify({'error': str(e)}), 500


@photos_bp.route('/api/photos/<int:photo_id>', methods=['DELETE'])
def api_delete_photo(photo_id):
    """Delete a photo and all its file versions."""
    try:
        photo = Photo.query.get_or_404(photo_id)

        if photo.source == 'plugin':
            return jsonify({'error': 'This photo is managed by a plugin. Delete the plugin instance to remove it.'}), 400

        PlaylistEntry.query.filter_by(photo_id=photo_id).delete()

        upload_folder = current_app.config['UPLOAD_FOLDER']
        files_to_delete = [
            (photo.filename, upload_folder),
            (photo.portrait_version, upload_folder),
            (photo.landscape_version, upload_folder),
            (photo.thumbnail, os.path.join(upload_folder, 'thumbnails')),
        ]

        for filename, folder in files_to_delete:
            if filename:
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        current_app.logger.info(f"Deleted file: {file_path}")
                except Exception as e:
                    current_app.logger.error(f"Error deleting file {file_path}: {e}")

        db.session.delete(photo)
        db.session.commit()

        return jsonify({'success': True, 'message': 'Photo deleted successfully'})

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting photo {photo_id}: {e}")
        return jsonify({'error': str(e)}), 500

