from datetime import datetime
from model import db, EventLog

class EventLogger:
    # Event type constants
    EVENT_CONNECTION = 'connection'
    EVENT_PHOTO_REQUEST = 'photo_request'
    EVENT_DIAGNOSTIC = 'diagnostic'
    EVENT_ERROR = 'error'
    EVENT_PLAYLIST_CHANGE = 'playlist_change'
    
    # Source constants
    SOURCE_USER = 'user'
    SOURCE_MQTT = 'mqtt' 
    SOURCE_SYSTEM = 'system'
    SOURCE_FRAME = 'frame'
    
    @staticmethod
    def log_event(frame_id, event_type, source='system', details=None):
        """
        Log an event for a specific frame
        
        Args:
            frame_id: The ID of the frame
            event_type: Type of event (use class constants)
            source: Source of the event (user, mqtt, system, frame)
            details: Dict containing additional event information
        """
        details = details or {}
        event = EventLog(
            frame_id=frame_id,
            event_type=event_type,
            source=source,
            details=details
        )
        db.session.add(event)
        db.session.commit()
    
    @staticmethod
    def log_connection(frame_id, source='frame', details=None):
        """Log a frame connection event"""
        EventLogger.log_event(frame_id, EventLogger.EVENT_CONNECTION, source, details)
    
    @staticmethod
    def log_photo_request(frame_id, photo_id=None, source='frame', details=None):
        """Log a photo request event"""
        event_details = details or {}
        if photo_id:
            event_details['photo_id'] = photo_id
        EventLogger.log_event(frame_id, EventLogger.EVENT_PHOTO_REQUEST, source, event_details)
    
    @staticmethod
    def log_diagnostic(frame_id, diagnostics=None, source='frame', details=None):
        """Log a diagnostic data event"""
        event_details = details or {}
        if diagnostics:
            event_details['diagnostics'] = diagnostics
        EventLogger.log_event(frame_id, EventLogger.EVENT_DIAGNOSTIC, source, event_details)
    
    @staticmethod
    def log_error(frame_id, error_message, source='frame', details=None):
        """Log an error event"""
        event_details = details or {}
        event_details['error_message'] = error_message
        EventLogger.log_event(frame_id, EventLogger.EVENT_ERROR, source, event_details)
    
    @staticmethod
    def log_playlist_change(frame_id, source='user', details=None):
        """Log a playlist change event"""
        EventLogger.log_event(frame_id, EventLogger.EVENT_PLAYLIST_CHANGE, source, details)
    
    @staticmethod
    def get_events(frame_id=None, event_type=None, limit=100, offset=0):
        """
        Get events, optionally filtered by frame and event type
        
        Args:
            frame_id: Optional frame ID to filter events
            event_type: Optional event type to filter events
            limit: Maximum number of events to return
            offset: Offset for pagination
            
        Returns:
            List of EventLog objects
        """
        query = EventLog.query
        
        if frame_id:
            query = query.filter(EventLog.frame_id == frame_id)
        
        if event_type:
            query = query.filter(EventLog.event_type == event_type)
            
        return query.order_by(EventLog.timestamp.desc()).limit(limit).offset(offset).all()