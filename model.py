from datetime import datetime, timedelta, timezone
import pytz
from zoneinfo import ZoneInfo
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import JSON
import logging

logger = logging.getLogger(__name__)

# Create the SQLAlchemy instance
db = SQLAlchemy()

def init_db(app):
    """Initialize the database with the Flask app"""
    db.init_app(app)

def is_in_deep_sleep(frame, current_time):
    """Check if frame is in deep sleep based on UTC hours."""
    if not frame.deep_sleep_enabled or frame.deep_sleep_start is None or frame.deep_sleep_end is None:
        return False
    
    current_hour = current_time.hour
    
    # Handle cases where sleep period crosses midnight
    if frame.deep_sleep_start > frame.deep_sleep_end:
        return current_hour >= frame.deep_sleep_start or current_hour < frame.deep_sleep_end
    else:
        return frame.deep_sleep_start <= current_hour < frame.deep_sleep_end

class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(256), nullable=False)
    portrait_version = db.Column(db.String(256))
    landscape_version = db.Column(db.String(256))
    thumbnail = db.Column(db.String(256))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    heading = db.Column(db.Text)
    media_type = db.Column(db.String(10), default='photo')  # 'photo' or 'video'
    duration = db.Column(db.Float)
    exif_metadata = db.Column(JSON)
    source = db.Column(db.String(50), nullable=True, default=None)  # None=normal, 'plugin'=owned by plugin

    playlist_entries = db.relationship('PlaylistEntry', backref='photo', lazy='dynamic')

    def __repr__(self):
        return f"<Photo {self.id}: {self.filename}>"

