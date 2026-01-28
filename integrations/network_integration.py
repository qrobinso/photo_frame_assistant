import os
import json
import logging
import shutil
from datetime import datetime
import socket
from pathlib import Path
import platform
import re

logger = logging.getLogger(__name__)

class NetworkIntegration:
    def __init__(self, config_path):
        self.config_path = config_path
        self.locations = self.load_locations()
        
    def load_locations(self):
        """Load network locations from config file."""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading network locations: {e}")
        return []
        
    def save_locations(self, locations):
        """Save network locations to config file."""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w') as f:
                json.dump(locations, f, indent=4)
            self.locations = locations
            return True
        except Exception as e:
            logger.error(f"Error saving network locations: {e}")
            return False
            
    def get_location_by_id(self, location_id):
        """Get a network location by its ID."""
        for location in self.locations:
            if str(location.get('id')) == str(location_id):
                return location
        return None
        
    def add_location(self, name, network_path, username, password):
        """Add a new network location."""
        # Generate a unique ID
        location_id = 1
        if self.locations:
            location_id = max(int(loc.get('id', 0)) for loc in self.locations) + 1
            
        new_location = {
            'id': location_id,
            'name': name,
            'network_path': network_path,
            'username': username,
            'password': password,
            'created_at': datetime.now().isoformat()
        }
        
        self.locations.append(new_location)
        self.save_locations(self.locations)
        return new_location
        
    def update_location(self, location_id, name, network_path, username, password):
        """Update an existing network location."""
        location = self.get_location_by_id(location_id)
        if not location:
            return None
            
        location['name'] = name
        location['network_path'] = network_path
        location['username'] = username
        location['password'] = password
        
        self.save_locations(self.locations)
        return location
        
    def delete_location(self, location_id):
        """Delete a network location."""
        location = self.get_location_by_id(location_id)
        if not location:
            return False
            
        self.locations = [loc for loc in self.locations if str(loc.get('id')) != str(location_id)]
        self.save_locations(self.locations)
        return True
        
    def list_files(self, location_id, path=None):
        """List files and directories in a network location."""
        location = self.get_location_by_id(location_id)
        if not location:
            raise ValueError(f"Network location with ID {location_id} not found")
            
        network_path = location['network_path']
        
        # If a subpath is provided, append it to the network path
        if path:
            # Ensure path doesn't contain any directory traversal attempts
            if '..' in path:
                raise ValueError("Invalid path")
            network_path = os.path.join(network_path, path)
            
        try:
            # List files and directories
            items = []
            
            # Check if the path exists and is accessible
            if not os.path.exists(network_path):
                raise ValueError(f"Path does not exist: {network_path}")
                
            for item in os.listdir(network_path):
                item_path = os.path.join(network_path, item)
                is_dir = os.path.isdir(item_path)
                
                # Get file size and modification time
                try:
                    stats = os.stat(item_path)
                    size = stats.st_size if not is_dir else 0
                    modified = datetime.fromtimestamp(stats.st_mtime).isoformat()
                except:
                    size = 0
                    modified = None
                
                items.append({
                    'name': item,
                    'is_directory': is_dir,
                    'size': size,
                    'modified': modified,
                    'path': os.path.join(path or '', item) if path else item
                })
                
            # Sort: directories first, then files alphabetically
            items.sort(key=lambda x: (not x['is_directory'], x['name'].lower()))
            
            return {
                'items': items,
                'current_path': path or '',
                'parent_path': os.path.dirname(path) if path else None
            }
            
        except Exception as e:
            logger.error(f"Error listing files in network location: {e}")
            raise
            
    def import_file(self, location_id, file_path, upload_folder):
        """Import a file from a network location to the upload folder."""
        location = self.get_location_by_id(location_id)
        if not location:
            raise ValueError(f"Network location with ID {location_id} not found")
            
        network_path = location['network_path']
        
        # Ensure file_path doesn't contain any directory traversal attempts
        if '..' in file_path:
            raise ValueError("Invalid file path")
            
        source_path = os.path.join(network_path, file_path)
        
        # Check if the file exists
        if not os.path.isfile(source_path):
            raise ValueError(f"File does not exist: {source_path}")
            
        # Generate a unique filename
        filename = os.path.basename(source_path)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        new_filename = f"network_{timestamp}_{filename}"
        destination_path = os.path.join(upload_folder, new_filename)
        
        try:
            # Copy the file
            shutil.copy2(source_path, destination_path)
            
            return {
                'filename': new_filename,
                'original_filename': filename,
                'source_path': source_path,
                'heading': f"Imported from {location['name']}"
            }
            
        except Exception as e:
            logger.error(f"Error importing file from network location: {e}")
            raise 