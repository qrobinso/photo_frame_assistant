# Google Photos Integration Setup Guide

## Prerequisites
- A Google Account
- Access to Google Cloud Console

## Setup Steps

1. **Create a Google Cloud Project**
   - Go to [Google Cloud Console](https://console.cloud.google.com)
   - Create a new project or select an existing one
   - Note down your Project ID

2. **Enable the Google Photos Library API**
   - In the Cloud Console, go to "APIs & Services" > "Library"
   - Search for "Google Photos Library API"
   - Click "Enable"

3. **Configure OAuth Consent Screen**
   - Go to "APIs & Services" > "OAuth consent screen"
   - Choose "External" user type
   - Fill in the required information:
     - App name
     - User support email
     - Developer contact information
   - Add the following scope:
     - `https://www.googleapis.com/auth/photoslibrary.readonly`
   - Add your test users' email addresses

4. **Create OAuth 2.0 Credentials**
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Choose "Desktop app" as the application type
   - Name your client ID
   - Download the client configuration file

5. **Configure Your Photo Frame**
   - Rename the downloaded client configuration file to `gphotos_auth.json`
   - Place the file in the `/home/pi/photo_server/credentials` directory
   - Ensure the file permissions are correct:
     ```bash
     chmod 600 /home/pi/photo_server/credentials/gphotos_auth.json
     ```
   - Connect your Google Photos account using the integration page

## Important Notes
- The credentials file MUST be named `gphotos_auth.json`
- The file MUST be placed in the `/credentials` directory
- The file should have secure permissions (readable only by the server)
- If you're updating existing credentials, you may need to disconnect and reconnect your Google Photos integration

## Troubleshooting

If you encounter any issues:
1. Ensure the API is enabled in your Google Cloud Console
2. Verify the credentials file is named correctly (`gphotos_auth.json`)
3. Check that the file is in the correct location (`/credentials` directory)
4. Confirm the file permissions are set correctly
5. Try disconnecting and reconnecting your Google Photos integration
6. Check that you've added your email as a test user in the OAuth consent screen

For more detailed information, visit the [Google Photos API documentation](https://developers.google.com/photos/library/guides/get-started). 