class PhotoFrame(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    name = db.Column(db.String(100))
    order = db.Column(db.Integer, default=0)
    sleep_interval = db.Column(db.Float, default=5.0)
    orientation = db.Column(db.String(20), default='portrait')
    battery_level = db.Column(db.Float)
    last_wake_time = db.Column(db.DateTime)
    next_wake_time = db.Column(db.DateTime)
    last_diagnostic = db.Column(db.DateTime)
    current_photo_id = db.Column(db.Integer, db.ForeignKey('photo.id'))
    shuffle_enabled = db.Column(db.Boolean, default=False)
    snap_to_hour = db.Column(db.Boolean, default=False)  # Align photo changes to clock hours when interval >= 60 min
    deep_sleep_enabled = db.Column(db.Boolean, default=False)
    deep_sleep_start = db.Column(db.Integer) # Hour in UTC (0-23)
    deep_sleep_end = db.Column(db.Integer)   # Hour in UTC (0-23)
    frame_type = db.Column(db.String(20), default='physical') # 'physical' or 'virtual'

    # Image settings
    contrast_factor = db.Column(db.Float, default=1.0)
    saturation = db.Column(db.Integer, default=100)
    blue_adjustment = db.Column(db.Integer, default=0)
    padding = db.Column(db.Integer, default=0)
    color_map = db.Column(JSON)
    overshoot_guard_enabled = db.Column(db.Boolean, default=True)

    # Device properties
    manufacturer = db.Column(db.String(256))
    model = db.Column(db.String(256))
    hardware_rev = db.Column(db.String(256))
    firmware_rev = db.Column(db.String(256))
    screen_resolution = db.Column(db.String(256))
    aspect_ratio = db.Column(db.String(256))
    os = db.Column(db.String(256))
    capabilities = db.Column(JSON)

    # Playlist assignment - frames reference a playlist (N:1)
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlist.id'), nullable=True)

    # Relationships
    current_photo = db.relationship('Photo', foreign_keys=[current_photo_id])
    playlist = db.relationship('Playlist', back_populates='frames')
    diagnostics = db.Column(JSON)  # Add this line to store diagnostic data

    @property
    def playlist_entries(self):
        """Backward-compatible property to get playlist entries via the assigned playlist."""
        if self.playlist:
            return self.playlist.entries
        # Return empty query for backward compatibility
        return PlaylistEntry.query.filter(False)

    def __repr__(self):
        return f"<PhotoFrame {self.id}: {self.name}>"

    def get_status(self, current_time=None):
        """Get frame status based on wake times and deep sleep settings."""
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        if not self.last_wake_time:
            return (0, "Never Connected", "#dc3545")  # Red

        # Ensure times are timezone-aware in UTC
        last_wake = self.last_wake_time.replace(tzinfo=pytz.UTC) if self.last_wake_time.tzinfo is None else self.last_wake_time.astimezone(pytz.UTC)
        current_time = current_time.replace(tzinfo=pytz.UTC) if current_time.tzinfo is None else current_time.astimezone(pytz.UTC)

        # Check deep sleep first (uses UTC hours stored in DB)
        if is_in_deep_sleep(self, current_time):
             return (3, "In Deep Sleep", "#6f42c1") # Purple

        # Calculate time since last wake
        time_since_wake = current_time - last_wake

        # If device connected recently, it's online
        if time_since_wake <= timedelta(minutes=5):
            return (2, "Online", "#28a745")  # Green

        # Check if we're in the expected wake window based on next_wake_time
        if self.next_wake_time:
            next_wake = self.next_wake_time.replace(tzinfo=pytz.UTC) if self.next_wake_time.tzinfo is None else self.next_wake_time.astimezone(pytz.UTC)
            wake_window_start = next_wake - timedelta(minutes=2)
            wake_window_end = next_wake + timedelta(minutes=2)

            if wake_window_start <= current_time <= wake_window_end:
                return (1, "Sleeping", "#ffc107")  # Yellow

            # If we've missed the wake window significantly
            if current_time > wake_window_end + timedelta(minutes=10):
                return (0, "Offline", "#dc3545")  # Red

        # Fallback check based on sleep_interval if next_wake_time is unreliable/missing
        expected_wake_based_on_interval = last_wake + timedelta(minutes=self.sleep_interval)
        wake_window_end_based_on_interval = expected_wake_based_on_interval + timedelta(minutes=2)

        if current_time <= wake_window_end_based_on_interval:
             return (1, "Sleeping", "#ffc107") # Yellow

        # If significantly past the expected interval-based wake time
        if current_time > wake_window_end_based_on_interval + timedelta(minutes=10):
            return (0, "Offline", "#dc3545") # Red

        # Default to sleeping if none of the above conditions met strongly
        return (1, "Sleeping", "#ffc107") # Yellow

class PlaylistEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    photo_id = db.Column(db.Integer, db.ForeignKey('photo.id'), nullable=False) # FK to Photo
    order = db.Column(db.Integer, nullable=False)
    date_added = db.Column(db.DateTime, default=datetime.utcnow)
    # Playlist reference - entries belong to playlists
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlist.id'), nullable=True)

    # Relationships defined via backref in Playlist

    def __repr__(self):
        return f"<PlaylistEntry {self.id} photo={self.photo_id} order={self.order} in Playlist {self.playlist_id}>"


class Playlist(db.Model):
    """Playlist model - photos are organized into playlists, which are assigned to frames."""
    __tablename__ = 'playlist'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship to playlist entries
    entries = db.relationship('PlaylistEntry',
                            backref='playlist',
                            lazy='dynamic',
                            cascade='all, delete-orphan',
                            order_by='PlaylistEntry.order')
    
    # Relationship to frames using this playlist
    frames = db.relationship('PhotoFrame', back_populates='playlist')

    def __repr__(self):
        return f'<Playlist {self.id}: {self.name}>'


# Alias for backward compatibility with existing code
CustomPlaylist = Playlist

class EventLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    frame_id = db.Column(db.String(50), db.ForeignKey('photo_frame.id'), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)  # connection, photo_request, diagnostic, error, playlist_change
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    source = db.Column(db.String(50), nullable=False)  # user, mqtt, system, frame, etc.
    details = db.Column(JSON)  # Store additional event-specific information
    
    # Relationship with PhotoFrame
    frame = db.relationship('PhotoFrame', backref=db.backref('events', lazy='dynamic'))
    
    def __repr__(self):
        return f"<EventLog {self.id}: {self.event_type} for {self.frame_id}>"


class PluginInstance(db.Model):
    """A configured instance of a developer plugin."""
    __tablename__ = 'plugin_instance'

    id          = db.Column(db.Integer, primary_key=True)
    plugin_id   = db.Column(db.String(100), nullable=False)   # matches plugins/ directory name
    name        = db.Column(db.String(256), nullable=False)   # user-chosen label
    config      = db.Column(JSON, nullable=False, default=dict)  # credentials, settings
    cron        = db.Column(db.String(100), nullable=False, default='0 * * * *')
    enabled     = db.Column(db.Boolean, default=True, nullable=False)
    photo_id    = db.Column(db.Integer, db.ForeignKey('photo.id'), nullable=True, unique=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    last_run_at = db.Column(db.DateTime)
    last_run_ok = db.Column(db.Boolean)   # None = never run
    last_error  = db.Column(db.Text)

    photo    = db.relationship('Photo', foreign_keys=[photo_id], backref=db.backref('plugin_instance', uselist=False))
    run_logs = db.relationship('PluginRunLog', backref='instance', cascade='all, delete-orphan',
                               order_by='PluginRunLog.ran_at.desc()')

    def __repr__(self):
        return f"<PluginInstance {self.id}: {self.plugin_id} '{self.name}'>"


class PluginRunLog(db.Model):
    """Execution history for a plugin instance."""
    __tablename__ = 'plugin_run_log'

    id          = db.Column(db.Integer, primary_key=True)
    instance_id = db.Column(db.Integer, db.ForeignKey('plugin_instance.id', ondelete='CASCADE'), nullable=False)
    ran_at      = db.Column(db.DateTime, default=datetime.utcnow)
    success     = db.Column(db.Boolean, nullable=False)
    error       = db.Column(db.Text)
    duration_ms = db.Column(db.Integer)

    def __repr__(self):
        return f"<PluginRunLog {self.id}: instance={self.instance_id} ok={self.success}>" 