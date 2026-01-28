import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__) 

class FrameTimingManager:
    """
    Manages the timing of virtual frame transitions on the server side.
    This class runs a background thread that periodically checks for frames
    that need to transition to the next photo and updates their state.
    """
    
    def __init__(self, app, db, models):
        """
        Initialize the frame timing manager.
        
        Args:
            app: Flask application instance
            db: SQLAlchemy database instance
            models: Dictionary containing database models
        """
        self.app = app
        self.db = db
        self.PhotoFrame = models['PhotoFrame']
        self.Photo = models['Photo']
        self.PlaylistEntry = models['PlaylistEntry']
        
        self.running = False
        self.thread = None
        self.check_interval = 1.0  # Check every second
        
        # Create a separate database session for the background thread
        self.engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
        self.Session = sessionmaker(bind=self.engine)
        
        logger.debug("Frame Timing Manager initialized")
    
    def start(self):
        """Start the background thread for managing frame timing."""
        if self.running:
            logger.debug("Frame Timing Manager is already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._timing_thread, daemon=True)
        self.thread.start()
        logger.info("Frame Timing Manager started")
    
    def stop(self):
        """Stop the background thread."""
        if not self.running:
            logger.debug("Frame Timing Manager is not running")
            return
        
        self.running = False
        if self.thread:
            self.thread.join(timeout=5.0)
            logger.info("Frame Timing Manager stopped")
    
    def _timing_thread(self):
        """Background thread that checks for frames that need to transition."""
        logger.debug("Frame timing thread started")
        
        while self.running:
            try:
                self._check_frames()
            except Exception as e:
                logger.error(f"Error in frame timing thread: {str(e)}", exc_info=True)
            
            # Sleep for the check interval
            time.sleep(self.check_interval)
    
    def _ensure_aware(self, dt):
        """
        Ensure a datetime is timezone-aware by adding UTC timezone if needed.
        
        Args:
            dt: A datetime object
            
        Returns:
            A timezone-aware datetime object
        """
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            # Assume naive datetime is in UTC
            return dt.replace(tzinfo=timezone.utc)
        return dt
    
    def _check_frames(self):
        """Check all virtual frames for needed transitions."""
        # Create a new session for this check
        session = self.Session()
        try:
            # Get current time in UTC
            now = datetime.now(timezone.utc)
            
            # Instead of filtering by datetime in the query, get all virtual frames
            # and filter them in Python where we have more control over timezone handling
            frames_to_update = []
            
            try:
                # Fetch all virtual frames regardless of wake time
                all_frames = session.query(self.PhotoFrame).filter(
                    self.PhotoFrame.frame_type == 'virtual'
                ).all()
                
                # Process each frame manually to handle timezone issues
                for frame in all_frames:
                    if not frame.next_wake_time:
                        continue
                        
                    # Ensure timezone awareness
                    next_wake = self._ensure_aware(frame.next_wake_time)
                    
                    try:
                        # Determine if the frame needs transition now
                        time_until_wake = (next_wake - now).total_seconds()
                    except TypeError as e:
                        logger.error(f"DATETIME ERROR: Cannot subtract {next_wake} (tzinfo: {next_wake.tzinfo}) from {now} (tzinfo: {now.tzinfo})")
                        logger.error(f"TypeError details: {str(e)}")
                        # Skip this frame to avoid crashing the thread
                        continue
                    
                    if time_until_wake <= 0:  # Due now or overdue
                        frames_to_update.append((frame, time_until_wake))
                
                # Log summary info
                if frames_to_update:
                    logger.info(f"Found {len(frames_to_update)} frames that need to transition")
                
                # Process frames that need updates
                for frame_info in frames_to_update:
                    frame, time_until_wake = frame_info
                    try:
                        # Only log significantly overdue frames
                        if time_until_wake < -5:  # More than 5 seconds overdue
                            logger.info(f"Frame {frame.id} ({frame.name}) is {-time_until_wake:.1f} seconds overdue for transition")
                        
                        self._transition_frame(session, frame, now)
                    except Exception as e:
                        logger.error(f"Error transitioning frame {frame.id}: {str(e)}", exc_info=True)
                
            except Exception as e:
                logger.error(f"Error querying frames: {str(e)}", exc_info=True)
        
        finally:
            session.close()
    
    def _transition_frame(self, session, frame, now):
        """
        Transition a frame to the next photo.
        
        Args:
            session: SQLAlchemy session
            frame: PhotoFrame instance to update
            now: Current datetime (should be timezone-aware)
        """
        try:
            # Ensure now is timezone-aware
            now = self._ensure_aware(now)
            
            # Get all playlist entries ordered by order
            playlist = session.query(self.PlaylistEntry).filter_by(
                frame_id=frame.id
            ).order_by(self.PlaylistEntry.order).all()
            
            # Check if playlist is empty
            if not playlist:
                logger.warning(f"Frame {frame.id} has an empty playlist, skipping transition")
                # Update wake times anyway to prevent constant checking
                frame.last_wake_time = now
                frame.next_wake_time = now + timedelta(minutes=frame.sleep_interval)
                session.commit()
                return
            
            # Get the next photo (random if shuffle enabled, otherwise first in playlist)
            if frame.shuffle_enabled:
                # If shuffle is enabled, choose a random photo that's not the current one
                import random
                if len(playlist) > 1 and frame.current_photo_id:
                    # Find entries that don't match the current photo
                    other_entries = [entry for entry in playlist if entry.photo_id != frame.current_photo_id]
                    if other_entries:
                        next_entry = random.choice(other_entries)
                    else:
                        next_entry = playlist[0]
                else:
                    next_entry = random.choice(playlist)
            else:
                # Sequential navigation - take the first entry
                next_entry = playlist[0]
            
            # Update the frame's current photo
            frame.current_photo_id = next_entry.photo_id
            
            # Reorder the playlist:
            # 1. Move all other entries up one position
            current_entries = [entry for entry in playlist if entry.photo_id != next_entry.photo_id]
            for i, entry in enumerate(current_entries):
                entry.order = i
            
            # 2. Move current entry to the bottom
            next_entry.order = len(playlist) - 1
            
            # Update wake times (ensure timezone awareness)
            frame.last_wake_time = now
            
            # Calculate next wake time safely
            sleep_interval = frame.sleep_interval if hasattr(frame, 'sleep_interval') else 1
            frame.next_wake_time = now + timedelta(minutes=sleep_interval)
            
            # Commit the changes
            session.commit()
            
            # Replace detailed debug log with more concise info log that only shows occasionally
            if isinstance(frame.id, str) or not frame.id % 10 == 0:  
                # Only log occasionally to reduce verbosity
                logger.info(f"Frame {frame.id} transitioned to new photo")
        except Exception as e:
            logger.error(f"Error in _transition_frame: {str(e)}", exc_info=True)
            raise  # Re-raise to allow the caller to handle it
    
    def force_transition(self, frame_id, direction='next'):
        """
        Force a frame to transition to the next or previous photo.
        
        Args:
            frame_id: ID of the frame to transition
            direction: 'next' or 'prev' to indicate direction
        
        Returns:
            dict: Information about the transition
        """
        try:
            with self.app.app_context():
                # Use the main application's session
                frame = self.db.session.get(self.PhotoFrame, frame_id)
                if not frame:
                    return {'error': 'Frame not found'}, 404
                
                # Get all playlist entries ordered by order
                playlist = self.PlaylistEntry.query.filter_by(
                    frame_id=frame_id
                ).order_by(self.PlaylistEntry.order).all()
                
                # Check if playlist is empty
                if not playlist:
                    return {'error': 'Playlist is empty'}, 404
                
                # Get the next/prev photo based on direction
                if direction == 'next':
                    if frame.shuffle_enabled and len(playlist) > 1:
                        # If shuffle is enabled, choose a random photo that's not the current one
                        import random
                        if frame.current_photo_id:
                            # Find entries that don't match the current photo
                            other_entries = [entry for entry in playlist if entry.photo_id != frame.current_photo_id]
                            if other_entries:
                                new_entry = random.choice(other_entries)
                            else:
                                new_entry = playlist[0]
                        else:
                            new_entry = random.choice(playlist)
                    else:
                        # Sequential navigation - take the first entry
                        new_entry = playlist[0]
                else:  # prev
                    # Find the current photo's position in the playlist
                    current_position = 0
                    if frame.current_photo_id:
                        for i, entry in enumerate(playlist):
                            if entry.photo_id == frame.current_photo_id:
                                current_position = i
                                break
                    
                    # Get the previous photo (or loop to the end)
                    new_position = (current_position - 1) % len(playlist)
                    new_entry = playlist[new_position]
                
                # Update the frame's current photo
                frame.current_photo_id = new_entry.photo_id
                
                # Reorder the playlist:
                # 1. Move all other entries up one position
                current_entries = [entry for entry in playlist if entry.photo_id != new_entry.photo_id]
                for i, entry in enumerate(current_entries):
                    entry.order = i
                
                # 2. Move current entry to the bottom
                new_entry.order = len(playlist) - 1
                
                # Update wake times with timezone-aware datetimes
                now = datetime.now(timezone.utc)  # Always use UTC
                frame.last_wake_time = now
                
                # Calculate next wake time safely
                sleep_interval = frame.sleep_interval if hasattr(frame, 'sleep_interval') else 1
                frame.next_wake_time = now + timedelta(minutes=sleep_interval)
                
                # Commit the changes
                self.db.session.commit()
                
                # Get the updated photo
                photo = self.db.session.get(self.Photo, new_entry.photo_id)
                
                # Ensure datetimes are timezone-aware for serialization
                next_wake_time = self._ensure_aware(frame.next_wake_time)
                last_wake_time = self._ensure_aware(frame.last_wake_time)
                
                # Choose the appropriate version based on frame orientation
                filename = None
                if photo:
                    if frame.orientation == 'portrait' and photo.portrait_version:
                        filename = photo.portrait_version
                    elif frame.orientation == 'landscape' and photo.landscape_version:
                        filename = photo.landscape_version
                    else:
                        filename = photo.filename  # Fall back to original if no oriented version exists
                
                return {
                    'success': True,
                    'current_photo': filename if photo else None,
                    'next_wake_time': next_wake_time.isoformat() if next_wake_time else None,
                    'last_wake_time': last_wake_time.isoformat() if last_wake_time else None
                }
        except Exception as e:
            logger.error(f"Error in force_transition: {str(e)}", exc_info=True)
            return {'error': str(e)}, 500
    
    def check_frame_status(self, frame_id):
        """
        Check the status of a specific frame and log detailed information.
        This is useful for debugging timing issues.
        
        Args:
            frame_id: ID of the frame to check
            
        Returns:
            dict: Status information about the frame
        """
        try:
            with self.app.app_context():
                # Use the main application's session
                frame = self.db.session.get(self.PhotoFrame, frame_id)
                if not frame:
                    logger.error(f"Frame {frame_id} not found")
                    return {'error': 'Frame not found'}
                
                now = datetime.now(timezone.utc)
                
                # Get current photo info
                current_photo = None
                if frame.current_photo_id:
                    current_photo = self.db.session.get(self.Photo, frame.current_photo_id)
                
                # Get playlist info
                playlist = self.PlaylistEntry.query.filter_by(frame_id=frame_id).order_by(self.PlaylistEntry.order).all()
                
                # Calculate timing information (with timezone awareness)
                time_until_next = None
                next_wake_time = self._ensure_aware(frame.next_wake_time)
                if next_wake_time:
                    time_until_next = (next_wake_time - now).total_seconds()
                
                time_since_last = None
                last_wake_time = self._ensure_aware(frame.last_wake_time)
                if last_wake_time:
                    time_since_last = (now - last_wake_time).total_seconds()
                
                # Choose the appropriate version based on frame orientation
                filename = None
                if current_photo:
                    if frame.orientation == 'portrait' and current_photo.portrait_version:
                        filename = current_photo.portrait_version
                    elif frame.orientation == 'landscape' and current_photo.landscape_version:
                        filename = current_photo.landscape_version
                    else:
                        filename = current_photo.filename  # Fall back to original if no oriented version exists
                
                # Consolidate multiple debug logs into a single more concise log
                if frame.next_wake_time and time_until_next is not None:
                    logger.debug(f"Frame {frame_id} ({frame.name}): next wake in {time_until_next:.1f}s, " +
                                f"playlist: {len(playlist)} photos, current photo: {filename if current_photo else 'None'}")
                
                # Check if this frame should have transitioned
                needs_transition = False
                if next_wake_time and next_wake_time <= now:
                    needs_transition = True
                    # Keep this as info level since it's important
                    logger.info(f"Frame {frame_id} NEEDS TRANSITION - {(now - next_wake_time).total_seconds():.1f} seconds overdue")
                
                return {
                    'id': frame.id,
                    'name': frame.name,
                    'frame_type': frame.frame_type,
                    'sleep_interval': frame.sleep_interval,
                    'current_photo': filename if current_photo else None,
                    'playlist_size': len(playlist),
                    'last_wake_time': last_wake_time.isoformat() if last_wake_time else None,
                    'next_wake_time': next_wake_time.isoformat() if next_wake_time else None,
                    'time_since_last_wake': time_since_last,
                    'time_until_next_wake': time_until_next,
                    'needs_transition': needs_transition
                }
        except Exception as e:
            logger.error(f"Error in check_frame_status: {str(e)}", exc_info=True)
            return {'error': str(e)} 