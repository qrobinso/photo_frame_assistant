import os

# Base directory of the application
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Google Photos API configuration
GOOGLE_CREDENTIALS_PATH = os.path.join(BASE_DIR, 'credentials', 'google_credentials.json')
GOOGLE_TOKEN_PATH = os.path.join(BASE_DIR, 'credentials', 'google_token.json')

# Ensure credentials directory exists
os.makedirs(os.path.dirname(GOOGLE_CREDENTIALS_PATH), exist_ok=True) 