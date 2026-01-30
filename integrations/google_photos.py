from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import os
import json
import logging
from datetime import datetime, timedelta
import requests
from io import BytesIO
from google.auth.exceptions import RefreshError
from googleapiclient.discovery_cache.base import Cache
from collections import OrderedDict

logger = logging.getLogger(__name__)

class MemoryCache(Cache):
    """LRU-bounded memory cache for Google API discovery documents."""
    _CACHE = OrderedDict()
    _MAX_SIZE = 10  # Limit cache to 10 entries (discovery docs are few but can be large)

    def get(self, url):
        if url in MemoryCache._CACHE:
            # Move to end (most recently used)
            MemoryCache._CACHE.move_to_end(url)
            return MemoryCache._CACHE[url]
        return None

    def set(self, url, content):
        # Remove oldest entries if at capacity
        while len(MemoryCache._CACHE) >= MemoryCache._MAX_SIZE:
            MemoryCache._CACHE.popitem(last=False)
        MemoryCache._CACHE[url] = content

class GooglePhotosIntegration:
    def __init__(self, client_secrets_file, token_file, upload_folder):
        self.client_secrets_file = client_secrets_file
        self.token_file = token_file
        self.upload_folder = upload_folder
        self.credentials = None
        self.service = None
        
        # Load credentials if they exist
        self.load_credentials()

    def load_credentials(self):
        """Load existing credentials from token file."""
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, 'r') as token:
                    creds_data = json.load(token)
                    self.credentials = Credentials.from_authorized_user_info(
                        creds_data,
                        scopes=['https://www.googleapis.com/auth/photoslibrary.readonly']
                    )
                
                # Check if credentials are expired
                if self.credentials and self.credentials.expired:
                    if self.credentials.refresh_token:
                        try:
                            self.credentials.refresh(Request())
                            self.save_credentials()
                            logger.info("Successfully refreshed Google Photos credentials")
                        except Exception as e:
                            logger.error(f"Error refreshing credentials: {e}")
                            self.credentials = None
                            return
                    else:
                        logger.error("No refresh token available")
                        self.credentials = None
                        return
                
                if self.credentials:
                    try:
                        # Initialize the service with explicit discovery URL
                        DISCOVERY_URL = 'https://photoslibrary.googleapis.com/$discovery/rest?version=v1'
                        self.service = build('photoslibrary', 'v1', 
                                           credentials=self.credentials,
                                           cache=MemoryCache(),
                                           discoveryServiceUrl=DISCOVERY_URL)
                        
                        # Test the connection
                        self.service.mediaItems().list(pageSize=1).execute()
                        logger.info("Successfully initialized Google Photos service")
                    except Exception as e:
                        logger.error(f"Error initializing service: {e}")
                        self.credentials = None
                        self.service = None
                        
            except Exception as e:
                logger.error(f"Error loading credentials: {e}")
                self.credentials = None
                if os.path.exists(self.token_file):
                    try:
                        os.remove(self.token_file)
                        logger.info("Removed invalid token file")
                    except Exception as e:
                        logger.error(f"Error removing token file: {e}")

    def save_credentials(self):
        """Save credentials to token file."""
        if self.credentials:
            try:
                creds_data = {
                    'token': self.credentials.token,
                    'refresh_token': self.credentials.refresh_token,
                    'token_uri': self.credentials.token_uri,
                    'client_id': self.credentials.client_id,
                    'client_secret': self.credentials.client_secret,
                    'scopes': self.credentials.scopes
                }
                
                # Ensure the directory exists
                os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
                
                # Save the token file
                with open(self.token_file, 'w') as token:
                    json.dump(creds_data, token)
                logger.info("Successfully saved Google Photos credentials")
                
                # Set proper permissions
                os.chmod(self.token_file, 0o600)
            except Exception as e:
                logger.error(f"Error saving credentials: {e}")

    def get_auth_url(self):
        """Generate authorization URL for Google Photos."""
        try:
            flow = Flow.from_client_secrets_file(
                self.client_secrets_file,
                scopes=['https://www.googleapis.com/auth/photoslibrary.readonly'],
                redirect_uri='urn:ietf:wg:oauth:2.0:oob'
            )
            auth_url, _ = flow.authorization_url(prompt='consent')
            return auth_url
        except Exception as e:
            logger.error(f"Error generating auth URL: {e}")
            return None

    def handle_auth_code(self, auth_code):
        """Handle the authorization code from Google OAuth."""
        try:
            flow = Flow.from_client_secrets_file(
                self.client_secrets_file,
                scopes=['https://www.googleapis.com/auth/photoslibrary.readonly'],
                redirect_uri='urn:ietf:wg:oauth:2.0:oob'
            )
            
            try:
                flow.fetch_token(code=auth_code)
            except Exception as e:
                if 'invalid_grant' in str(e):
                    logger.error("Invalid or expired authorization code")
                    raise ValueError("The authorization code has expired or is invalid. Please try connecting again.")
                raise e

            self.credentials = flow.credentials
            self.save_credentials()
            
            try:
                # Initialize the service with explicit discovery URL
                DISCOVERY_URL = 'https://photoslibrary.googleapis.com/$discovery/rest?version=v1'
                self.service = build('photoslibrary', 'v1', 
                                   credentials=self.credentials,
                                   cache=MemoryCache(),
                                   discoveryServiceUrl=DISCOVERY_URL)
                
                # Test the service with a simple API call
                self.service.mediaItems().list(pageSize=1).execute()
                logger.info("Successfully authenticated with Google Photos")
                return True
            except Exception as e:
                logger.error(f"Error initializing Google Photos service: {e}")
                raise ValueError("Failed to initialize Google Photos service. Please ensure the API is enabled in your Google Cloud Console.")
                
        except ValueError as e:
            raise
        except Exception as e:
            logger.error(f"Error handling auth code: {e}")
            if self.credentials:
                self.credentials = None
            if os.path.exists(self.token_file):
                os.remove(self.token_file)
            raise ValueError("Failed to authenticate with Google Photos. Please try again.")

    def disconnect(self):
        """Disconnect Google Photos integration."""
        try:
            if os.path.exists(self.token_file):
                os.remove(self.token_file)
            self.credentials = None
            self.service = None
            return True
        except Exception as e:
            logger.error(f"Error disconnecting Google Photos: {e}")
            return False

    def search_photos(self, query=None, page_size=50, page_token=None, album_id=None):
        """Search Google Photos library."""
        if not self.service:
            logger.error("Google Photos service not initialized")
            return None, None

        try:
            logger.info(f"Starting Google Photos search with query: '{query}', album_id: '{album_id}'")
            
            # If album_id is provided, get photos from that album
            if album_id:
                logger.info(f"Searching in album: {album_id}")
                body = {
                    'pageSize': page_size,
                    'albumId': album_id
                }
                if page_token:
                    body['pageToken'] = page_token
                    
                request = self.service.mediaItems().search(body=body)
                
                logger.info("Executing API request...")
                response = request.execute()
                
                media_items = response.get('mediaItems', [])
                next_token = response.get('nextPageToken')
                
                logger.info(f"Received {len(media_items)} items from API")
                logger.info(f"Next page token: {next_token if next_token else 'None'}")
                
                return media_items, next_token

            elif not query:
                # For no query, use simple list
                logger.info("No query - using list() endpoint")
                request = self.service.mediaItems().list(
                    pageSize=page_size,
                    pageToken=page_token if page_token else None
                )
            else:
                # For search queries, use search with filters
                logger.info("Query provided - using search() endpoint")
                body = {
                    'pageSize': page_size,
                    'filters': {
                        'mediaTypeFilter': {
                            'mediaTypes': ['PHOTO']
                        }
                    }
                }
                
                # Try to find matching albums first
                try:
                    albums_request = self.service.albums().list(
                        pageSize=50,
                        fields="albums(id,title)"
                    ).execute()
                    
                    albums = albums_request.get('albums', [])
                    matching_albums = [
                        album for album in albums 
                        if query.lower() in album.get('title', '').lower()
                    ]
                    
                    if matching_albums:
                        logger.info(f"Found matching album: {matching_albums[0].get('title')}")
                        body = {
                            'pageSize': page_size,
                            'albumId': matching_albums[0]['id']
                        }
                    else:
                        # If no matching album, search in recent photos
                        logger.info("No matching albums found, searching in recent photos")
                        body = {
                            'pageSize': page_size,
                            'filters': {
                                'mediaTypeFilter': {
                                    'mediaTypes': ['PHOTO']
                                },
                                'dateFilter': {
                                    'ranges': [{
                                        'startDate': {
                                            'year': 2000,
                                            'month': 1,
                                            'day': 1
                                        },
                                        'endDate': {
                                            'year': 2025,
                                            'month': 12,
                                            'day': 31
                                        }
                                    }]
                                }
                            }
                        }
                except Exception as e:
                    logger.error(f"Error searching albums: {e}")
                    # Fallback to basic search
                    body = {
                        'pageSize': page_size,
                        'filters': {
                            'mediaTypeFilter': {
                                'mediaTypes': ['PHOTO']
                            }
                        }
                    }
                
                if page_token:
                    body['pageToken'] = page_token
                
                logger.info(f"Search request body: {json.dumps(body, indent=2)}")
                request = self.service.mediaItems().search(body=body)

            logger.info("Executing API request...")
            response = request.execute()
            
            media_items = response.get('mediaItems', [])
            logger.info(f"Received {len(media_items)} items from API")
            
            if query and not album_id:  # Only apply client-side filtering for text search, not album searches
                # Apply client-side filtering for text search
                query_lower = query.lower()
                filtered_items = [
                    item for item in media_items
                    if any(
                        query_lower in value.lower()
                        for value in [
                            item.get('filename', ''),
                            item.get('description', ''),
                            item.get('mediaMetadata', {}).get('creationTime', ''),
                        ]
                        if value
                    )
                ]
                logger.info(f"Filtered to {len(filtered_items)} items matching query")
                return filtered_items, response.get('nextPageToken')
            
            return media_items, response.get('nextPageToken')
            
        except Exception as e:
            logger.error(f"Error searching Google Photos: {str(e)}")
            logger.exception("Full traceback:")
            return None, None

    def download_photo(self, media_item_id):
        """Download a photo from Google Photos."""
        if not self.service:
            return None

        try:
            media_item = self.service.mediaItems().get(mediaItemId=media_item_id).execute()
            base_url = media_item.get('baseUrl')
            if not base_url:
                return None

            # Get full resolution image
            download_url = f"{base_url}=d"
            response = requests.get(download_url)
            if response.status_code == 200:
                return BytesIO(response.content)
            return None
            
        except Exception as e:
            logger.error(f"Error downloading photo: {e}")
            return None

    def is_connected(self):
        """Check if Google Photos is connected."""
        try:
            logger.info("Checking Google Photos connection status...")
            logger.info(f"Credentials exist: {self.credentials is not None}")
            logger.info(f"Service exists: {self.service is not None}")
            
            if not self.credentials or not self.service:
                logger.info("No credentials or service available")
                return False
            
            # Test the connection with a simple API call
            result = self.service.mediaItems().list(pageSize=1).execute()
            logger.info("Successfully verified Google Photos connection")
            logger.info(f"Test API call result: {result is not None}")
            return True
        except Exception as e:
            logger.error(f"Error checking Google Photos connection: {e}")
            logger.exception("Full traceback:")
            return False

    def list_albums(self):
        """Get list of Google Photos albums."""
        if not self.service:
            logger.error("Google Photos service not initialized")
            return None

        try:
            albums = []
            page_token = None
            
            while True:
                # Request albums from the API
                request = self.service.albums().list(
                    pageSize=50,
                    pageToken=page_token,
                    fields="albums(id,title,mediaItemsCount,coverPhotoBaseUrl),nextPageToken"
                )
                response = request.execute()
                
                # Add albums from this page to our list
                if 'albums' in response:
                    albums.extend(response['albums'])
                
                # Get next page token
                page_token = response.get('nextPageToken')
                if not page_token:
                    break
                
            return albums
            
        except Exception as e:
            logger.error(f"Error listing albums: {e}")
            return None

