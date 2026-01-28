from datetime import datetime, timedelta, timezone
import pytz
from zoneinfo import ZoneInfo
import math
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
    ai_description = db.Column(JSON)
    ai_analyzed_at = db.Column(db.DateTime)
    media_type = db.Column(db.String(10), default='photo')  # 'photo' or 'video'
    duration = db.Column(db.Float)
    exif_metadata = db.Column(JSON)

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
    sync_group_id = db.Column(db.Integer, db.ForeignKey('sync_group.id'))
    shuffle_enabled = db.Column(db.Boolean, default=False)
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

    # Device properties
    manufacturer = db.Column(db.String(256))
    model = db.Column(db.String(256))
    hardware_rev = db.Column(db.String(256))
    firmware_rev = db.Column(db.String(256))
    screen_resolution = db.Column(db.String(256))
    aspect_ratio = db.Column(db.String(256))
    os = db.Column(db.String(256))
    capabilities = db.Column(JSON)

    # Dynamic playlist fields
    dynamic_playlist_prompt = db.Column(db.Text)
    dynamic_playlist_active = db.Column(db.Boolean, default=False)
    dynamic_playlist_model = db.Column(db.String(100))
    dynamic_playlist_updated_at = db.Column(db.DateTime)

    # Overlay preferences
    overlay_preferences = db.Column(db.Text, default='{"weather": false, "metadata": false, "qrcode": false}')

    # Relationships
    current_photo = db.relationship('Photo', foreign_keys=[current_photo_id])
    playlist_entries = db.relationship('PlaylistEntry', backref='frame', lazy='dynamic', order_by='PlaylistEntry.order')
    scheduled_generations = db.relationship('ScheduledGeneration', backref='frame', lazy='dynamic')

    # Timestamps
    last_sync_time = db.Column(db.DateTime) # For sync group tracking
    diagnostics = db.Column(JSON)  # Add this line to store diagnostic data

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
    frame_id = db.Column(db.String(50), db.ForeignKey('photo_frame.id'), nullable=True) # FK to PhotoFrame
    photo_id = db.Column(db.Integer, db.ForeignKey('photo.id'), nullable=False) # FK to Photo
    order = db.Column(db.Integer, nullable=False)
    date_added = db.Column(db.DateTime, default=datetime.utcnow)
    custom_playlist_id = db.Column(db.Integer, db.ForeignKey('custom_playlist.id'), nullable=True) # FK to CustomPlaylist

    # Relationships defined via backref in PhotoFrame and CustomPlaylist

    def __repr__(self):
        playlist_type = f"Frame {self.frame_id}" if self.frame_id else f"CustomPlaylist {self.custom_playlist_id}"
        return f"<PlaylistEntry {self.id} photo={self.photo_id} order={self.order} in {playlist_type}>"

class CustomPlaylist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship to playlist entries
    entries = db.relationship('PlaylistEntry',
                            backref='custom_playlist',
                            lazy='dynamic',
                            cascade='all, delete-orphan',
                            order_by='PlaylistEntry.order')

    def __repr__(self):
        return f'<CustomPlaylist {self.id}: {self.name}>'

class ScheduledGeneration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False)
    prompt = db.Column(db.Text, nullable=False) # Query for Unsplash/Pixabay, Prompt for AI
    frame_id = db.Column(db.String(50), db.ForeignKey('photo_frame.id'), nullable=False)
    service = db.Column(db.String(50), nullable=False) # 'dalle', 'stability', 'unsplash', 'pixabay', 'custom_playlist'
    model = db.Column(db.String(100), nullable=False) # AI Model name, 'unsplash', 'pixabay', or CustomPlaylist ID
    orientation = db.Column(db.String(20), default='portrait') # 'portrait', 'landscape', 'square', etc.
    style_preset = db.Column(db.Text) # JSON for AI style, Unsplash/Pixabay params
    cron_expression = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship defined via backref in PhotoFrame

class GenerationHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey('scheduled_generation.id'))
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    success = db.Column(db.Boolean, default=True)
    error_message = db.Column(db.Text)
    photo_id = db.Column(db.Integer, db.ForeignKey('photo.id')) # ID of the generated/added photo
    name = db.Column(db.String(256)) # Name of the schedule at time of generation

    schedule = db.relationship('ScheduledGeneration')
    photo = db.relationship('Photo')

class SyncGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False, unique=True)
    sleep_interval = db.Column(db.Float, default=5.0) # Default interval for the group
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    frames = db.relationship('PhotoFrame', backref='sync_group', lazy=True)

    def get_next_sync_time(self, after=None):
        """Calculate the next sync point based on the group interval (UTC)."""
        now_utc = datetime.now(timezone.utc)
        base_time = after if after else now_utc
        if base_time.tzinfo is None: # Ensure base_time is timezone-aware UTC
             base_time = pytz.UTC.localize(base_time)
        else:
             base_time = base_time.astimezone(pytz.UTC)

        interval_seconds = self.sleep_interval * 60
        epoch_seconds = base_time.timestamp()

        # Find the next interval boundary from UTC epoch
        next_boundary_seconds = math.ceil(epoch_seconds / interval_seconds) * interval_seconds

        # Convert back to naive UTC datetime for database/comparison consistency
        next_sync_naive_utc = datetime.fromtimestamp(next_boundary_seconds, tz=timezone.utc).replace(tzinfo=None)

        logger.debug(f"Group {self.id} sync calc: Base={base_time.isoformat()}, Interval={self.sleep_interval}m, NextSync={next_sync_naive_utc.isoformat()}Z")
        return next_sync_naive_utc 

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