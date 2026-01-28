import os
import json
import shutil
import logging
import io
from tempfile import NamedTemporaryFile
from flask import Blueprint, request, jsonify, current_app, send_file, abort
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime
import socket
import subprocess
from PIL import Image
from io import BytesIO
import requests
import traceback

# Try to import HEIC support
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_SUPPORT = True
    logging.info("HEIC support enabled")
except ImportError:
    HEIC_SUPPORT = False
    logging.warning("pillow-heif not installed. HEIC conversion will be limited.")

# Try to import SMB libraries
try:
    from smb.SMBConnection import SMBConnection
    import nmb.NetBIOS
    SMB_AVAILABLE = True
except ImportError:
    SMB_AVAILABLE = False
    logging.warning("pysmb not installed. SMB functionality will be limited.")

# Import Immich integration
from integrations.immich_integration import ImmichIntegration

# Create blueprint
integration_routes = Blueprint('integration_routes', __name__)

# Path to the network locations configuration file
NETWORK_CONFIG_FILE = 'config/network_locations.json'

# Path to the Immich configuration file
IMMICH_CONFIG_FILE = 'config/immich_config.json'

# Path to track imported files for each location
IMPORTED_FILES_DIR = 'config/imported_files'

# Ensure config directories exist
os.makedirs(os.path.dirname(NETWORK_CONFIG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(IMMICH_CONFIG_FILE), exist_ok=True)
os.makedirs(IMPORTED_FILES_DIR, exist_ok=True)

# Helper function to load network locations
def load_network_locations():
    if not os.path.exists(NETWORK_CONFIG_FILE):
        # Create empty config file if it doesn't exist
        with open(NETWORK_CONFIG_FILE, 'w') as f:
            json.dump({"locations": []}, f)
        return {"locations": []}
    
    try:
        with open(NETWORK_CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading network locations: {str(e)}")
        return {"locations": []}

# Helper function to save network locations
def save_network_locations(data):
    try:
        with open(NETWORK_CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logging.error(f"Error saving network locations: {str(e)}")
        return False

# Helper function to load imported files for a location
def load_imported_files(location_id):
    imported_files_path = os.path.join(IMPORTED_FILES_DIR, f"{location_id}.json")
    if not os.path.exists(imported_files_path):
        return []
    
    try:
        with open(imported_files_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error loading imported files for location {location_id}: {str(e)}")
        return []

# Helper function to save imported files for a location
def save_imported_files(location_id, files):
    try:
        imported_files_path = os.path.join(IMPORTED_FILES_DIR, f"{location_id}.json")
        with open(imported_files_path, 'w') as f:
            json.dump(files, f, indent=2)
        return True
    except Exception as e:
        logging.error(f"Error saving imported files for location {location_id}: {str(e)}")
        return False

# Helper function to resolve server name to IP address
def resolve_server_name(server_name):
    # If the server name is already an IP address, return it as is
    if is_ip_address(server_name):
        logging.info(f"Server name '{server_name}' is already an IP address")
        return server_name
    
    try:
        # Try to resolve the server name to an IP address
        ip_address = socket.gethostbyname(server_name)
        logging.info(f"Resolved server name '{server_name}' to IP address '{ip_address}'")
        return ip_address
    except socket.gaierror as e:
        logging.warning(f"Failed to resolve server name '{server_name}' via DNS: {str(e)}")
        
        # For local network connections, we might not have DNS resolution
        # but the server might still be accessible by its NetBIOS name or IP
        logging.info(f"Returning original server name '{server_name}' for local network connection")
        return server_name

# Helper function to check if a string is an IP address
def is_ip_address(s):
    try:
        # Try to create an IPv4 address object
        socket.inet_aton(s)
        # Check if it's a valid IPv4 address format (x.x.x.x)
        return s.count('.') == 3
    except socket.error:
        return False

# Route to get all network locations
@integration_routes.route('/api/network/locations', methods=['GET'])
def get_network_locations():
    try:
        data = load_network_locations()
        
        # For security, don't send passwords to the client
        for location in data.get('locations', []):
            if 'password' in location:
                # Replace with a flag indicating password exists
                location['has_password'] = bool(location['password'])
                location.pop('password', None)
        
        return jsonify({"success": True, "locations": data.get('locations', [])})
    except Exception as e:
        logging.error(f"Error retrieving network locations: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to add a new network location
@integration_routes.route('/api/network/locations', methods=['POST'])
def add_network_location():
    try:
        data = load_network_locations()
        new_location = request.json
        
        # Validate required fields
        if not new_location.get('name') or not new_location.get('network_path'):
            return jsonify({"success": False, "error": "Name and network path are required"}), 400
        
        # Generate a unique ID for the new location
        new_location['id'] = str(uuid.uuid4())
        
        # Add auto-add fields if they exist
        if 'autoAddNewMedia' in new_location and new_location['autoAddNewMedia']:
            if not new_location.get('autoAddTargetFrameId'):
                return jsonify({"success": False, "error": "Target frame is required when Auto Add New Media is enabled"}), 400
        
        # Add the new location
        data['locations'].append(new_location)
        
        # Save the updated data
        if save_network_locations(data):
            return jsonify({"success": True, "message": "Network location added successfully"})
        else:
            return jsonify({"success": False, "error": "Failed to save network location"}), 500
    except Exception as e:
        logging.error(f"Error adding network location: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to update an existing network location
@integration_routes.route('/api/network/locations/<location_id>', methods=['PUT'])
def update_network_location(location_id):
    try:
        data = load_network_locations()
        updated_location = request.json
        
        # Find the location to update
        location_index = None
        for i, location in enumerate(data.get('locations', [])):
            if location.get('id') == location_id:
                location_index = i
                break
        
        if location_index is None:
            return jsonify({"success": False, "error": "Network location not found"}), 404
        
        # Validate required fields
        if not updated_location.get('name') or not updated_location.get('network_path'):
            return jsonify({"success": False, "error": "Name and network path are required"}), 400
        
        # Validate auto-add fields if enabled
        if updated_location.get('autoAddNewMedia', False):
            if not updated_location.get('autoAddTargetFrameId'):
                return jsonify({"success": False, "error": "Target frame is required when Auto Add New Media is enabled"}), 400
        
        # Preserve the ID
        updated_location['id'] = location_id
        
        # Update the location
        data['locations'][location_index] = updated_location
        
        # Save the updated data
        if save_network_locations(data):
            return jsonify({"success": True, "message": "Network location updated successfully"})
        else:
            return jsonify({"success": False, "error": "Failed to save network location"}), 500
    except Exception as e:
        logging.error(f"Error updating network location: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to delete a network location
@integration_routes.route('/api/network/locations/<location_id>', methods=['DELETE'])
def delete_network_location(location_id):
    try:
        data = load_network_locations()
        
        # Find the location to delete
        location_index = None
        for i, location in enumerate(data.get('locations', [])):
            if location.get('id') == location_id:
                location_index = i
                break
        
        if location_index is None:
            return jsonify({"success": False, "error": "Network location not found"}), 404
        
        # Remove the location
        data['locations'].pop(location_index)
        
        # Save the updated data
        if save_network_locations(data):
            return jsonify({"success": True, "message": "Network location deleted successfully"})
        else:
            return jsonify({"success": False, "error": "Failed to delete network location"}), 500
    except Exception as e:
        logging.error(f"Error deleting network location: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to test a network connection
@integration_routes.route('/api/network/test-connection', methods=['POST'])
def test_network_connection():
    try:
        location_data = request.json
        
        # Validate required fields
        if not location_data.get('network_path'):
            return jsonify({"success": False, "error": "Network path is required"}), 400
        
        # Get connection details
        network_path = location_data.get('network_path')
        username = location_data.get('username', '')
        password = location_data.get('password', '')
        
        # Parse the network path to extract server and share information
        # Expected format: \\server\share\path or //server/share/path
        network_path = network_path.replace('\\', '/')
        if network_path.startswith('//'):
            network_path = network_path[2:]
        
        parts = network_path.split('/')
        if len(parts) < 2:
            return jsonify({"success": False, "error": "Invalid network path format. Expected format: \\\\server\\share or //server/share"}), 400
        
        server_name = parts[0]
        share_name = parts[1]
        path = '/'.join(parts[2:]) if len(parts) > 2 else ''
        
        # Try to resolve the server name to an IP address
        # For local network connections, this might return the original server name
        server_ip = resolve_server_name(server_name)
        
        logging.info(f"Testing connection to server '{server_name}' (resolved to '{server_ip}') with share '{share_name}'")
        
        if SMB_AVAILABLE:
            # Create SMB connection
            conn = SMBConnection(
                username,
                password,
                'PhotoServer',  # Client name
                server_name,    # Server name
                use_ntlm_v2=True,
                is_direct_tcp=False  # Use NetBIOS over TCP/IP instead of direct TCP
            )
            
            try:
                # First try to connect using NetBIOS over TCP/IP (port 139)
                logging.info(f"Attempting to connect to {server_name} (resolved to '{server_ip}') using NetBIOS over TCP/IP (port 139)")
                connected = conn.connect(server_ip, 139)
                
                if not connected:
                    # If NetBIOS connection fails, try direct TCP (port 445)
                    logging.info(f"NetBIOS connection failed, trying direct TCP (port 445)")
                    conn = SMBConnection(
                        username,
                        password,
                        'PhotoServer',  # Client name
                        server_name,    # Server name
                        use_ntlm_v2=True,
                        is_direct_tcp=True
                    )
                    connected = conn.connect(server_ip, 445)
                    
                    if not connected:
                        # If both connection methods fail, try connecting using the original server name
                        if server_ip != server_name:
                            logging.info(f"Direct TCP connection failed, trying with original server name '{server_name}'")
                            conn = SMBConnection(
                                username,
                                password,
                                'PhotoServer',  # Client name
                                server_name,    # Server name
                                use_ntlm_v2=True,
                                is_direct_tcp=False
                            )
                            connected = conn.connect(server_name, 139)
                            
                            if not connected:
                                conn = SMBConnection(
                                    username,
                                    password,
                                    'PhotoServer',  # Client name
                                    server_name,    # Server name
                                    use_ntlm_v2=True,
                                    is_direct_tcp=True
                                )
                                connected = conn.connect(server_name, 445)
                        
                        if not connected:
                            return jsonify({"success": False, "error": "Failed to connect to the server using both NetBIOS and direct TCP"}), 400
                
                # Try to list files in the share to verify access
                try:
                    conn.listPath(share_name, '/' + path)
                    conn.close()
                    return jsonify({"success": True, "message": "Connection successful"})
                except Exception as e:
                    conn.close()
                    return jsonify({"success": False, "error": f"Connected to server but failed to access share: {str(e)}"}), 403
            except socket.gaierror as e:
                # Handle DNS resolution errors
                logging.error(f"DNS resolution error: {str(e)}")
                return jsonify({"success": False, "error": f"Could not resolve server name '{server_name}'. Please check the network path and ensure the server is accessible."}), 400
            except ConnectionRefusedError:
                return jsonify({"success": False, "error": f"Connection refused by server '{server_name}' (resolved to '{server_ip}'). Please check if the server is running and accepting SMB connections."}), 400
            except TimeoutError:
                return jsonify({"success": False, "error": f"Connection to server '{server_name}' (resolved to '{server_ip}') timed out. Please check if the server is accessible on the network."}), 400
            except Exception as e:
                logging.error(f"SMB connection error: {str(e)}")
                return jsonify({"success": False, "error": f"Failed to connect to server: {str(e)}"}), 400
        else:
            # Fall back to direct file system access if SMB libraries are not available
            logging.warning("SMB libraries not available, falling back to direct file system access")
            
            # Check if the path exists and is accessible using direct file system access
            if not os.path.exists(network_path):
                return jsonify({"success": False, "error": f"Path not found: {network_path}"}), 404
            
            # Try to list the directory to verify access
            try:
                os.listdir(network_path)
                return jsonify({"success": True, "message": "Connection successful (direct file system)"})
            except PermissionError:
                return jsonify({"success": False, "error": "Permission denied. Check username and password if authentication is required."}), 403
            except Exception as e:
                return jsonify({"success": False, "error": f"Error accessing directory: {str(e)}"}), 500
                
    except Exception as e:
        logging.error(f"Error testing network connection: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to list files in a network location
@integration_routes.route('/api/network/browse', methods=['POST', 'GET'])
def browse_network_location():
    try:
        # Check if this is a GET request for a file preview
        if request.method == 'GET' and request.args.get('preview') == 'true':
            location_id = request.args.get('location_id')
            path = request.args.get('path', '')
            return serve_network_file_preview(location_id, path)
        
        # Handle normal browsing (POST request)
        location_id = request.json.get('location_id') if request.method == 'POST' else request.args.get('location_id')
        path = request.json.get('path', '') if request.method == 'POST' else request.args.get('path', '')
        
        if not location_id:
            return jsonify({"success": False, "error": "Location ID is required"}), 400
        
        # Load network locations
        data = load_network_locations()
        
        # Find the requested location
        location = None
        for loc in data.get('locations', []):
            if loc.get('id') == location_id:
                location = loc
                break
        
        if not location:
            return jsonify({"success": False, "error": "Network location not found"}), 404
        
        # Get connection details
        network_path = location.get('network_path')
        username = location.get('username', '')
        password = location.get('password', '')
        
        # Parse the network path to extract server and share information
        # Expected format: \\server\share\path or //server/share/path
        network_path_normalized = network_path.replace('\\', '/')
        if network_path_normalized.startswith('//'):
            network_path_normalized = network_path_normalized[2:]
        
        parts = network_path_normalized.split('/')
        if len(parts) < 2:
            return jsonify({"success": False, "error": "Invalid network path format. Expected format: \\\\server\\share or //server/share"}), 400
        
        server_name = parts[0]
        share_name = parts[1]
        base_path = '/'.join(parts[2:]) if len(parts) > 2 else ''
        
        # Try to resolve the server name to an IP address
        # For local network connections, this might return the original server name
        server_ip = resolve_server_name(server_name)
        
        logging.info(f"Browsing server '{server_name}' (resolved to '{server_ip}') with share '{share_name}', path '{path}'")
        
        # Combine base path with requested path
        full_path = base_path
        if path:
            full_path = f"{base_path}/{path}" if base_path else path
        
        if SMB_AVAILABLE:
            # Create SMB connection
            conn = SMBConnection(
                username,
                password,
                'PhotoServer',  # Client name
                server_name,    # Server name
                use_ntlm_v2=True,
                is_direct_tcp=False  # Use NetBIOS over TCP/IP instead of direct TCP
            )
            
            try:
                # First try to connect using NetBIOS over TCP/IP (port 139)
                logging.info(f"Attempting to connect to {server_name} (resolved to '{server_ip}') using NetBIOS over TCP/IP (port 139)")
                connected = conn.connect(server_ip, 139)
                
                if not connected:
                    # If NetBIOS connection fails, try direct TCP (port 445)
                    logging.info(f"NetBIOS connection failed, trying direct TCP (port 445)")
                    conn = SMBConnection(
                        username,
                        password,
                        'PhotoServer',  # Client name
                        server_name,    # Server name
                        use_ntlm_v2=True,
                        is_direct_tcp=True
                    )
                    connected = conn.connect(server_ip, 445)
                
                if not connected:
                    # If both connection methods fail, try connecting using the original server name
                    if server_ip != server_name:
                        logging.info(f"Direct TCP connection failed, trying with original server name '{server_name}'")
                        conn = SMBConnection(
                            username,
                            password,
                            'PhotoServer',  # Client name
                            server_name,    # Server name
                            use_ntlm_v2=True,
                            is_direct_tcp=False
                        )
                        connected = conn.connect(server_name, 139)
                        
                        if not connected:
                            conn = SMBConnection(
                                username,
                                password,
                                'PhotoServer',  # Client name
                                server_name,    # Server name
                                use_ntlm_v2=True,
                                is_direct_tcp=True
                            )
                            connected = conn.connect(server_name, 445)
                    
                    if not connected:
                        return jsonify({"success": False, "error": "Failed to connect to the server using both NetBIOS and direct TCP"}), 400
                
                # List files and directories
                items = []
                smb_path = '/' + full_path if full_path else '/'
                
                try:
                    file_list = conn.listPath(share_name, smb_path)
                    
                    for file_info in file_list:
                        # Skip . and .. entries
                        if file_info.filename in ['.', '..']:
                            continue
                        
                        is_dir = file_info.isDirectory
                        
                        # Only include directories and image files
                        if is_dir or any(file_info.filename.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']):
                            items.append({
                                "name": file_info.filename,
                                "path": f"{path}/{file_info.filename}" if path else file_info.filename,
                                "is_dir": is_dir,
                                "size": file_info.file_size if not is_dir else 0,
                                "modified": datetime.fromtimestamp(file_info.last_write_time).isoformat()
                            })
                
                    # Sort items: directories first, then files
                    items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
                    
                    conn.close()
                    
                    return jsonify({
                        "success": True,
                        "location": {
                            "id": location.get('id'),
                            "name": location.get('name')
                        },
                        "current_path": path,
                        "items": items
                    })
                    
                except Exception as e:
                    conn.close()
                    return jsonify({"success": False, "error": f"Error listing files: {str(e)}"}), 500
            except socket.gaierror as e:
                # Handle DNS resolution errors
                logging.error(f"DNS resolution error: {str(e)}")
                return jsonify({"success": False, "error": f"Could not resolve server name '{server_name}'. Please check the network path and ensure the server is accessible."}), 400
            except ConnectionRefusedError:
                return jsonify({"success": False, "error": f"Connection refused by server '{server_name}' (resolved to '{server_ip}'). Please check if the server is running and accepting SMB connections."}), 400
            except TimeoutError:
                return jsonify({"success": False, "error": f"Connection to server '{server_name}' (resolved to '{server_ip}') timed out. Please check if the server is accessible on the network."}), 400
            except Exception as e:
                logging.error(f"SMB connection error: {str(e)}")
                return jsonify({"success": False, "error": f"Failed to connect to server: {str(e)}"}), 400
        else:
            # Fall back to direct file system access if SMB libraries are not available
            logging.warning("SMB libraries not available, falling back to direct file system access")
            
            # Construct the full path for direct file system access
            full_path = os.path.join(network_path, path) if path else network_path
            
            # Check if the path exists and is accessible
            if not os.path.exists(full_path):
                return jsonify({"success": False, "error": f"Path not found: {full_path}"}), 404
            
            # List files and directories
            items = []
            for item in os.listdir(full_path):
                item_path = os.path.join(full_path, item)
                is_dir = os.path.isdir(item_path)
                
                # Only include directories and image files
                if is_dir or any(item.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']):
                    items.append({
                        "name": item,
                        "path": os.path.join(path, item) if path else item,
                        "is_dir": is_dir,
                        "size": os.path.getsize(item_path) if not is_dir else 0,
                        "modified": datetime.fromtimestamp(os.path.getmtime(item_path)).isoformat()
                    })
            
            # Sort items: directories first, then files
            items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
            
            return jsonify({
                "success": True,
                "location": {
                    "id": location.get('id'),
                    "name": location.get('name')
                },
                "current_path": path,
                "items": items
            })
            
    except Exception as e:
        logging.error(f"Error browsing network location: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Function to serve network file previews
def serve_network_file_preview(location_id, path):
    try:
        # Load network locations
        data = load_network_locations()
        
        # Find the requested location
        location = None
        for loc in data.get('locations', []):
            if loc.get('id') == location_id:
                location = loc
                break
        
        if not location:
            abort(404)
        
        # Get connection details
        network_path = location.get('network_path')
        username = location.get('username', '')
        password = location.get('password', '')
        
        # Parse the network path
        network_path_normalized = network_path.replace('\\', '/')
        if network_path_normalized.startswith('//'):
            network_path_normalized = network_path_normalized[2:]
        
        parts = network_path_normalized.split('/')
        if len(parts) < 2:
            abort(400)
        
        server_name = parts[0]
        share_name = parts[1]
        base_path = '/'.join(parts[2:]) if len(parts) > 2 else ''
        
        # Combine base path with requested path
        full_path = base_path
        if path:
            full_path = f"{base_path}/{path}" if base_path else path
        
        # Try to resolve the server name to an IP address
        server_ip = resolve_server_name(server_name)
        
        # Generate a thumbnail of the image
        if SMB_AVAILABLE:
            # Create SMB connection
            conn = SMBConnection(
                username,
                password,
                'PhotoServer',
                server_name,
                use_ntlm_v2=True,
                is_direct_tcp=False
            )
            
            # Try different connection methods
            connected = False
            try:
                connected = conn.connect(server_ip, 139)
            except:
                pass
                
            if not connected:
                try:
                    conn = SMBConnection(
                        username,
                        password,
                        'PhotoServer',
                        server_name,
                        use_ntlm_v2=True,
                        is_direct_tcp=True
                    )
                    connected = conn.connect(server_ip, 445)
                except:
                    pass
            
            if not connected and server_ip != server_name:
                try:
                    conn = SMBConnection(
                        username,
                        password,
                        'PhotoServer',
                        server_name,
                        use_ntlm_v2=True,
                        is_direct_tcp=False
                    )
                    connected = conn.connect(server_name, 139)
                except:
                    pass
                    
                if not connected:
                    try:
                        conn = SMBConnection(
                            username,
                            password,
                            'PhotoServer',
                            server_name,
                            use_ntlm_v2=True,
                            is_direct_tcp=True
                        )
                        connected = conn.connect(server_name, 445)
                    except:
                        pass
            
            if not connected:
                abort(500)
            
            # Download the file to memory
            file_obj = io.BytesIO()
            smb_path = '/' + full_path if full_path else '/'
            file_attributes, file_size = conn.retrieveFile(share_name, smb_path, file_obj)
            
            # Generate a thumbnail
            file_obj.seek(0)
            try:
                img = Image.open(file_obj)
                img.thumbnail((300, 300))  # Create a small thumbnail
                thumbnail_io = io.BytesIO()
                img.save(thumbnail_io, format=img.format or 'JPEG')
                thumbnail_io.seek(0)
                
                conn.close()
                return send_file(thumbnail_io, mimetype=f'image/{img.format.lower() if img.format else "jpeg"}')
            except Exception as e:
                logging.error(f"Error creating thumbnail: {str(e)}")
                conn.close()
                abort(500)
        else:
            # Fall back to direct file system access
            full_path = os.path.join(network_path, path) if path else network_path
            
            if not os.path.exists(full_path) or not os.path.isfile(full_path):
                abort(404)
                
            try:
                img = Image.open(full_path)
                img.thumbnail((300, 300))  # Create a small thumbnail
                thumbnail_io = io.BytesIO()
                img.save(thumbnail_io, format=img.format or 'JPEG')
                thumbnail_io.seek(0)
                return send_file(thumbnail_io, mimetype=f'image/{img.format.lower() if img.format else "jpeg"}')
            except Exception as e:
                logging.error(f"Error creating thumbnail: {str(e)}")
                abort(500)
                
    except Exception as e:
        logging.error(f"Error serving network file preview: {str(e)}")
        abort(500)

# Route to import files from a network location
@integration_routes.route('/api/network/import', methods=['POST'])
def import_from_network():
    try:
        location_id = request.json.get('location_id')
        files = request.json.get('files', [])
        frame_id = request.json.get('frame_id')
        
        if not location_id or not files:
            return jsonify({"success": False, "error": "Location ID and files are required"}), 400
        
        if not frame_id:
            return jsonify({"success": False, "error": "Frame ID is required"}), 400
        
        # Load network locations
        data = load_network_locations()
        
        # Find the requested location
        location = None
        for loc in data.get('locations', []):
            if loc.get('id') == location_id:
                location = loc
                break
        
        if not location:
            return jsonify({"success": False, "error": "Network location not found"}), 404
        
        # Get connection details
        network_path = location.get('network_path')
        username = location.get('username', '')
        password = location.get('password', '')
        
        # Get the upload directory from app config
        upload_dir = current_app.config['UPLOAD_FOLDER']
        os.makedirs(upload_dir, exist_ok=True)
        
        # Parse the network path to extract server and share information
        network_path_normalized = network_path.replace('\\', '/')
        if network_path_normalized.startswith('//'):
            network_path_normalized = network_path_normalized[2:]
        
        parts = network_path_normalized.split('/')
        if len(parts) < 2:
            return jsonify({"success": False, "error": "Invalid network path format. Expected format: \\\\server\\share or //server/share"}), 400
        
        server_name = parts[0]
        share_name = parts[1]
        base_path = '/'.join(parts[2:]) if len(parts) > 2 else ''
        
        # Try to resolve the server name to an IP address
        server_ip = resolve_server_name(server_name)
        if server_ip is None:
            return jsonify({"success": False, "error": f"Could not resolve server name '{server_name}'. Please check the network path and ensure the server is accessible."}), 400
        
        logging.info(f"Importing files from server '{server_name}' ({server_ip}) with share '{share_name}'")
        
        # Import each file
        imported_files = []
        
        # Import the necessary modules from server.py
        # We'll use these inside the app context to ensure proper database connections
        from server import app, db, Photo, PlaylistEntry, photo_processor, generate_video_thumbnail
        
        if SMB_AVAILABLE:
            # Create SMB connection
            conn = SMBConnection(
                username,
                password,
                'PhotoServer',  # Client name
                server_name,    # Server name
                use_ntlm_v2=True,
                is_direct_tcp=False  # Use NetBIOS over TCP/IP instead of direct TCP
            )
            
            try:
                # First try to connect using NetBIOS over TCP/IP (port 139)
                logging.info(f"Attempting to connect to {server_name} ({server_ip}) using NetBIOS over TCP/IP (port 139)")
                connected = conn.connect(server_ip, 139)
                
                if not connected:
                    # If NetBIOS connection fails, try direct TCP (port 445)
                    logging.info(f"NetBIOS connection failed, trying direct TCP (port 445)")
                    conn = SMBConnection(
                        username,
                        password,
                        'PhotoServer',  # Client name
                        server_name,    # Server name
                        use_ntlm_v2=True,
                        is_direct_tcp=True
                    )
                    connected = conn.connect(server_ip, 445)
                    
                    if not connected:
                        return jsonify({"success": False, "error": "Failed to connect to the server using both NetBIOS and direct TCP"}), 400
                
                # Import each file using SMB
                for file_path in files:
                    try:
                        # Normalize file path for SMB
                        smb_file_path = file_path.replace('\\', '/')
                        if smb_file_path.startswith('/'):
                            smb_file_path = smb_file_path[1:]
                        
                        # Combine with base path if needed
                        if base_path:
                            full_smb_path = f"{base_path}/{smb_file_path}"
                        else:
                            full_smb_path = smb_file_path
                        
                        # Extract directory and filename
                        path_parts = full_smb_path.split('/')
                        filename = path_parts[-1]
                        
                        # Download the file from SMB share
                        file_obj = BytesIO()
                        file_attributes, file_size = conn.retrieveFile(share_name, '/' + full_smb_path, file_obj)
                        
                        if file_size == 0:
                            continue  # Skip empty files
                        
                        # Generate a unique filename
                        secure_filename_value = secure_filename(filename)
                        unique_filename = f"{uuid.uuid4()}_{secure_filename_value}"
                        dest_path = os.path.join(upload_dir, unique_filename)
                        
                        # Save the file
                        file_obj.seek(0)
                        with open(dest_path, 'wb') as f:
                            f.write(file_obj.read())
                        
                        # Check if it's a HEIC file and convert to JPG if needed
                        if dest_path.lower().endswith(('.heic', '.HEIC')):
                            logging.info(f"Converting HEIC file to JPG: {dest_path}")
                            converted_path, was_converted = convert_heic_to_jpg(dest_path)
                            
                            if was_converted:
                                # Update the filename and path
                                dest_path = converted_path
                                filename = os.path.basename(dest_path)
                                unique_filename = os.path.basename(dest_path)
                                logging.info(f"Using converted JPG file: {dest_path}")
                        
                        # Determine if it's a video
                        is_video = filename.lower().endswith(('.mp4', '.mov', '.avi', '.webm'))
                        
                        # Extract EXIF metadata if it's an image
                        exif_metadata = None
                        if not is_video:
                            from server import extract_exif_metadata
                            exif_metadata = extract_exif_metadata(dest_path)
                        
                        # Use the app's application context for database operations
                        with app.app_context():
                            # Create a new Photo record
                            new_photo = Photo(
                                filename=unique_filename,
                                media_type='video' if is_video else 'photo',
                                exif_metadata=exif_metadata
                            )
                            
                            db.session.add(new_photo)
                            db.session.commit()
                            
                            # Add to playlist if frame_id is provided
                            if frame_id:
                                # Shift existing entries
                                PlaylistEntry.query.filter_by(frame_id=frame_id)\
                                    .update({PlaylistEntry.order: PlaylistEntry.order + 1})
                                
                                # Add new entry at the beginning
                                entry = PlaylistEntry(
                                    frame_id=frame_id,
                                    photo_id=new_photo.id,
                                    order=0
                                )
                                db.session.add(entry)
                                db.session.commit()
                            
                            # Process the file with the appropriate function based on type
                            if is_video:
                                # For videos, generate thumbnail
                                # Create thumbnails directory if it doesn't exist
                                thumbnails_dir = os.path.join(upload_dir, 'thumbnails')
                                os.makedirs(thumbnails_dir, exist_ok=True)
                                
                                # Generate thumbnail
                                thumb_filename = f"thumb_{unique_filename}.jpg"
                                thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                                
                                if generate_video_thumbnail(dest_path, thumb_path):
                                    # Update photo record with thumbnail
                                    new_photo.thumbnail = thumb_filename
                                    db.session.commit()
                            else:
                                # For images, generate thumbnail and orientation versions
                                try:
                                    # Create thumbnails directory if it doesn't exist
                                    thumbnails_dir = os.path.join(upload_dir, 'thumbnails')
                                    os.makedirs(thumbnails_dir, exist_ok=True)
                                    
                                    # Generate thumbnail
                                    with Image.open(dest_path) as img:
                                        img.thumbnail((400, 400))
                                        thumb_filename = f"thumb_{unique_filename}"
                                        thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                                        img.save(thumb_path, "JPEG")
                                        
                                        # Update photo record with thumbnail
                                        new_photo.thumbnail = thumb_filename
                                        db.session.commit()
                                    
                                    # Process for orientations
                                    portrait_path = photo_processor.process_for_orientation(dest_path, 'portrait')
                                    if portrait_path:
                                        new_photo.portrait_version = os.path.basename(portrait_path)
                                    
                                    landscape_path = photo_processor.process_for_orientation(dest_path, 'landscape')
                                    if landscape_path:
                                        new_photo.landscape_version = os.path.basename(landscape_path)
                                    
                                    db.session.commit()
                                except Exception as e:
                                    logging.error(f"Error processing image: {str(e)}")
                            
                            # Check if AI analysis is enabled
                            from server import load_server_settings
                            server_settings = load_server_settings()
                            if server_settings.get('ai_analysis_enabled', False):
                                # Run analysis in a background thread
                                def async_analyze(app, db, photo_id):
                                    with app.app_context():
                                        try:
                                            from server import PhotoAnalyzer
                                            photo_analyzer = PhotoAnalyzer(app, db)
                                            photo_analyzer.analyze_photo(photo_id)
                                        except Exception as e:
                                            logging.error(f"Background analysis failed: {e}")
                                
                                from threading import Thread
                                Thread(target=async_analyze, args=(app, db, new_photo.id)).start()
                            
                            # Add to the list of imported files
                            imported_files.append({
                                "id": new_photo.id,
                                "original_name": filename,
                                "saved_name": unique_filename,
                                "message": "File imported successfully"
                            })
                        
                    except Exception as e:
                        logging.error(f"Error importing file {file_path} via SMB: {str(e)}")
                        continue
                
                # Close the SMB connection
                conn.close()
            except socket.gaierror as e:
                # Handle DNS resolution errors
                logging.error(f"DNS resolution error: {str(e)}")
                return jsonify({"success": False, "error": f"Could not resolve server name '{server_name}'. Please check the network path and ensure the server is accessible."}), 400
            except ConnectionRefusedError:
                return jsonify({"success": False, "error": f"Connection refused by server '{server_name}' ({server_ip}). Please check if the server is running and accepting SMB connections."}), 400
            except TimeoutError:
                return jsonify({"success": False, "error": f"Connection to server '{server_name}' ({server_ip}) timed out. Please check if the server is accessible on the network."}), 400
            except Exception as e:
                logging.error(f"SMB connection error: {str(e)}")
                return jsonify({"success": False, "error": f"Failed to connect to server: {str(e)}"}), 400
        else:
            # Fall back to direct file system access if SMB libraries are not available
            logging.warning("SMB libraries not available, falling back to direct file system access")
            
            # Import each file using direct file system access
            for file_path in files:
                try:
                    # Construct the full source path
                    source_path = os.path.join(network_path, file_path)
                    
                    # Check if the file exists and is accessible
                    if not os.path.exists(source_path) or not os.path.isfile(source_path):
                        continue
                    
                    # Get the filename
                    filename = os.path.basename(source_path)
                    
                    # Generate a unique filename
                    secure_filename_value = secure_filename(filename)
                    unique_filename = f"{uuid.uuid4()}_{secure_filename_value}"
                    dest_path = os.path.join(upload_dir, unique_filename)
                    
                    # Copy the file
                    shutil.copy2(source_path, dest_path)
                    
                    # Check if it's a HEIC file and convert to JPG if needed
                    if dest_path.lower().endswith(('.heic', '.HEIC')):
                        logging.info(f"Converting HEIC file to JPG: {dest_path}")
                        converted_path, was_converted = convert_heic_to_jpg(dest_path)
                        
                        if was_converted:
                            # Update the filename and path
                            dest_path = converted_path
                            filename = os.path.basename(dest_path)
                            unique_filename = os.path.basename(dest_path)
                            logging.info(f"Using converted JPG file: {dest_path}")
                    
                    # Determine if it's a video
                    is_video = filename.lower().endswith(('.mp4', '.mov', '.avi', '.webm'))
                    
                    # Extract EXIF metadata if it's an image
                    exif_metadata = None
                    if not is_video:
                        from server import extract_exif_metadata
                        exif_metadata = extract_exif_metadata(dest_path)
                    
                    # Use the app's application context for database operations
                    with app.app_context():
                        # Create a new Photo record
                        new_photo = Photo(
                            filename=unique_filename,
                            original_filename=filename,
                            media_type='video' if is_video else 'photo',
                            heading=f"Imported from {location.get('name')}",
                            exif_metadata=exif_metadata
                        )
                        
                        db.session.add(new_photo)
                        db.session.commit()
                        
                        # Add to playlist if frame_id is provided
                        if frame_id:
                            # Shift existing entries
                            PlaylistEntry.query.filter_by(frame_id=frame_id)\
                                .update({PlaylistEntry.order: PlaylistEntry.order + 1})
                            
                            # Add new entry at the beginning
                            entry = PlaylistEntry(
                                frame_id=frame_id,
                                photo_id=new_photo.id,
                                order=0
                            )
                            db.session.add(entry)
                            db.session.commit()
                        
                        # Process the file with the appropriate function based on type
                        if is_video:
                            # For videos, generate thumbnail
                            # Create thumbnails directory if it doesn't exist
                            thumbnails_dir = os.path.join(upload_dir, 'thumbnails')
                            os.makedirs(thumbnails_dir, exist_ok=True)
                            
                            # Generate thumbnail
                            thumb_filename = f"thumb_{unique_filename}.jpg"
                            thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                            
                            if generate_video_thumbnail(dest_path, thumb_path):
                                # Update photo record with thumbnail
                                new_photo.thumbnail = thumb_filename
                                db.session.commit()
                        else:
                            # For images, generate thumbnail and orientation versions
                            try:
                                # Create thumbnails directory if it doesn't exist
                                thumbnails_dir = os.path.join(upload_dir, 'thumbnails')
                                os.makedirs(thumbnails_dir, exist_ok=True)
                                
                                # Generate thumbnail
                                with Image.open(dest_path) as img:
                                    img.thumbnail((400, 400))
                                    thumb_filename = f"thumb_{unique_filename}"
                                    thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                                    img.save(thumb_path, "JPEG")
                                    
                                    # Update photo record with thumbnail
                                    new_photo.thumbnail = thumb_filename
                                    db.session.commit()
                                
                                # Process for orientations
                                portrait_path = photo_processor.process_for_orientation(dest_path, 'portrait')
                                if portrait_path:
                                    new_photo.portrait_version = os.path.basename(portrait_path)
                                
                                landscape_path = photo_processor.process_for_orientation(dest_path, 'landscape')
                                if landscape_path:
                                    new_photo.landscape_version = os.path.basename(landscape_path)
                                
                                db.session.commit()
                            except Exception as e:
                                logging.error(f"Error processing image: {str(e)}")
                        
                        # Check if AI analysis is enabled
                        from server import load_server_settings
                        server_settings = load_server_settings()
                        if server_settings.get('ai_analysis_enabled', False):
                            # Run analysis in a background thread
                            def async_analyze(app, db, photo_id):
                                with app.app_context():
                                    try:
                                        from server import PhotoAnalyzer
                                        photo_analyzer = PhotoAnalyzer(app, db)
                                        photo_analyzer.analyze_photo(photo_id)
                                    except Exception as e:
                                        logging.error(f"Background analysis failed: {e}")
                            
                            from threading import Thread
                            Thread(target=async_analyze, args=(app, db, new_photo.id)).start()
                        
                        # Add to the list of imported files
                        imported_files.append({
                            "id": new_photo.id,
                            "original_name": filename,
                            "saved_name": unique_filename,
                            "message": "File imported successfully"
                        })
                    
                except Exception as e:
                    logging.error(f"Error importing file {file_path}: {str(e)}")
                    continue
        
        return jsonify({
            "success": True,
            "message": f"Successfully imported {len(imported_files)} files",
            "imported_files": imported_files
        })
    except Exception as e:
        logging.error(f"Error importing from network: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to discover network servers and shares
@integration_routes.route('/api/network/discover', methods=['GET'])
def discover_network_shares():
    """Discover available SMB/CIFS shares on the local network."""
    try:
        shares = []
        
        if SMB_AVAILABLE:
            # Use NetBIOS to discover servers
            servers = discover_smb_servers()
            
            # For each server, try to discover shares
            for server in servers:
                server_shares = discover_server_shares(server)
                shares.extend(server_shares)
        else:
            # Try using system commands if SMB libraries are not available
            try:
                if os.name == 'nt':  # Windows
                    shares = discover_shares_windows()
                else:  # Linux/macOS
                    shares = discover_shares_linux()
            except Exception as e:
                logging.error(f"Error discovering shares using system commands: {str(e)}")
        
        return jsonify({"success": True, "shares": shares})
    except Exception as e:
        logging.error(f"Error discovering network shares: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

def discover_smb_servers():
    """Discover SMB servers on the local network using NetBIOS."""
    servers = []
    try:
        # Create a NetBIOS instance
        nb = nmb.NetBIOS.NetBIOS()
        
        # Send NetBIOS query
        query_results = nb.queryName('*', timeout=5)
        
        if query_results:
            for result in query_results:
                server_name = result.decode('utf-8').strip()
                if server_name and server_name not in servers:
                    servers.append(server_name)
        
        # If no servers found via NetBIOS, try additional methods
        if not servers:
            # Try to get local hostname
            local_hostname = socket.gethostname()
            local_ip = socket.gethostbyname(local_hostname)
            
            # Get local network prefix
            ip_parts = local_ip.split('.')
            network_prefix = '.'.join(ip_parts[0:3])
            
            # Scan a few common addresses
            for i in range(1, 10):
                ip = f"{network_prefix}.{i}"
                try:
                    hostname = socket.gethostbyaddr(ip)[0]
                    if hostname and hostname not in servers:
                        servers.append(hostname)
                except:
                    pass
    except Exception as e:
        logging.error(f"Error discovering SMB servers: {str(e)}")
    
    return servers

def discover_server_shares(server_name):
    """Discover shares on a specific SMB server."""
    shares = []
    
    try:
        # Try to resolve the server name to an IP address
        server_ip = resolve_server_name(server_name)
        
        # Create SMB connection
        conn = SMBConnection(
            '',  # Empty username for guest access
            '',  # Empty password for guest access
            'PhotoServer',  # Client name
            server_name,    # Server name
            use_ntlm_v2=True,
            is_direct_tcp=False
        )
        
        # Try to connect
        connected = False
        try:
            connected = conn.connect(server_ip, 139)
        except:
            try:
                conn = SMBConnection(
                    '',  # Empty username for guest access
                    '',  # Empty password for guest access
                    'PhotoServer',  # Client name
                    server_name,    # Server name
                    use_ntlm_v2=True,
                    is_direct_tcp=True
                )
                connected = conn.connect(server_ip, 445)
            except:
                pass
        
        if connected:
            # Get list of shares
            share_list = conn.listShares()
            
            for share in share_list:
                # Skip special and hidden shares
                if not share.isSpecial and not share.name.endswith('$'):
                    shares.append({
                        "server": server_name,
                        "name": share.name,
                        "comment": share.comments,
                        "path": f"\\\\{server_name}\\{share.name}"
                    })
            
            conn.close()
    except Exception as e:
        logging.error(f"Error discovering shares on server {server_name}: {str(e)}")
    
    return shares

def discover_shares_windows():
    """Discover network shares using Windows 'net view' command."""
    shares = []
    
    try:
        # Get list of servers
        output = subprocess.check_output(['net', 'view'], universal_newlines=True)
        lines = output.splitlines()
        
        servers = []
        for line in lines:
            if '\\\\' in line:
                server = line.strip().split('\\\\')[1].split(' ')[0]
                servers.append(server)
        
        # For each server, get shares
        for server in servers:
            try:
                output = subprocess.check_output(['net', 'view', f'\\\\{server}'], universal_newlines=True)
                lines = output.splitlines()
                
                for line in lines:
                    if 'Disk' in line and not '$' in line:  # Skip hidden shares
                        parts = [p for p in line.split(' ') if p]
                        if len(parts) >= 2:
                            share_name = parts[0]
                            shares.append({
                                "server": server,
                                "name": share_name,
                                "comment": ' '.join(parts[1:]),
                                "path": f"\\\\{server}\\{share_name}"
                            })
            except:
                pass
    except Exception as e:
        logging.error(f"Error discovering shares using Windows commands: {str(e)}")
    
    return shares

def discover_shares_linux():
    """Discover network shares using Linux 'smbclient' command."""
    shares = []
    
    try:
        # Get list of servers using nmblookup
        output = subprocess.check_output(['nmblookup', '-S', '*'], universal_newlines=True)
        lines = output.splitlines()
        
        servers = []
        for line in lines:
            if '<SERVER>' in line:
                parts = line.split(' ')
                if len(parts) >= 2:
                    server = parts[0].strip()
                    if server not in servers:
                        servers.append(server)
        
        # For each server, get shares
        for server in servers:
            try:
                output = subprocess.check_output(['smbclient', '-N', '-L', server], universal_newlines=True)
                lines = output.splitlines()
                
                for line in lines:
                    if 'Disk' in line and not '$' in line:  # Skip hidden shares
                        parts = line.split(' ')
                        parts = [p for p in parts if p]
                        if len(parts) >= 2:
                            share_name = parts[0]
                            shares.append({
                                "server": server,
                                "name": share_name,
                                "comment": ' '.join(parts[1:]),
                                "path": f"\\\\{server}\\{share_name}"
                            })
            except:
                pass
    except Exception as e:
        logging.error(f"Error discovering shares using Linux commands: {str(e)}")
    
    return shares

# Function to check for new media in network locations and import them
def check_network_locations_for_new_media():
    """
    Check all network locations with auto-add enabled for new media files
    and import them to the specified target frames.
    """
    logging.info("Starting automatic check for new media in network locations")
    
    try:
        # Load network locations
        data = load_network_locations()
        locations = data.get('locations', [])
        
        # Filter locations with auto-add enabled
        auto_add_locations = [loc for loc in locations if loc.get('autoAddNewMedia', False) and loc.get('autoAddTargetFrameId')]
        
        if not auto_add_locations:
            logging.info("No network locations with auto-add enabled")
            return
        
        # Import necessary modules from server.py
        from server import app, db, Photo, PlaylistEntry, photo_processor, extract_exif_metadata, generate_video_thumbnail
        
        # Process each location
        for location in auto_add_locations:
            location_id = location.get('id')
            target_frame_id = location.get('autoAddTargetFrameId')
            network_path = location.get('network_path')
            username = location.get('username', '')
            password = location.get('password', '')
            
            logging.info(f"Checking location '{location.get('name')}' (ID: {location_id}) for new media")
            
            # Load previously imported files
            imported_files = load_imported_files(location_id)
            
            # Get all media files in the location
            media_files = get_media_files_in_location(network_path, username, password)
            
            # Filter out already imported files
            new_files = [f for f in media_files if f not in imported_files]
            
            if not new_files:
                logging.info(f"No new media files found in location '{location.get('name')}'")
                continue
            
            logging.info(f"Found {len(new_files)} new media files in location '{location.get('name')}'")
            
            # Import each new file
            for file_path in new_files:
                try:
                    with app.app_context():
                        # Import the file
                        result = import_file_to_frame(
                            location, 
                            file_path, 
                            target_frame_id, 
                            app, 
                            db, 
                            Photo, 
                            PlaylistEntry, 
                            photo_processor, 
                            extract_exif_metadata, 
                            generate_video_thumbnail
                        )
                        
                        if result:
                            # Add to imported files list
                            imported_files.append(file_path)
                            logging.info(f"Successfully imported file '{file_path}' to frame {target_frame_id}")
                        else:
                            logging.error(f"Failed to import file '{file_path}' to frame {target_frame_id}")
                except Exception as e:
                    logging.error(f"Error importing file '{file_path}': {str(e)}")
            
            # Save updated imported files list
            save_imported_files(location_id, imported_files)
            
        logging.info("Completed automatic check for new media in network locations")
    except Exception as e:
        logging.error(f"Error checking network locations for new media: {str(e)}")

# Function to get all media files in a network location
def get_media_files_in_location(network_path, username='', password=''):
    """Get all media files in a network location recursively."""
    media_files = []
    
    try:
        # Parse the network path
        network_path_normalized = network_path.replace('\\', '/')
        if network_path_normalized.startswith('//'):
            network_path_normalized = network_path_normalized[2:]
        
        parts = network_path_normalized.split('/')
        if len(parts) < 2:
            logging.error(f"Invalid network path format: {network_path}")
            return media_files
        
        server_name = parts[0]
        share_name = parts[1]
        base_path = '/'.join(parts[2:]) if len(parts) > 2 else ''
        
        # Try to resolve the server name to an IP address
        server_ip = resolve_server_name(server_name)
        
        if SMB_AVAILABLE:
            # Use SMB to list files
            conn = SMBConnection(
                username,
                password,
                'PhotoServer',
                server_name,
                use_ntlm_v2=True,
                is_direct_tcp=False
            )
            
            # Try different connection methods
            connected = False
            try:
                connected = conn.connect(server_ip, 139)
            except:
                pass
                
            if not connected:
                try:
                    conn = SMBConnection(
                        username,
                        password,
                        'PhotoServer',
                        server_name,
                        use_ntlm_v2=True,
                        is_direct_tcp=True
                    )
                    connected = conn.connect(server_ip, 445)
                except:
                    pass
            
            if not connected and server_ip != server_name:
                try:
                    conn = SMBConnection(
                        username,
                        password,
                        'PhotoServer',
                        server_name,
                        use_ntlm_v2=True,
                        is_direct_tcp=False
                    )
                    connected = conn.connect(server_name, 139)
                except:
                    pass
                    
                if not connected:
                    try:
                        conn = SMBConnection(
                            username,
                            password,
                            'PhotoServer',
                            server_name,
                            use_ntlm_v2=True,
                            is_direct_tcp=True
                        )
                        connected = conn.connect(server_name, 445)
                    except:
                        pass
            
            if not connected:
                logging.error(f"Failed to connect to server {server_name}")
                return media_files
            
            # List files recursively
            def list_files_recursive(path):
                nonlocal media_files
                smb_path = '/' + path if path else '/'
                
                try:
                    file_list = conn.listPath(share_name, smb_path)
                    
                    for file_info in file_list:
                        # Skip . and .. entries
                        if file_info.filename in ['.', '..']:
                            continue
                        
                        file_path = f"{path}/{file_info.filename}" if path else file_info.filename
                        
                        if file_info.isDirectory:
                            # Recursively list files in subdirectory
                            list_files_recursive(file_path)
                        else:
                            # Check if it's a media file
                            if is_media_file(file_info.filename):
                                media_files.append(file_path)
                except Exception as e:
                    logging.error(f"Error listing files in {path}: {str(e)}")
            
            # Start recursive listing
            list_files_recursive(base_path)
            
            # Close the connection
            conn.close()
        else:
            # Use direct file system access
            full_path = network_path
            
            if not os.path.exists(full_path) or not os.path.isdir(full_path):
                logging.error(f"Path not found or not a directory: {full_path}")
                return media_files
            
            # Walk the directory tree
            for root, dirs, files in os.walk(full_path):
                for file in files:
                    if is_media_file(file):
                        # Get relative path from the network path
                        rel_path = os.path.relpath(os.path.join(root, file), full_path)
                        media_files.append(rel_path.replace('\\', '/'))
    
    except Exception as e:
        logging.error(f"Error getting media files in location: {str(e)}")
    
    return media_files

# Function to check if a file is a supported media file
def is_media_file(filename):
    """Check if a file is a supported media file based on extension."""
    media_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.mp4', '.mov', '.avi', '.webm', '.heic', '.HEIC']
    return any(filename.lower().endswith(ext.lower()) for ext in media_extensions)

# Function to convert HEIC to JPG if needed
def convert_heic_to_jpg(file_path):
    """
    Convert HEIC file to JPG if needed.
    Returns the path to the converted file (or original if no conversion needed).
    """
    if not file_path.lower().endswith(('.heic', '.HEIC')):
        return file_path, False  # No conversion needed
    
    try:
        if not HEIC_SUPPORT:
            logging.warning(f"HEIC conversion requested but pillow-heif not installed. Skipping conversion for {file_path}")
            return file_path, False
        
        # Create a new filename with .jpg extension
        jpg_path = os.path.splitext(file_path)[0] + '.jpg'
        
        # Open and convert the HEIC file
        with Image.open(file_path) as img:
            # Convert to RGB (in case it's RGBA)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Save as JPG with high quality
            img.save(jpg_path, 'JPEG', quality=95)
            
            logging.info(f"Successfully converted HEIC file to JPG: {file_path} -> {jpg_path}")
            
            # Delete the original HEIC file to save space
            os.remove(file_path)
            
            return jpg_path, True
    except Exception as e:
        logging.error(f"Error converting HEIC to JPG: {str(e)}")
        return file_path, False  # Return original path on error

# Function to import a file to a frame
def import_file_to_frame(location, file_path, frame_id, app, db, Photo, PlaylistEntry, photo_processor, extract_exif_metadata, generate_video_thumbnail):
    """Import a file from a network location to a frame."""
    try:
        # Get connection details
        network_path = location.get('network_path')
        username = location.get('username', '')
        password = location.get('password', '')
        
        # Get the upload directory from app config
        upload_dir = app.config['UPLOAD_FOLDER']
        os.makedirs(upload_dir, exist_ok=True)
        
        # Parse the network path
        network_path_normalized = network_path.replace('\\', '/')
        if network_path_normalized.startswith('//'):
            network_path_normalized = network_path_normalized[2:]
        
        parts = network_path_normalized.split('/')
        server_name = parts[0]
        share_name = parts[1]
        base_path = '/'.join(parts[2:]) if len(parts) > 2 else ''
        
        # Normalize file_path to ensure it doesn't include base_path again
        # This prevents the "testFolder\testFolder\" duplication issue
        if base_path and file_path.startswith(base_path):
            # If file_path already includes base_path, don't add it again
            full_smb_path = file_path
        else:
            # Otherwise, combine with base_path if needed
            full_smb_path = f"{base_path}/{file_path}" if base_path else file_path
        
        # Log the paths for debugging
        logging.info(f"Importing file: base_path='{base_path}', file_path='{file_path}', full_smb_path='{full_smb_path}'")
        
        # Extract filename
        filename = os.path.basename(file_path)
        
        # Generate a unique filename
        secure_filename_value = secure_filename(filename)
        unique_filename = f"{uuid.uuid4()}_{secure_filename_value}"
        dest_path = os.path.join(upload_dir, unique_filename)
        
        # Try to resolve the server name to an IP address
        server_ip = resolve_server_name(server_name)
        
        # Download the file
        if SMB_AVAILABLE:
            # Use SMB to download the file
            conn = SMBConnection(
                username,
                password,
                'PhotoServer',
                server_name,
                use_ntlm_v2=True,
                is_direct_tcp=False
            )
            
            # Try different connection methods
            connected = False
            try:
                connected = conn.connect(server_ip, 139)
            except:
                pass
                
            if not connected:
                try:
                    conn = SMBConnection(
                        username,
                        password,
                        'PhotoServer',
                        server_name,
                        use_ntlm_v2=True,
                        is_direct_tcp=True
                    )
                    connected = conn.connect(server_ip, 445)
                except:
                    pass
            
            if not connected and server_ip != server_name:
                try:
                    conn = SMBConnection(
                        username,
                        password,
                        'PhotoServer',
                        server_name,
                        use_ntlm_v2=True,
                        is_direct_tcp=False
                    )
                    connected = conn.connect(server_name, 139)
                except:
                    pass
                    
                if not connected:
                    try:
                        conn = SMBConnection(
                            username,
                            password,
                            'PhotoServer',
                            server_name,
                            use_ntlm_v2=True,
                            is_direct_tcp=True
                        )
                        connected = conn.connect(server_name, 445)
                    except:
                        pass
            
            if not connected:
                logging.error(f"Failed to connect to server {server_name}")
                return False
            
            # Ensure the SMB path starts with a slash
            smb_path = '/' + full_smb_path.replace('\\', '/')
            if smb_path.startswith('//'):
                smb_path = smb_path[1:]
                
            logging.info(f"Retrieving file from share '{share_name}', path '{smb_path}'")
            
            # Download the file
            try:
                file_obj = BytesIO()
                file_attributes, file_size = conn.retrieveFile(share_name, smb_path, file_obj)
                
                if file_size == 0:
                    conn.close()
                    logging.error(f"File is empty: {smb_path}")
                    return False  # Skip empty files
                
                # Save the file
                file_obj.seek(0)
                with open(dest_path, 'wb') as f:
                    f.write(file_obj.read())
                
                conn.close()
            except Exception as e:
                conn.close()
                logging.error(f"Failed to retrieve {smb_path} on {share_name}: {str(e)}")
                return False
        else:
            # Use direct file system access
            # Ensure we don't duplicate the path
            if base_path and file_path.startswith(base_path):
                source_path = os.path.join(network_path, file_path.replace(base_path, '', 1).lstrip('/\\'))
            else:
                source_path = os.path.join(network_path, file_path)
            
            logging.info(f"Accessing file directly at: {source_path}")
            
            if not os.path.exists(source_path) or not os.path.isfile(source_path):
                logging.error(f"File not found: {source_path}")
                return False
            
            # Copy the file
            shutil.copy2(source_path, dest_path)
        
        # Check if it's a HEIC file and convert to JPG if needed
        if dest_path.lower().endswith(('.heic', '.HEIC')):
            logging.info(f"Converting HEIC file to JPG: {dest_path}")
            converted_path, was_converted = convert_heic_to_jpg(dest_path)
            
            if was_converted:
                # Update the filename and path
                dest_path = converted_path
                filename = os.path.basename(dest_path)
                unique_filename = os.path.basename(dest_path)
                logging.info(f"Using converted JPG file: {dest_path}")
        
        # Determine if it's a video
        is_video = filename.lower().endswith(('.mp4', '.mov', '.avi', '.webm'))
        
        # Extract EXIF metadata if it's an image
        exif_metadata = None
        if not is_video:
            exif_metadata = extract_exif_metadata(dest_path)
        
        # Create a new Photo record
        new_photo = Photo(
            filename=unique_filename,
            media_type='video' if is_video else 'photo',
            heading=f"Auto-imported from {location.get('name')}",
            exif_metadata=exif_metadata
        )
        
        db.session.add(new_photo)
        db.session.commit()
        
        # Add to playlist
        # Shift existing entries
        PlaylistEntry.query.filter_by(frame_id=frame_id)\
            .update({PlaylistEntry.order: PlaylistEntry.order + 1})
        
        # Add new entry at the beginning
        entry = PlaylistEntry(
            frame_id=frame_id,
            photo_id=new_photo.id,
            order=1
        )
        db.session.add(entry)
        db.session.commit()
        
        # Process the file with the appropriate function based on type
        if is_video:
            # For videos, generate thumbnail
            thumbnails_dir = os.path.join(upload_dir, 'thumbnails')
            os.makedirs(thumbnails_dir, exist_ok=True)
            
            # Generate thumbnail
            thumb_filename = f"thumb_{unique_filename}.jpg"
            thumb_path = os.path.join(thumbnails_dir, thumb_filename)
            
            if generate_video_thumbnail(dest_path, thumb_path):
                # Update photo record with thumbnail
                new_photo.thumbnail = thumb_filename
                db.session.commit()
        else:
            # For images, generate thumbnail and orientation versions
            try:
                # Create thumbnails directory if it doesn't exist
                thumbnails_dir = os.path.join(upload_dir, 'thumbnails')
                os.makedirs(thumbnails_dir, exist_ok=True)
                
                # Generate thumbnail
                with Image.open(dest_path) as img:
                    img.thumbnail((400, 400))
                    thumb_filename = f"thumb_{unique_filename}"
                    thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                    img.save(thumb_path, "JPEG")
                    
                    # Update photo record with thumbnail
                    new_photo.thumbnail = thumb_filename
                    db.session.commit()
                
                # Process for orientations
                portrait_path = photo_processor.process_for_orientation(dest_path, 'portrait')
                if portrait_path:
                    new_photo.portrait_version = os.path.basename(portrait_path)
                
                landscape_path = photo_processor.process_for_orientation(dest_path, 'landscape')
                if landscape_path:
                    new_photo.landscape_version = os.path.basename(landscape_path)
                
                db.session.commit()
            except Exception as e:
                logging.error(f"Error processing image: {str(e)}")
        
        # Check if AI analysis is enabled
        from server import load_server_settings
        server_settings = load_server_settings()
        if server_settings.get('ai_analysis_enabled', False):
            # Run analysis in a background thread
            def async_analyze(app, db, photo_id):
                with app.app_context():
                    try:
                        from server import PhotoAnalyzer
                        photo_analyzer = PhotoAnalyzer(app, db)
                        photo_analyzer.analyze_photo(photo_id)
                    except Exception as e:
                        logging.error(f"Background analysis failed: {e}")
            
            from threading import Thread
            Thread(target=async_analyze, args=(app, db, new_photo.id)).start()
        
        logging.info(f"Successfully imported {file_path} to frame {frame_id}")
        return True
    except Exception as e:
        logging.error(f"Error importing file {file_path}: {str(e)}")
        return False 

# Function to check for new media in Immich and import them
def check_immich_for_new_media():
    """
    Check Immich auto-import configurations for new media files
    and import them to the specified target frames.
    """
    logging.info("Starting automatic check for new media in Immich")
    
    try:
        # Initialize Immich integration
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        
        # Get auto-import configurations
        auto_imports = immich.get_auto_import_configs()
        
        if not auto_imports:
            logging.info("No Immich auto-import configurations found")
            return
        
        # Import necessary modules from server.py
        from server import app, db, Photo, PlaylistEntry, photo_processor, extract_exif_metadata, generate_video_thumbnail
        
        # Process each auto-import configuration
        for config in auto_imports:
            config_id = config.get('id')
            source_type = config.get('source_type')
            source_id = config.get('source_id')
            source_name = config.get('source_name')
            frame_id = config.get('frame_id')
            imported_assets = config.get('imported_assets', [])
            
            logging.info(f"Checking Immich {source_type} '{source_name}' for new media")
            
            # Get assets based on source type
            assets = []
            if source_type == 'album':
                assets = immich.get_album_assets(source_id)
            elif source_type == 'face':
                assets = immich.get_face_assets(source_id)
                # For faces, we want to maintain only the latest 10 photos
                # Remove older assets from imported_assets if they're not in the latest 10
                latest_asset_ids = [asset.get('id') for asset in assets]
                imported_assets = [asset_id for asset_id in imported_assets if asset_id in latest_asset_ids]
            
            # Filter out already imported assets
            new_assets = [asset for asset in assets if asset.get('id') not in imported_assets]
            
            if not new_assets:
                logging.info(f"No new media files found in Immich {source_type} '{source_name}'")
                continue
            
            logging.info(f"Found {len(new_assets)} new media files in Immich {source_type} '{source_name}'")
            
            # Import each new file
            for asset in new_assets:
                try:
                    with app.app_context():
                        # Get the upload directory from app config
                        upload_dir = app.config['UPLOAD_FOLDER']
                        os.makedirs(upload_dir, exist_ok=True)

                        # Generate a unique filename
                        unique_filename = f"immich_{uuid.uuid4()}_{secure_filename(asset.get('originalFileName', 'photo.jpg'))}"
                        dest_path = os.path.join(upload_dir, unique_filename)

                        # Download the asset
                        success, message = immich.download_asset(asset['id'], dest_path)
                        if not success:
                            logging.error(f"Failed to download asset {asset['id']}: {message}")
                            continue

                        # Check if it's a HEIC file and convert to JPG if needed
                        if dest_path.lower().endswith(('.heic', '.HEIC')):
                            logging.info(f"Converting HEIC file to JPG: {dest_path}")
                            converted_path, was_converted = convert_heic_to_jpg(dest_path)
                            
                            if was_converted:
                                # Update the filename and path
                                dest_path = converted_path
                                unique_filename = os.path.basename(dest_path)
                                logging.info(f"Using converted JPG file: {dest_path}")

                        # Create a new Photo record
                        new_photo = Photo(
                            filename=unique_filename,
                            media_type='video' if asset.get('type') == 'VIDEO' else 'photo',
                            heading=f"Auto-imported from Immich {source_type}"
                        )
                        
                        db.session.add(new_photo)
                        db.session.commit()

                        # Add to playlist
                        PlaylistEntry.query.filter_by(frame_id=frame_id)\
                            .update({PlaylistEntry.order: PlaylistEntry.order + 1})
                        
                        entry = PlaylistEntry(
                            frame_id=frame_id,
                            photo_id=new_photo.id,
                            order=0
                        )
                        db.session.add(entry)
                        db.session.commit()

                        # Process the file
                        if new_photo.media_type == 'video':
                            thumbnails_dir = os.path.join(upload_dir, 'thumbnails')
                            os.makedirs(thumbnails_dir, exist_ok=True)
                            thumb_filename = f"thumb_{unique_filename}.jpg"
                            thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                            
                            if generate_video_thumbnail(dest_path, thumb_path):
                                new_photo.thumbnail = thumb_filename
                                db.session.commit()
                        else:
                            try:
                                thumbnails_dir = os.path.join(upload_dir, 'thumbnails')
                                os.makedirs(thumbnails_dir, exist_ok=True)
                                
                                with Image.open(dest_path) as img:
                                    img.thumbnail((400, 400))
                                    thumb_filename = f"thumb_{unique_filename}"
                                    thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                                    img.save(thumb_path, "JPEG")
                                    new_photo.thumbnail = thumb_filename
                                    db.session.commit()
                                
                                portrait_path = photo_processor.process_for_orientation(dest_path, 'portrait')
                                if portrait_path:
                                    new_photo.portrait_version = os.path.basename(portrait_path)
                                
                                landscape_path = photo_processor.process_for_orientation(dest_path, 'landscape')
                                if landscape_path:
                                    new_photo.landscape_version = os.path.basename(landscape_path)
                                
                                db.session.commit()
                            except Exception as e:
                                logging.error(f"Error processing image: {str(e)}")

                        # Add to imported assets list
                        imported_assets.append(asset['id'])
                        logging.info(f"Successfully imported asset {asset['id']} to frame {frame_id}")
                except Exception as e:
                    logging.error(f"Error importing asset {asset.get('id')}: {str(e)}")
            
            # For faces, ensure we keep only the latest 10 in imported_assets
            if source_type == 'face':
                imported_assets = imported_assets[-10:]
            
            # Save updated imported files list
            immich.update_imported_assets(config_id, imported_assets)
            
        logging.info("Completed automatic check for new media in Immich")
    except Exception as e:
        logging.error(f"Error checking network locations for new media: {str(e)}")

# Immich API Routes

# Route to get Immich settings
@integration_routes.route('/api/immich/settings', methods=['GET'])
def get_immich_settings():
    try:
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        config = immich.load_config()
        
        # For security, don't send the API key directly
        settings = {
            "url": config.get("url", ""),
            "api_key": config.get("api_key", "")
        }
        
        return jsonify({"success": True, "settings": settings})
    except Exception as e:
        logging.error(f"Error retrieving Immich settings: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to update Immich settings
@integration_routes.route('/api/immich/settings', methods=['POST'])
def update_immich_settings():
    try:
        data = request.json
        
        # Validate required fields
        if not data.get('url') or not data.get('api_key'):
            return jsonify({"success": False, "error": "URL and API key are required"}), 400
        
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        success = immich.update_config(data.get('url'), data.get('api_key'))
        
        if success:
            return jsonify({"success": True, "message": "Immich settings updated successfully"})
        else:
            return jsonify({"success": False, "error": "Failed to save Immich settings"}), 500
    except Exception as e:
        logging.error(f"Error updating Immich settings: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to test Immich connection
@integration_routes.route('/api/immich/test-connection', methods=['POST'])
def test_immich_connection():
    try:
        data = request.json
        
        # Validate required fields
        if not data.get('url') or not data.get('api_key'):
            return jsonify({"success": False, "error": "URL and API key are required"}), 400
        
        # Create a temporary Immich integration instance with the provided settings
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        immich.update_config(data.get('url'), data.get('api_key'))
        
        # Test the connection
        success, message = immich.test_connection()
        
        return jsonify({"success": success, "message": message})
    except Exception as e:
        logging.error(f"Error testing Immich connection: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to get albums from Immich
@integration_routes.route('/api/immich/albums', methods=['GET'])
def get_immich_albums():
    try:
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        
        # Check if connection is configured
        if not immich.config.get('url') or not immich.config.get('api_key'):
            return jsonify({"success": False, "error": "Immich connection not configured"}), 400
        
        # Get albums
        albums = immich.get_albums()
        
        return jsonify({"success": True, "albums": albums})
    except Exception as e:
        logging.error(f"Error getting Immich albums: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to get faces from Immich
@integration_routes.route('/api/immich/faces', methods=['GET'])
def get_immich_faces():
    # Use the predefined constant for the config file path
    immich = ImmichIntegration(IMMICH_CONFIG_FILE)
    
    # Log what we're doing
    print("Fetching faces from Immich server")
    
    try:
        # Get faces data from Immich
        faces_data = immich.get_faces()
        print(f"Received faces data of type: {type(faces_data)}")
        
        # Return faces data as is
        return jsonify(faces_data)
    except Exception as e:
        print(f"Error fetching faces: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": f"Error fetching faces: {str(e)}"}), 500

# Route to get face thumbnail
@integration_routes.route('/api/immich/face-thumbnail/<face_id>', methods=['GET'])
def get_immich_face_thumbnail(face_id):
    try:
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        
        # Check if connection is configured
        if not immich.config.get('url') or not immich.config.get('api_key'):
            abort(400)
        
        # Get the faces data
        faces_data = immich.get_faces()
        
        # Extract faces array from the response
        if not isinstance(faces_data, dict) or 'people' not in faces_data:
            logging.error("Invalid faces data structure received")
            abort(500)
            
        # Find the face in the people array
        face = next((f for f in faces_data['people'] if f.get('id') == face_id), None)
        
        if not face:
            logging.error(f"Face not found with ID: {face_id}")
            abort(404)
        
        # Get the thumbnail URL
        thumbnail_path = face.get('thumbnailPath')
        
        if not thumbnail_path:
            logging.error(f"No thumbnail path for face ID: {face_id}")
            abort(404)
        
        # Download the thumbnail
        headers = {
            'Accept': 'image/jpeg',
            'x-api-key': immich.config.get('api_key')
        }
        
        # Construct the full URL
        thumbnail_url = f"{immich.config.get('url')}{thumbnail_path}"
        logging.info(f"Fetching thumbnail from: {thumbnail_url}")
        
        response = requests.get(
            thumbnail_url,
            headers=headers,
            stream=True,
            timeout=10
        )
        
        if response.status_code != 200:
            logging.error(f"Failed to fetch thumbnail. Status code: {response.status_code}")
            abort(404)
        
        # Create a BytesIO object from the response content
        img_io = BytesIO(response.content)
        
        # Return the image
        return send_file(img_io, mimetype='image/jpeg')
    except Exception as e:
        logging.error(f"Error getting Immich face thumbnail: {str(e)}")
        traceback.print_exc()  # Add stack trace for debugging
        abort(500)

# Route to get auto-import configurations
@integration_routes.route('/api/immich/auto-imports', methods=['GET'])
def get_immich_auto_imports():
    try:
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        
        # Get auto-import configurations
        auto_imports = immich.get_auto_import_configs()
        
        return jsonify({"success": True, "auto_imports": auto_imports})
    except Exception as e:
        logging.error(f"Error getting Immich auto-import configurations: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to add auto-import configuration
@integration_routes.route('/api/immich/auto-imports', methods=['POST'])
def add_immich_auto_import():
    try:
        data = request.json
        
        # Validate required fields
        if not data.get('source_type') or not data.get('source_id') or not data.get('source_name') or not data.get('frame_id'):
            return jsonify({"success": False, "error": "Source type, source ID, source name, and frame ID are required"}), 400
        
        # Validate source type
        if data.get('source_type') not in ['album', 'face']:
            return jsonify({"success": False, "error": "Source type must be 'album' or 'face'"}), 400
        
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        
        # Add auto-import configuration
        success = immich.add_auto_import(
            data.get('source_type'),
            data.get('source_id'),
            data.get('source_name'),
            data.get('frame_id')
        )
        
        if success:
            return jsonify({"success": True, "message": "Auto-import configuration added successfully"})
        else:
            return jsonify({"success": False, "error": "Failed to add auto-import configuration"}), 500
    except Exception as e:
        logging.error(f"Error adding Immich auto-import configuration: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to delete auto-import configuration
@integration_routes.route('/api/immich/auto-imports/<config_id>', methods=['DELETE'])
def delete_immich_auto_import(config_id):
    try:
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        
        # Delete auto-import configuration
        success = immich.remove_auto_import(config_id)
        
        if success:
            return jsonify({"success": True, "message": "Auto-import configuration deleted successfully"})
        else:
            return jsonify({"success": False, "error": "Failed to delete auto-import configuration"}), 500
    except Exception as e:
        logging.error(f"Error deleting Immich auto-import configuration: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to manually import from Immich
@integration_routes.route('/api/immich/import', methods=['POST'])
def import_from_immich():
    try:
        data = request.json
        
        # Validate required fields
        if not data.get('source_type') or not data.get('source_id') or not data.get('frame_id'):
            return jsonify({"success": False, "error": "Source type, source ID, and frame ID are required"}), 400
        
        # Validate source type
        if data.get('source_type') not in ['album', 'face']:
            return jsonify({"success": False, "error": "Source type must be 'album' or 'face'"}), 400
        
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        
        # Get assets based on source type
        assets = []
        if data.get('source_type') == 'album':
            assets = immich.get_album_assets(data.get('source_id'))
        elif data.get('source_type') == 'face':
            assets = immich.get_face_assets(data.get('source_id'))
        
        if not assets:
            return jsonify({"success": False, "error": "No assets found"}), 404
        
        # Import necessary modules from server.py
        from server import app, db, Photo, PlaylistEntry, photo_processor, extract_exif_metadata, generate_video_thumbnail
        
        # Get the upload directory from app config
        with app.app_context():
            upload_dir = app.config['UPLOAD_FOLDER']
            os.makedirs(upload_dir, exist_ok=True)
            
            # Import each asset
            imported_assets = []
            for asset in assets:
                try:
                    asset_id = asset.get('id')
                    
                    # Generate a unique filename
                    unique_filename = f"immich_{uuid.uuid4()}_{secure_filename(asset.get('originalFileName', 'photo.jpg'))}"
                    dest_path = os.path.join(upload_dir, unique_filename)
                    
                    # Download the asset
                    success, message = immich.download_asset(asset_id, dest_path)
                    
                    if not success:
                        logging.error(f"Failed to download asset {asset_id}: {message}")
                        continue
                    
                    # Check if it's a HEIC file and convert to JPG if needed
                    if dest_path.lower().endswith(('.heic', '.HEIC')):
                        logging.info(f"Converting HEIC file to JPG: {dest_path}")
                        converted_path, was_converted = convert_heic_to_jpg(dest_path)
                        
                        if was_converted:
                            # Update the filename and path
                            dest_path = converted_path
                            unique_filename = os.path.basename(dest_path)
                            logging.info(f"Using converted JPG file: {dest_path}")
                    
                    # Determine if it's a video
                    is_video = asset.get('type') == 'VIDEO'
                    
                    # Extract EXIF metadata if it's an image
                    exif_metadata = None
                    if not is_video:
                        exif_metadata = extract_exif_metadata(dest_path)
                    
                    # Create a new Photo record
                    new_photo = Photo(
                        filename=unique_filename,
                        original_filename=asset.get('originalFileName', 'photo.jpg'),
                        media_type='video' if is_video else 'photo',
                        heading=f"Imported from Immich {data.get('source_type')}",
                        exif_metadata=exif_metadata
                    )
                    
                    db.session.add(new_photo)
                    db.session.commit()
                    
                    # Add to playlist
                    # Shift existing entries
                    PlaylistEntry.query.filter_by(frame_id=data.get('frame_id'))\
                        .update({PlaylistEntry.order: PlaylistEntry.order + 1})
                    
                    # Add new entry at the beginning
                    entry = PlaylistEntry(
                        frame_id=data.get('frame_id'),
                        photo_id=new_photo.id,
                        order=0
                    )
                    db.session.add(entry)
                    db.session.commit()
                    
                    # Process the file with the appropriate function based on type
                    if is_video:
                        # For videos, generate thumbnail
                        thumbnails_dir = os.path.join(upload_dir, 'thumbnails')
                        os.makedirs(thumbnails_dir, exist_ok=True)
                        
                        # Generate thumbnail
                        thumb_filename = f"thumb_{unique_filename}.jpg"
                        thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                        
                        if generate_video_thumbnail(dest_path, thumb_path):
                            # Update photo record with thumbnail
                            new_photo.thumbnail = thumb_filename
                            db.session.commit()
                    else:
                        # For images, generate thumbnail and orientation versions
                        try:
                            # Create thumbnails directory if it doesn't exist
                            thumbnails_dir = os.path.join(upload_dir, 'thumbnails')
                            os.makedirs(thumbnails_dir, exist_ok=True)
                            
                            # Generate thumbnail
                            with Image.open(dest_path) as img:
                                img.thumbnail((400, 400))
                                thumb_filename = f"thumb_{unique_filename}"
                                thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                                img.save(thumb_path, "JPEG")
                                
                                # Update photo record with thumbnail
                                new_photo.thumbnail = thumb_filename
                                db.session.commit()
                            
                            # Process for orientations
                            portrait_path = photo_processor.process_for_orientation(dest_path, 'portrait')
                            if portrait_path:
                                new_photo.portrait_version = os.path.basename(portrait_path)
                            
                            landscape_path = photo_processor.process_for_orientation(dest_path, 'landscape')
                            if landscape_path:
                                new_photo.landscape_version = os.path.basename(landscape_path)
                            
                            db.session.commit()
                        except Exception as e:
                            logging.error(f"Error processing image: {str(e)}")
                    
                    # Add to imported assets list
                    imported_assets.append({
                        "id": asset_id,
                        "photo_id": new_photo.id,
                        "filename": unique_filename
                    })
                except Exception as e:
                    logging.error(f"Error importing asset: {str(e)}")
            
            return jsonify({
                "success": True,
                "message": f"Successfully imported {len(imported_assets)} assets",
                "imported_assets": imported_assets
            })
    except Exception as e:
        logging.error(f"Error importing from Immich: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to search Immich assets
@integration_routes.route('/api/immich/search', methods=['GET'])
def search_immich_assets():
    try:
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        size = request.args.get('size', 50, type=int)  # Number of items per page
        
        # Prepare search parameters - only include what we need
        search_params = {
            'page': page,
            'size': size,
            'order': 'desc',  # Sort by newest first
            'withExif': True  # Include EXIF data in response
        }
        
        logging.info(f"Searching Immich assets with params: {json.dumps(search_params, indent=2)}")
        
        # Make the search request
        response = requests.post(
            f"{immich.config['url']}/api/search/metadata",
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'x-api-key': immich.config['api_key']
            },
            json=search_params,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            logging.info(f"Search response status: {response.status_code}")
            logging.info(f"Search response data: {json.dumps(data, indent=2)}")
            
            # Handle different possible response formats
            items = []
            total = 0
            if isinstance(data, dict):
                if 'items' in data:
                    # Format: { items: [...], total: n }
                    items = data.get('items', [])
                    total = data.get('total', 0)
                elif 'assets' in data:
                    # Format: { assets: { items: [...], total: n } }
                    assets = data.get('assets', {})
                    if isinstance(assets, dict):
                        items = assets.get('items', [])
                        total = assets.get('total', 0)
            elif isinstance(data, list):
                # Format: direct array of items
                items = data
                total = len(items)
            
            logging.info(f"Found {len(items)} items (total: {total})")
            
            # Transform the response to include only necessary data
            assets = []
            for asset in items:
                assets.append({
                    'id': asset.get('id'),
                    'thumbnailPath': f"/api/asset/thumbnail/{asset.get('id')}",  # Direct thumbnail path
                    'originalPath': asset.get('originalPath'),
                    'originalFileName': asset.get('originalFileName'),
                    'type': asset.get('type'),
                    'fileCreatedAt': asset.get('fileCreatedAt'),
                    'fileModifiedAt': asset.get('fileModifiedAt')
                })
            
            return jsonify({
                "success": True,
                "assets": assets,
                "total": total,
                "hasMore": (page * size) < total
            })
        else:
            logging.error(f"Immich search failed with status {response.status_code}")
            try:
                error_data = response.json()
                logging.error(f"Error response: {error_data}")
            except:
                logging.error(f"Response content: {response.text}")
            return jsonify({"success": False, "error": "Failed to fetch assets from Immich"}), response.status_code
        
    except Exception as e:
        logging.error(f"Error searching Immich assets: {str(e)}")
        logging.error(f"Stack trace: {traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500

# Route to get asset thumbnail
@integration_routes.route('/api/immich/asset-thumbnail/<asset_id>', methods=['GET'])
def get_immich_asset_thumbnail(asset_id):
    try:
        immich = ImmichIntegration(IMMICH_CONFIG_FILE)
        
        # Make request to get thumbnail
        response = requests.get(
            f"{immich.config['url']}/api/assets/{asset_id}/thumbnail",
            headers={
                'x-api-key': immich.config['api_key']
            },
            stream=True,
            timeout=30
        )
        
        if response.status_code == 200:
            # Stream the response directly to the client
            return send_file(
                io.BytesIO(response.content),
                mimetype=response.headers.get('content-type', 'image/jpeg'),
                as_attachment=False
            )
        else:
            logging.error(f"Failed to get thumbnail for asset {asset_id}: {response.status_code}")
            try:
                error_data = response.json()
                logging.error(f"Error response: {error_data}")
            except:
                logging.error(f"Response content: {response.text}")
            abort(response.status_code)
            
    except Exception as e:
        logging.error(f"Error getting asset thumbnail: {str(e)}")
        logging.error(f"Stack trace: {traceback.format_exc()}")
        abort(500)