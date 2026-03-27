import random
import logging
import pytz
import humanize
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def format_relative_time(dt, current_time=None, timezone_name='UTC'):
    """Format a datetime as a human-readable relative time string."""
    if not dt:
        return "Never"
    try:
        # Ensure dt is timezone-aware (assume UTC if naive)
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)

        # Get current time, ensure it's timezone-aware
        if current_time is None:
            current_time = datetime.now(timezone.utc)
        elif current_time.tzinfo is None:
            current_time = pytz.UTC.localize(current_time) # Assume current_time is UTC if naive

        # Convert both to the target timezone for comparison
        target_tz = pytz.timezone(timezone_name)
        dt_local = dt.astimezone(target_tz)
        current_time_local = current_time.astimezone(target_tz)

        # Use humanize for relative time
        return humanize.naturaltime(current_time_local - dt_local) # Pass timedelta to naturaltime

    except Exception as e:
        logger.error(f"Error formatting relative time for {dt} in TZ {timezone_name}: {e}")
        # Fallback to ISO format in UTC
        return dt.astimezone(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S %Z')


def is_in_deep_sleep(frame, current_time_utc=None):
    """Check if a frame is currently in its deep sleep window (using UTC)."""
    if not frame.deep_sleep_enabled or frame.deep_sleep_start is None or frame.deep_sleep_end is None:
        return False

    if current_time_utc is None:
        current_time_utc = datetime.now(timezone.utc)
    elif current_time_utc.tzinfo is None:
         current_time_utc = pytz.UTC.localize(current_time_utc) # Assume UTC if naive
    else:
         current_time_utc = current_time_utc.astimezone(pytz.UTC)

    current_hour = current_time_utc.hour
    start = frame.deep_sleep_start # Stored as UTC hour
    end = frame.deep_sleep_end     # Stored as UTC hour

    if start < end:  # e.g., 1:00 to 6:00 UTC
        return start <= current_hour < end
    else:            # Crosses midnight UTC e.g., 22:00 to 6:00 UTC
        return current_hour >= start or current_hour < end


def calculate_sleep_interval(frame, current_time_utc=None):
    """Calculate the effective sleep interval considering deep sleep and snap-to-hour (in minutes, UTC)."""
    if current_time_utc is None:
        current_time_utc = datetime.now(timezone.utc)
    elif current_time_utc.tzinfo is None:
         current_time_utc = pytz.UTC.localize(current_time_utc)
    else:
         current_time_utc = current_time_utc.astimezone(pytz.UTC)

    base_interval = frame.sleep_interval

    if frame.deep_sleep_enabled and frame.deep_sleep_start is not None and frame.deep_sleep_end is not None:
        # Check if currently in deep sleep
        if is_in_deep_sleep(frame, current_time_utc):
            # Calculate time until deep sleep ends
            end_time_today = current_time_utc.replace(hour=frame.deep_sleep_end, minute=0, second=0, microsecond=0)
            if end_time_today <= current_time_utc: # If end time is in the past today, it's tomorrow
                end_time = end_time_today + timedelta(days=1)
            else:
                end_time = end_time_today
            minutes_to_sleep = (end_time - current_time_utc).total_seconds() / 60.0
            logger.debug(f"Frame {frame.id} in deep sleep. Sleeping for {minutes_to_sleep:.1f} mins until {end_time.isoformat()}.")
            return max(minutes_to_sleep, base_interval) # Ensure we sleep at least the base interval

        # Check if the *next* wake-up would fall into deep sleep
        next_normal_wake = current_time_utc + timedelta(minutes=base_interval)
        if is_in_deep_sleep(frame, next_normal_wake):
            # Calculate time until deep sleep ends from *now*
            end_time_today = next_normal_wake.replace(hour=frame.deep_sleep_end, minute=0, second=0, microsecond=0)
            if end_time_today <= next_normal_wake: # If end time is past the wake time, it's the next day's end time
                 end_time = end_time_today + timedelta(days=1)
            else:
                 end_time = end_time_today
            minutes_to_sleep = (end_time - current_time_utc).total_seconds() / 60.0
            logger.debug(f"Frame {frame.id} next wake is in deep sleep. Sleeping for {minutes_to_sleep:.1f} mins until {end_time.isoformat()}.")
            return max(minutes_to_sleep, base_interval)

    # Handle snap-to-hour when interval is 60+ minutes
    if getattr(frame, 'snap_to_hour', False) and base_interval >= 60:
        interval_hours = base_interval / 60.0
        # Round to nearest integer hour for alignment (e.g., 60->1, 180->3, 1440->24)
        interval_hours = max(1, round(interval_hours))

        # Calculate next aligned hour
        current_hour = current_time_utc.hour
        # Find next hour that aligns with the interval (e.g., for 3-hour: 0, 3, 6, 9...)
        next_aligned_hour = ((current_hour // interval_hours) + 1) * interval_hours

        # Calculate the target time
        target_time = current_time_utc.replace(minute=0, second=0, microsecond=0)
        if next_aligned_hour >= 24:
            # Wraps to next day
            days_ahead = next_aligned_hour // 24
            target_time = target_time + timedelta(days=days_ahead)
            target_time = target_time.replace(hour=next_aligned_hour % 24)
        else:
            target_time = target_time.replace(hour=next_aligned_hour)

        minutes_to_aligned = (target_time - current_time_utc).total_seconds() / 60.0

        # Ensure we don't return a negative or zero interval
        if minutes_to_aligned <= 0:
            minutes_to_aligned += interval_hours * 60

        logger.debug(f"Frame {frame.id} snap-to-hour enabled. Next aligned time: {target_time.isoformat()}, sleeping {minutes_to_aligned:.1f} mins.")
        return minutes_to_aligned

    # Not in deep sleep, and next wake is not in deep sleep
    return base_interval


class PhotoHelper:
    """Class to encapsulate static methods related to photo retrieval for frames."""
    @staticmethod
    def get_current_photo_filename(frame_id):
        from model import db, Photo, PhotoFrame, PlaylistEntry
        frame = db.session.get(PhotoFrame, frame_id)
        if frame and frame.current_photo_id:
            photo = db.session.get(Photo, frame.current_photo_id)
            if photo:
                # Return appropriate version based on orientation
                if frame.orientation == 'portrait' and photo.portrait_version: return photo.portrait_version
                if frame.orientation == 'landscape' and photo.landscape_version: return photo.landscape_version
                return photo.filename
        elif frame: # Frame exists but no current photo set, try first in playlist
            entry = frame.playlist_entries.order_by(PlaylistEntry.order).first()
            if entry and entry.photo:
                photo = entry.photo
                if frame.orientation == 'portrait' and photo.portrait_version: return photo.portrait_version
                if frame.orientation == 'landscape' and photo.landscape_version: return photo.landscape_version
                return photo.filename
        return None

    @staticmethod
    def get_next_photo_filename(frame_id):
        from model import db, Photo, PhotoFrame, PlaylistEntry
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame: return None

        playlist = frame.playlist_entries.order_by(PlaylistEntry.order).all()
        if not playlist: return None

        current_photo_id = frame.current_photo_id
        next_photo = None

        if frame.shuffle_enabled:
             # Simple shuffle: pick a random one *not* the current one, if possible
             possible_next = [p for p in playlist if p.photo_id != current_photo_id]
             if not possible_next and len(playlist) == 1: # Only one photo
                 next_photo = playlist[0].photo
             elif possible_next:
                 next_photo = random.choice(possible_next).photo
             else: # If current_photo_id was somehow invalid, pick any random
                 next_photo = random.choice(playlist).photo
        else:
             # Sequential: find current, get next (looping)
             current_index = -1
             if current_photo_id:
                 for i, entry in enumerate(playlist):
                     if entry.photo_id == current_photo_id:
                         current_index = i
                         break
             next_index = (current_index + 1) % len(playlist)
             next_photo = playlist[next_index].photo

        if next_photo:
            # Return appropriate version based on orientation
            if frame.orientation == 'portrait' and next_photo.portrait_version: return next_photo.portrait_version
            if frame.orientation == 'landscape' and next_photo.landscape_version: return next_photo.landscape_version
            return next_photo.filename
        return None

    @staticmethod
    def get_current_photo(frame_id):
        """Return the current Photo object for a frame (for template use)."""
        from model import db, Photo, PhotoFrame, PlaylistEntry
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return None
        if frame.current_photo_id:
            photo = db.session.get(Photo, frame.current_photo_id)
            if photo:
                return photo
        # Fall back to first playlist entry
        entry = frame.playlist_entries.order_by(PlaylistEntry.order).first()
        return entry.photo if entry else None

    @staticmethod
    def get_next_photo(frame_id):
        """Return the next Photo object for a frame (for template use)."""
        from model import db, PhotoFrame, PlaylistEntry
        frame = db.session.get(PhotoFrame, frame_id)
        if not frame:
            return None
        playlist = frame.playlist_entries.order_by(PlaylistEntry.order).all()
        if not playlist:
            return None
        current_photo_id = frame.current_photo_id
        if frame.shuffle_enabled:
            possible_next = [p for p in playlist if p.photo_id != current_photo_id]
            if possible_next:
                return random.choice(possible_next).photo
            return random.choice(playlist).photo
        # Sequential: find current entry, return the next one
        current_index = -1
        if current_photo_id:
            for i, entry in enumerate(playlist):
                if entry.photo_id == current_photo_id:
                    current_index = i
                    break
        next_index = (current_index + 1) % len(playlist)
        return playlist[next_index].photo

    @staticmethod
    def get_photo_object_by_id(photo_id):
        from model import db, Photo
        return db.session.get(Photo, photo_id)


def add_photo_to_frame_playlist(photo_id, frame_id):
    """Adds a photo to the beginning of a specific frame's playlist."""
    from model import db, Photo, PhotoFrame, PlaylistEntry, Playlist, EventLog
    from flask import current_app
    from datetime import datetime
    try:
        # Check if frame and photo exist
        frame = db.session.get(PhotoFrame, frame_id)
        photo = db.session.get(Photo, photo_id)
        if not frame or not photo:
            logger.error(f"Cannot add photo to playlist: Frame {frame_id} or Photo {photo_id} not found.")
            return False, "Frame or Photo not found."

        # Ensure frame has a playlist
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

        # Shift existing entries' order down by 1
        PlaylistEntry.query.filter_by(playlist_id=frame.playlist_id).update({
            PlaylistEntry.order: PlaylistEntry.order + 1
        })

        # Add the new photo at the beginning (order 0)
        new_entry = PlaylistEntry(
            playlist_id=frame.playlist_id,
            photo_id=photo.id,
            order=0,
            date_added=datetime.utcnow()
        )
        db.session.add(new_entry)
        db.session.commit()
        logger.info(f"Added photo {photo_id} to start of playlist for frame {frame_id}")

        # Trigger MQTT update if enabled
        if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
            current_app.mqtt_integration.update_frame_options(frame)

        return True, "Photo added to playlist."
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding photo {photo_id} to playlist for frame {frame_id}: {e}")
        return False, f"Error adding to playlist: {e}"
