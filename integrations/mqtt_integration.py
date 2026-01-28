import paho.mqtt.client as mqtt
import json
import logging
import os
from typing import Dict, Any
import threading
import time
from collections import OrderedDict
from threading import Lock
from datetime import timezone
import re
from datetime import datetime

class MQTTIntegration:
    def __init__(self, settings, photo_dir, Frame, db, PlaylistEntry, app, CustomPlaylist):
        self.app = app
        self.Frame = Frame
        self.db = db
        self.PlaylistEntry = PlaylistEntry
        self.photo_dir = photo_dir
        self.settings = settings
        self.CustomPlaylist = CustomPlaylist
        self.client = None
        self.connected = False
        self.status = "Disconnected"
        
        # MQTT topics
        self.topic_prefix = "frame"
        self.discovery_prefix = "homeassistant"
        
        # Get frames
        with self.app.app_context():
            self.frames = self.Frame.query.all()
        
        # Initialize if enabled
        if self.settings.get('enabled', False):
            self.start()
            logging.warning("MQTT Debug: Integration initialized and started")
        else:
            logging.warning("MQTT Debug: MQTT integration disabled")
        
        # Store device component configs for later use
        self.device_components = {
            'media_player': {
                'component': 'media_player',
                'supported_features': [
                    'SUPPORT_PLAY',
                    'SUPPORT_PAUSE',
                    'SUPPORT_NEXT_TRACK',
                    'SUPPORT_PREVIOUS_TRACK',
                    'SUPPORT_SELECT_SOURCE',
                    'SUPPORT_SHUFFLE_SET'
                ]
            },
            'switch': {
                'component': 'switch',
                'icon': 'mdi:power'
            },
            'sensor': {
                'component': 'sensor',
                'device_class': 'timestamp'
            },
            'number': {
                'component': 'number',
                'icon': 'mdi:timer'
            }
        }

    def start(self) -> bool:
        """Start MQTT client."""
        try:
            if self.client:
                self.stop()
                
            self.client = mqtt.Client()
            logging.warning("MQTT Debug: Created new MQTT client in start()")
            
            # Set up authentication if credentials are provided
            if self.settings.get('username') and self.settings.get('password'):
                self.client.username_pw_set(
                    self.settings['username'],
                    self.settings['password']
                )
                
            # Set up message handlers
            self.client.on_connect = self.on_connect
            self.client.on_disconnect = self.on_disconnect
            self.client.on_message = self.on_message

            # Connect to broker
            self.client.connect(
                self.settings['broker'],
                int(self.settings.get('port', 1883)),
                60
            )
            self.client.loop_start()
            logging.warning("MQTT Debug: Started MQTT client loop in start()")
            
            self.status = "Connecting..."
            return True
            
        except Exception as e:
            logging.error(f"Failed to connect to MQTT broker: {e}")
            self.status = f"Connection failed: {str(e)}"
            self.client = None
            self.connected = False
            return False

    def stop(self):
        """Stop MQTT client."""
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except:
                pass
            finally:
                self.client = None
                self.connected = False
                self.status = "Disconnected"

    def on_connect(self, client, userdata, flags, rc):
        """Handle connection to MQTT broker."""
        if rc == 0:
            logging.warning("MQTT Debug: Connected to MQTT broker successfully")
            self.connected = True
            self.status = "Connected"
            
            # Subscribe to all required topics
            topics = [
                f"{self.topic_prefix}/+/set",
                f"{self.topic_prefix}/+/apply_playlist/set",
                f"{self.topic_prefix}/+/next_up/set",
                f"{self.topic_prefix}/+/sleep_interval/set",
                f"{self.topic_prefix}/+/shuffle/set",
                f"{self.topic_prefix}/+/deep_sleep/set"
            ]
            
            for topic in topics:
                client.subscribe(topic)
            
            logging.warning("MQTT Debug: Subscribed to all required topics")
            
            # Sync frames with Home Assistant after connection
            threading.Timer(1, self.sync_frames).start()
            
            # Publish discovery configs after frames are synced
            threading.Timer(3, self._publish_discovery_configs).start()
            
            # Publish initial state
            threading.Timer(5, self.publish_state_all).start()
        else:
            logging.error(f"Failed to connect to MQTT broker with code: {rc}")
            self.connected = False
            self.status = f"Connection failed (code {rc})"

    def on_disconnect(self, client, userdata, rc):
        """Handle disconnection from MQTT broker."""
        logging.info(f"Disconnected from MQTT broker with code: {rc}")
        self.connected = False
        self.status = "Disconnected"

    def on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages."""
        try:
            topic = msg.topic
            payload = msg.payload.decode()
            
            logging.warning(f"MQTT Debug: Handling message on topic {topic}: {payload}")
            
            # Extract frame ID from topic (format: frame/[frame_id]/...)
            parts = topic.split('/')
            if len(parts) < 3:
                return
                
            frame_id = parts[1]
            command = parts[2]
            
            # Handle different message types
            if topic.endswith('/apply_playlist/set'):
                self.handle_apply_playlist(frame_id, payload)
            elif topic.endswith('/next_up/set'):
                self.handle_next_up(frame_id, payload)
            elif topic.endswith('/sleep_interval/set'):
                self.handle_sleep_interval(frame_id, int(payload))
            elif topic.endswith('/shuffle/set'):
                self.handle_shuffle(frame_id, payload.lower() == "true")
            elif topic.endswith('/deep_sleep/set'):
                self.handle_deep_sleep(frame_id, payload.lower() == "true")
            elif topic.endswith('/set'):
                self._handle_command(command, payload)
            else:
                logging.warning(f"MQTT Debug: Unhandled topic: {topic}")
                
        except Exception as e:
            logging.error(f"Error handling message: {e}")

    def _handle_command(self, command: str, payload: str):
        """Handle different command types."""
        try:
            if command == "photo":
                # Handle photo selection
                photo_name = payload
                if os.path.exists(os.path.join(self.photo_dir, photo_name)):
                    # Implement photo change logic here
                    logging.info(f"Changing photo to: {photo_name}")
                    self.publish_state()
                else:
                    logging.error(f"Photo not found: {photo_name}")
            
        except Exception as e:
            logging.error(f"Error handling command {command}: {e}")

    def handle_next_up(self, frame_id, filename):
        """Handle Next Up photo selection."""
        with self.app.app_context():
            try:
                frame = self.Frame.query.get(frame_id)
                if not frame:
                    return
                
                # Find the playlist entry with the selected photo
                selected_entry = frame.playlist_entries.join(self.PlaylistEntry.photo).filter_by(filename=filename).first()
                if not selected_entry:
                    return
                
                # Get all playlist entries ordered by current order
                entries = frame.playlist_entries.order_by(self.PlaylistEntry.order).all()
                
                # Remove selected entry from current position
                entries.remove(selected_entry)
                # Insert it at the beginning
                entries.insert(0, selected_entry)
                
                # Update order for all entries
                for i, entry in enumerate(entries):
                    entry.order = i
                
                # Commit changes
                self.db.session.commit()
                
                # Update frame options in Home Assistant
                self.update_frame_options(frame)
                
                # Publish updated state
                self.publish_state(frame)
                
            except Exception as e:
                logging.error(f"Error handling next up selection: {e}")

    def handle_sleep_interval(self, frame_id, interval):
        """Handle sleep interval updates."""
        with self.app.app_context():
            try:
                frame = self.Frame.query.get(frame_id)
                if not frame:
                    return
                
                frame.sleep_interval = interval
                self.db.session.commit()
                
                # Publish updated state
                self.publish_state(frame)
                
            except Exception as e:
                logging.error(f"Error handling sleep interval update: {e}")

    def handle_shuffle(self, frame_id, enabled):
        """Handle shuffle toggle."""
        with self.app.app_context():
            try:
                frame = self.Frame.query.get(frame_id)
                if not frame:
                    return
                
                frame.shuffle_enabled = enabled
                self.db.session.commit()
                
                # Publish updated state
                self.publish_state(frame)
                
            except Exception as e:
                logging.error(f"Error handling shuffle update: {e}")

    def handle_deep_sleep(self, frame_id, enabled):
        """Handle deep sleep toggle."""
        with self.app.app_context():
            try:
                frame = self.Frame.query.get(frame_id)
                if not frame:
                    return
                
                frame.deep_sleep_enabled = enabled
                self.db.session.commit()
                
                # Publish updated state
                self.publish_state(frame)
                
            except Exception as e:
                logging.error(f"Error handling deep sleep update: {e}")

    def handle_apply_playlist(self, frame_id, payload):
        """Handle playlist selection from MQTT."""
        with self.app.app_context():
            try:
                # Don't process the default option
                if payload == "--Select Playlist--":
                    logging.warning(f"MQTT Debug: Ignoring default playlist selection")
                    return
                
                # Handle the "None" option - clear the playlist
                if payload == "--None--":
                    logging.warning(f"MQTT Debug: Clearing playlist for frame {frame_id}")
                    frame = self.Frame.query.get(frame_id)
                    if frame:
                        # Clear existing playlist entries
                        self.PlaylistEntry.query.filter_by(frame_id=frame_id).delete()
                        self.db.session.commit()
                        
                        # Update the state
                        self.update_frame_options(frame)
                        self.publish_state(frame)
                        
                        # Send notification
                        notification_topic = f"{self.topic_prefix}/{frame_id}/notification"
                        notification_message = "Cleared playlist"
                        self.client.publish(notification_topic, notification_message, retain=False)
                    
                    # Reset the playlist selector to the default value
                    self.client.publish(
                        f"{self.topic_prefix}/{frame_id}/playlist_state",
                        "--Select Playlist--",
                        retain=True
                    )
                    return
                
                # Extract playlist ID from payload format "Name [ID]"
                match = re.search(r"\[(\d+)\]$", payload)
                if not match:
                    logging.error(f"MQTT Debug: Invalid playlist payload format: {payload}")
                    return
                    
                playlist_id = int(match.group(1))
                logging.warning(f"MQTT Debug: Applying playlist {playlist_id} to frame {frame_id}")
                self._apply_playlist_to_frame(frame_id, playlist_id)
                
                # Reset the playlist selector to the default value
                logging.warning(f"MQTT Debug: Resetting playlist selector for frame {frame_id}")
                self.client.publish(
                    f"{self.topic_prefix}/{frame_id}/playlist_state",
                    "--Select Playlist--",
                    retain=True
                )
                
            except Exception as e:
                logging.error(f"Error applying playlist: {e}")

    def _apply_playlist_to_frame(self, frame_id, playlist_id):
        """Internal method to apply playlist to frame."""
        try:
            frame = self.Frame.query.get(frame_id)
            playlist = self.CustomPlaylist.query.get(playlist_id)
            
            if not frame or not playlist:
                logging.error(f"MQTT Debug: Frame {frame_id} or playlist {playlist_id} not found")
                return
            
            # Clear existing playlist entries
            self.PlaylistEntry.query.filter_by(frame_id=frame_id).delete()
            
            logging.warning(f"MQTT Debug: Applying playlist {playlist.name} to frame {frame.name}")
    
            # Add new entries from selected playlist
            for order, entry in enumerate(playlist.entries.order_by(self.PlaylistEntry.order)):
                new_entry = self.PlaylistEntry(
                    frame_id=frame_id,
                    photo_id=entry.photo_id,
                    order=order
                )
                self.db.session.add(new_entry)
            
            self.db.session.commit()
            
            # Update the state and send notification message
            self.update_frame_options(frame)
            self.publish_state(frame)
            
            # Send a notification message to indicate the playlist was applied
            notification_topic = f"{self.topic_prefix}/{frame_id}/notification"
            notification_message = f"Applied playlist: {playlist.name}"
            self.client.publish(notification_topic, notification_message, retain=False)
            logging.warning(f"MQTT Debug: Published notification: {notification_message}")
            
        except Exception as e:
            logging.error(f"Error applying playlist to frame: {e}")

    def _publish_discovery_configs(self):
        """Publish Home Assistant MQTT discovery configs."""
        if not self.connected:
            return
            
        try:
            with self.app.app_context():
                device_info = {
                    "identifiers": ["photo_frame"],
                    "name": self.settings.get('device_name', 'Photo Frame'),
                    "model": "Photo Frame Server",
                    "manufacturer": "Custom"
                }

                # Photo selector
                photos = [f for f in os.listdir(self.photo_dir) 
                         if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp'))]
                         
                select_config = {
                    "name": "Photo Select",
                    "unique_id": "photo_frame_select",
                    "command_topic": f"{self.topic_prefix}/photo/set",
                    "state_topic": f"{self.topic_prefix}/state",
                    "value_template": "{{ value_json.current_photo }}",
                    "options": photos,
                    "device": device_info
                }
                
                self._publish_config("select", "photo_select", select_config)
        except Exception as e:
            logging.error(f"Error publishing discovery configs: {e}")

    def _publish_config(self, component: str, name: str, config: Dict[str, Any]):
        """Publish a single discovery config."""
        if not self.connected or not self.client:
            return
            
        try:
            topic = f"{self.discovery_prefix}/{component}/photo_frame/{name}/config"
            self.client.publish(topic, json.dumps(config), retain=True)
        except Exception as e:
            logging.error(f"Error publishing config: {e}")

    def publish_state(self, frame):
        """Publish frame state to Home Assistant."""
        if not self.connected or not self.client:
            logging.warning("MQTT client not connected, skipping state publish")
            return
            
        try:
            # Get frame status
            status = frame.get_status()[0]
            state = 'playing' if status == 2 else 'paused'
            
            # Get current photo
            current_photo = frame.current_photo
            
            # Media player state
            self.client.publish(f'{self.topic_prefix}/{frame.id}/state', state)
            
            # Full state as JSON
            state_json = {
                'state': state,
                'shuffle_enabled': str(frame.shuffle_enabled).lower(),
                'deep_sleep_enabled': str(frame.deep_sleep_enabled).lower(),
                'sleep_interval': str(frame.sleep_interval),
                'last_wake_time': frame.last_wake_time.isoformat() if frame.last_wake_time else None,
                'next_wake_time': frame.next_wake_time.isoformat() if frame.next_wake_time else None,
                'next_up': current_photo.filename if current_photo else None
            }
            self.client.publish(
                f'{self.topic_prefix}/{frame.id}/state',
                json.dumps(state_json),
                retain=True
            )
            
            # Attributes
            attributes = {
                'media_title': current_photo.heading if current_photo else None,
                'media_artist': 'Photo Frame',
                'source': 'playlist',
                'shuffle': frame.shuffle_enabled,
                'playlist_position': frame.playlist_entries.count(),
                'playlist_length': frame.playlist_entries.count(),
                'last_updated': datetime.now().isoformat()
            }
            self.client.publish(
                f'{self.topic_prefix}/{frame.id}/attributes', 
                json.dumps(attributes),
                retain=True
            )

            # Power state
            power_state = 'ON' if status in [1, 2] else 'OFF'
            self.client.publish(
                f'{self.topic_prefix}/{frame.id}/power/state', 
                power_state,
                retain=True
            )

            # Sleep interval
            self.client.publish(
                f'{self.topic_prefix}/{frame.id}/sleep_interval/state', 
                str(frame.sleep_interval),
                retain=True
            )
            
            # Individual states for switches
            self.client.publish(
                f'{self.topic_prefix}/{frame.id}/shuffle/state',
                str(frame.shuffle_enabled).lower(),
                retain=True
            )
            
            self.client.publish(
                f'{self.topic_prefix}/{frame.id}/deep_sleep/state',
                str(frame.deep_sleep_enabled).lower(),
                retain=True
            )
            
            logging.warning(f"MQTT Debug: Published state for frame {frame.id}")
            
        except Exception as e:
            logging.error(f"Error publishing MQTT state: {e}")
            self.connected = False
            self.status = "Disconnected"

    def publish_state_all(self):
        """Publish state for all frames."""
        if not self.connected or not self.client:
            logging.warning("MQTT client not connected, skipping state publish")
            return
        
        try:
            with self.app.app_context():
                frames = self.Frame.query.all()
                for frame in frames:
                    self.publish_state(frame)
        except Exception as e:
            logging.error(f"Error publishing MQTT state for all frames: {e}")

    def _register_frame(self, frame):
        """Register a frame with Home Assistant."""
        if not self.connected or not self.client:
            return
            
        try:
            with self.app.app_context():
                # Fetch a fresh copy of the frame with all relationships
                frame = self.Frame.query.get(frame.id)
                if not frame:
                    logging.error(f"Frame {frame.id} not found when registering")
                    return
                    
                playlist_entries = frame.playlist_entries.order_by(self.PlaylistEntry.order).all()
                
                # Create device info for this frame
                device_info = {
                    "identifiers": [f"frame_{frame.id}"],
                    "name": frame.name,
                    "model": frame.model or "Photo Frame",
                    "manufacturer": "Photo Frame Assistant"
                }

                # Create and publish all entity configs
                self._publish_frame_entities(frame, device_info, playlist_entries)
                
                # Immediately publish current state
                self.publish_state(frame)
                
        except Exception as e:
            logging.error(f"Error registering frame {frame.id}: {e}")

    def _publish_frame_entities(self, frame, device_info, playlist_entries):
        """Publish all entity configs for a frame."""
        try:
            logging.warning(f"MQTT Debug: Publishing entities for frame {frame.id} - {frame.name}")
            
            # Deep Sleep switch
            deep_sleep_config = {
                "name": f"{frame.name} Deep Sleep",
                "unique_id": f"frame_{frame.id}_deep_sleep",
                "command_topic": f"{self.topic_prefix}/{frame.id}/deep_sleep/set",
                "state_topic": f"{self.topic_prefix}/{frame.id}/state",
                "value_template": "{{ value_json.deep_sleep_enabled }}",
                "payload_on": "true",
                "payload_off": "false",
                "state_on": "true",
                "state_off": "false",
                "device": device_info,
                "icon": "mdi:power-sleep",
                "entity_category": "config",
                "device_class": "switch"
            }
            self._publish_config("switch", f"{frame.id}_deep_sleep", deep_sleep_config)
            logging.warning(f"MQTT Debug: Published deep sleep switch for frame {frame.id}")

            # Shuffle switch
            shuffle_config = {
                "name": f"{frame.name} Shuffle",
                "unique_id": f"frame_{frame.id}_shuffle",
                "command_topic": f"{self.topic_prefix}/{frame.id}/shuffle/set",
                "state_topic": f"{self.topic_prefix}/{frame.id}/state",
                "value_template": "{{ value_json.shuffle_enabled }}",
                "payload_on": "true",
                "payload_off": "false",
                "state_on": "true",
                "state_off": "false",
                "device": device_info,
                "icon": "mdi:shuffle",
                "entity_category": "config",
                "device_class": "switch"
            }
            self._publish_config("switch", f"{frame.id}_shuffle", shuffle_config)
            logging.warning(f"MQTT Debug: Published shuffle switch for frame {frame.id}")

            # Last Wake Time sensor
            last_wake_config = { 
                "name": f"{frame.name} Last Wake",
                "unique_id": f"frame_{frame.id}_last_wake",
                "state_topic": f"{self.topic_prefix}/{frame.id}/state",
                "value_template": "{{ value_json.last_wake_time }}",
                "device": device_info,
                "icon": "mdi:clock-outline",
                "entity_category": "diagnostic",
                "device_class": "timestamp"
            }
            self._publish_config("sensor", f"{frame.id}_last_wake", last_wake_config)
            logging.warning(f"MQTT Debug: Published last wake sensor for frame {frame.id}")

            # Next Wake Time sensor
            next_wake_config = { 
                "name": f"{frame.name} Next Wake",
                "unique_id": f"frame_{frame.id}_next_wake",
                "state_topic": f"{self.topic_prefix}/{frame.id}/state",
                "value_template": "{{ value_json.next_wake_time }}",
                "device": device_info,
                "icon": "mdi:clock-outline",
                "entity_category": "diagnostic",
                "device_class": "timestamp"
            }
            self._publish_config("sensor", f"{frame.id}_next_wake", next_wake_config)
            logging.warning(f"MQTT Debug: Published next wake sensor for frame {frame.id}")

            # Playlist selector
            with self.app.app_context():
                playlists = self.CustomPlaylist.query.all()
                playlist_options = ["--Select Playlist--", "--None--"] + [f"{p.name} [{p.id}]" for p in playlists]
            
            select_playlist_config = {
                "name": f"{frame.name} Playlist",
                "unique_id": f"frame_{frame.id}_playlist",
                "command_topic": f"{self.topic_prefix}/{frame.id}/apply_playlist/set",
                "state_topic": f"{self.topic_prefix}/{frame.id}/playlist_state",
                "options": playlist_options,
                "device": device_info,
                "icon": "mdi:playlist-music",
                "entity_category": "config"
            }
            self._publish_config("select", f"{frame.id}_playlist", select_playlist_config)
            logging.warning(f"MQTT Debug: Published playlist selector for frame {frame.id}")

            # Initialize playlist state to default value
            self.client.publish(
                f"{self.topic_prefix}/{frame.id}/playlist_state",
                "--Select Playlist--",
                retain=True
            )

            # Next Up selector
            options = self._get_frame_playlist(frame)
            select_next_up_config = {
                "name": f"{frame.name} Next Up",
                "unique_id": f"frame_{frame.id}_next_up",
                "command_topic": f"{self.topic_prefix}/{frame.id}/next_up/set",
                "state_topic": f"{self.topic_prefix}/{frame.id}/state",
                "value_template": "{{ value_json.next_up }}",
                "options": options,
                "device": device_info,
                "icon": "mdi:image-plus",
                "entity_category": "config"
            }
            self._publish_config("select", f"{frame.id}_next_up", select_next_up_config)
            logging.warning(f"MQTT Debug: Published next up selector for frame {frame.id} with {len(options)} options")

            # Sleep Interval number
            sleep_interval_config = {
                "name": f"{frame.name} Sleep Interval",
                "unique_id": f"frame_{frame.id}_sleep_interval",
                "command_topic": f"{self.topic_prefix}/{frame.id}/sleep_interval/set",
                "state_topic": f"{self.topic_prefix}/{frame.id}/state",
                "value_template": "{{ value_json.sleep_interval }}",
                "min": 1,
                "max": 3600,
                "step": 1,
                "unit_of_measurement": "minutes",
                "device": device_info,
                "icon": "mdi:timer-outline",
                "entity_category": "config",
                "state": str(frame.sleep_interval)
            }
            self._publish_config("number", f"{frame.id}_sleep_interval", sleep_interval_config)
            logging.warning(f"MQTT Debug: Published sleep interval for frame {frame.id}")

            # Status notification sensor
            notification_config = {
                "name": f"{frame.name} Status",
                "unique_id": f"frame_{frame.id}_notification",
                "state_topic": f"{self.topic_prefix}/{frame.id}/notification",
                "device": device_info,
                "icon": "mdi:bell",
                "entity_category": "diagnostic"
            }
            self._publish_config("sensor", f"{frame.id}_notification", notification_config)
            logging.warning(f"MQTT Debug: Published notification sensor for frame {frame.id}")
        except Exception as e:
            logging.error(f"Error publishing frame entities for frame {frame.id}: {e}")

    def update_frame_options(self, frame):
        """Update Home Assistant options when playlist changes."""
        if not self.connected or not self.client:
            return
            
        try:
            playlist_entries = frame.playlist_entries.order_by(self.PlaylistEntry.order).all()
            options = [entry.photo.filename for entry in playlist_entries]
            
            # Update the options in Home Assistant
            device_info = {
                "identifiers": [f"frame_{frame.id}"],
                "name": frame.name,
                "model": frame.model or "Photo Frame",
                "manufacturer": frame.manufacturer or "Photo Frame Assistant"
            }

            select_next_up_config = {
                "name": f"{frame.name} Next Up",
                "unique_id": f"frame_{frame.id}_next_up",
                "command_topic": f"{self.topic_prefix}/{frame.id}/next_up/set",
                "state_topic": f"{self.topic_prefix}/{frame.id}/state",
                "value_template": "{{ value_json.next_up }}",
                "options": options,
                "device": device_info,
                "icon": "mdi:image-plus",
                "entity_category": "config"
            }
            self._publish_config("select", f"{frame.id}_next_up", select_next_up_config)
            
            # Update the state
            self.publish_state(frame)
            
        except Exception as e:
            logging.error(f"Error updating frame options: {e}") 

    def update_frame_registration(self, frame):
        """Update frame registration when frame properties change."""
        if not self.connected or not self.client:
            return
            
        try:
            # Re-register the frame to update all entities with new information
            self._register_frame(frame)
            
            # Publish updated state
            self.publish_state(frame)
            
        except Exception as e:
            logging.error(f"Error updating frame registration: {e}")

    def test_connection(self) -> Dict[str, Any]:
        """Test MQTT connection with current settings."""
        try:
            test_client = mqtt.Client()
            
            if self.settings.get('username') and self.settings.get('password'):
                test_client.username_pw_set(
                    self.settings['username'],
                    self.settings['password']
                )
                
            test_client.connect(
                self.settings['broker'],
                int(self.settings.get('port', 1883)),
                5
            )
            test_client.disconnect()
            
            return {
                "success": True,
                "message": "Connection successful",
                "status": "Connected"
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Connection failed: {str(e)}",
                "status": "Connection failed"
            }

    def _get_managed_frames(self):
        """Get list of managed frames from database."""
        try:
            return self.Frame.query.all()
        except Exception as e:
            logging.error(f"Error getting frames: {e}")
            return []

    def _get_frame_playlist(self, frame):
        """Get list of photos from frame's playlist entries."""
        try:
            playlist_entries = frame.playlist_entries.order_by(self.PlaylistEntry.order).all()
            return [entry.photo.filename for entry in playlist_entries]
        except Exception as e:
            logging.error(f"Error getting playlist for frame {frame.id}: {e}")
            return []

    def unregister_frame(self, frame_id):
        """Unregister a frame from Home Assistant by clearing discovery configs."""
        if not self.connected or not self.client:
            return
        
        try:
            logging.warning(f"MQTT Debug: Unregistering frame {frame_id} from Home Assistant")
            
            # List of entity types and suffixes for this frame
            entities = [
                ("switch", "deep_sleep"),
                ("switch", "shuffle"),
                ("sensor", "last_wake"),
                ("sensor", "next_wake"),
                ("select", "playlist"),
                ("select", "next_up"),
                ("number", "sleep_interval"),
                ("sensor", "notification")
            ]
            
            # Clear each entity's discovery config by publishing empty message
            for component, suffix in entities:
                topic = f"{self.discovery_prefix}/{component}/photo_frame/{frame_id}_{suffix}/config"
                self.client.publish(topic, "", retain=True)
                logging.warning(f"MQTT Debug: Cleared discovery config for {topic}")
            
            # Also clear state topics
            state_topics = [
                f"{self.topic_prefix}/{frame_id}/state",
                f"{self.topic_prefix}/{frame_id}/playlist_state",
                f"{self.topic_prefix}/{frame_id}/notification"
            ]
            
            for topic in state_topics:
                self.client.publish(topic, "", retain=True)
                logging.warning(f"MQTT Debug: Cleared state for {topic}")
            
        except Exception as e:
            logging.error(f"Error unregistering frame {frame_id}: {e}") 

    def sync_frames(self):
        """Synchronize frames with Home Assistant."""
        if not self.connected or not self.client:
            return
        
        try:
            with self.app.app_context():
                # Get current frames from database
                current_frames = self.Frame.query.all()
                current_frame_ids = {f.id for f in current_frames}
                
                # Get previously known frames
                known_frame_ids = {f.id for f in self.frames}
                
                # Find frames to remove and add
                frames_to_remove = known_frame_ids - current_frame_ids
                frames_to_add = current_frame_ids - known_frame_ids
                
                logging.warning(f"MQTT Debug: Synchronizing frames. Removing {frames_to_remove}, Adding {frames_to_add}")
                
                # Unregister removed frames
                for frame_id in frames_to_remove:
                    self.unregister_frame(frame_id)
                
                # Register new frames
                for frame in current_frames:
                    if frame.id in frames_to_add:
                        self._register_frame(frame)
                    else:
                        # Update existing frames to ensure all entities are present
                        self._register_frame(frame)
                
                # Update frames list
                self.frames = current_frames
        except Exception as e:
            logging.error(f"Error synchronizing frames: {e}") 

    def setup_ha_device(self, frame):
        """Set up Home Assistant device configuration."""
        device_info = {
            'identifiers': [f'photo_frame_{frame.id}'],
            'name': frame.name or f'Photo Frame {frame.id}',
            'model': frame.model or 'Digital Photo Frame',
            'manufacturer': frame.manufacturer or 'Photo Frame Server',
            'sw_version': frame.firmware_rev or '1.0.0'
        }
        
        # Media Player entity
        self.publish_discovery_message(frame, 'media_player', {
            'name': f'{frame.name} Photo Frame',
            'unique_id': f'photo_frame_{frame.id}_player',
            'device': device_info,
            'state_topic': f'photo_frame/{frame.id}/state',
            'command_topic': f'photo_frame/{frame.id}/command',
            'availability_topic': f'photo_frame/{frame.id}/availability',
            'json_attributes_topic': f'photo_frame/{frame.id}/attributes',
            'supported_features': [
                'play', 'pause', 'next_track', 'previous_track',
                'select_source', 'shuffle'
            ],
            'source_list': ['playlist', 'dynamic', 'scheduled'],
            'payload_play': 'PLAY',
            'payload_pause': 'PAUSE',
            'payload_next': 'NEXT',
            'payload_previous': 'PREVIOUS'
        })

        # Power Switch entity
        self.publish_discovery_message(frame, 'switch', {
            'name': f'{frame.name} Power',
            'unique_id': f'photo_frame_{frame.id}_power',
            'device': device_info,
            'state_topic': f'photo_frame/{frame.id}/power/state',
            'command_topic': f'photo_frame/{frame.id}/power/set',
            'payload_on': 'ON',
            'payload_off': 'OFF'
        })

        # Sleep Interval Number entity
        self.publish_discovery_message(frame, 'number', {
            'name': f'{frame.name} Sleep Interval',
            'unique_id': f'photo_frame_{frame.id}_sleep_interval',
            'device': device_info,
            'state_topic': f'photo_frame/{frame.id}/sleep_interval/state',
            'command_topic': f'photo_frame/{frame.id}/sleep_interval/set',
            'min': 1,
            'max': 120,
            'step': 1,
            'unit_of_measurement': 'minutes'
        })

    def handle_command(self, frame_id, command, payload):
        """Handle commands from Home Assistant."""
        frame = self.db.session.get(self.PhotoFrame, frame_id)
        if not frame:
            return

        if command == 'command':
            if payload == 'PLAY':
                frame.deep_sleep_enabled = False
            elif payload == 'PAUSE':
                frame.deep_sleep_enabled = True
            elif payload == 'NEXT':
                self.app.frame_timing_manager.force_transition(frame_id, 'next')
            elif payload == 'PREVIOUS':
                self.app.frame_timing_manager.force_transition(frame_id, 'prev')
            
        elif command == 'power/set':
            frame.deep_sleep_enabled = (payload == 'OFF')
            
        elif command == 'sleep_interval/set':
            try:
                frame.sleep_interval = float(payload)
            except ValueError:
                pass

        self.db.session.commit()
        self.publish_state(frame) 