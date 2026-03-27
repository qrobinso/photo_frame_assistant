"""
Immich Plugin — fetches a random photo from an Immich server.

Supports filtering by album, person (face), or pulling from all albums.
Requires the Immich server URL and an API key.
"""

import io
import random
import requests
from PIL import Image, ImageOps

from plugins.base import PluginBase


class ImmichPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return 'immich'

    @property
    def display_name(self) -> str:
        return 'Immich Photos'

    @property
    def description(self) -> str:
        return 'Displays a random photo from your Immich server, filtered by album or person.'

    @property
    def default_cron(self) -> str:
        return '0 * * * *'  # Every hour

    @property
    def config_schema(self) -> dict:
        return {
            'server_url': {
                'type':     'string',
                'label':    'Immich Server URL (e.g. http://192.168.1.10:2283)',
                'required': True,
            },
            'api_key': {
                'type':     'string',
                'label':    'API Key',
                'secret':   True,
                'required': True,
            },
            'source_type': {
                'type':    'select',
                'label':   'Photo Source',
                'options': ['random', 'album', 'person'],
                'default': 'random',
            },
            'source_id': {
                'type':  'string',
                'label': 'Album or Person ID (required for album/person source)',
            },
        }

    def generate(self, config: dict) -> bytes:
        server_url  = config.get('server_url', '').strip().rstrip('/')
        api_key     = config.get('api_key', '').strip()
        source_type = config.get('source_type', 'random')
        source_id   = config.get('source_id', '').strip()

        if not server_url:
            raise ValueError("Immich Server URL is required.")
        if not api_key:
            raise ValueError("API Key is required.")
        if source_type in ('album', 'person') and not source_id:
            raise ValueError(f"A source ID is required when source type is '{source_type}'.")

        if not server_url.startswith(('http://', 'https://')):
            server_url = 'http://' + server_url

        headers = {
            'Accept': 'application/json',
            'x-api-key': api_key,
        }

        assets = self._fetch_assets(server_url, headers, source_type, source_id)
        if not assets:
            raise RuntimeError("No assets found for the configured source.")

        # Filter to image assets only
        image_assets = [a for a in assets if a.get('type', '').upper() == 'IMAGE']
        if not image_assets:
            image_assets = assets  # Fall back to all if type field missing

        asset = random.choice(image_assets)
        asset_id = asset.get('id')
        if not asset_id:
            raise RuntimeError("Selected asset has no ID.")

        img_bytes = self._download_asset(server_url, api_key, asset_id)
        img = self._open_image(img_bytes)
        img = ImageOps.exif_transpose(img)
        img = self._fit_to_frame(img, 1200, 1600)

        buf = io.BytesIO()
        img.convert('RGB').save(buf, format='JPEG', quality=92)
        return buf.getvalue()

    # ------------------------------------------------------------------

    def _fetch_assets(self, server_url, headers, source_type, source_id):
        if source_type == 'album':
            return self._get_album_assets(server_url, headers, source_id)
        elif source_type == 'person':
            return self._get_person_assets(server_url, headers, source_id)
        else:
            return self._get_random_assets(server_url, headers)

    def _get_album_assets(self, server_url, headers, album_id):
        resp = requests.get(
            f"{server_url}/api/albums/{album_id}",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get('assets', [])

    def _get_person_assets(self, server_url, headers, person_id):
        search_headers = {**headers, 'Content-Type': 'application/json'}
        resp = requests.post(
            f"{server_url}/api/search/metadata",
            headers=search_headers,
            json={
                'personIds': [person_id],
                'size': 50,
                'page': 1,
                'order': 'desc',
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            if 'items' in data:
                return data['items']
            if 'assets' in data:
                assets = data['assets']
                return assets.get('items', []) if isinstance(assets, dict) else assets
        if isinstance(data, list):
            return data
        return []

    def _get_random_assets(self, server_url, headers):
        resp = requests.get(
            f"{server_url}/api/albums",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        albums = resp.json()
        if not albums:
            raise RuntimeError("No albums found on the Immich server.")

        # Pick a random album that has assets
        random.shuffle(albums)
        for album in albums:
            album_id = album.get('id')
            count = album.get('assetCount', 0)
            if not album_id or count == 0:
                continue
            assets = self._get_album_assets(server_url, headers, album_id)
            if assets:
                return assets

        raise RuntimeError("All albums are empty.")

    def _download_asset(self, server_url, api_key, asset_id):
        resp = requests.get(
            f"{server_url}/api/assets/{asset_id}/original",
            headers={'X-API-Key': api_key},
            stream=True,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content

    def _open_image(self, raw_bytes):
        """Open image bytes with PIL, handling HEIC via pillow-heif if available."""
        try:
            return Image.open(io.BytesIO(raw_bytes))
        except Exception:
            try:
                import pillow_heif
                heif_file = pillow_heif.read_heif(raw_bytes)
                return Image.frombytes(
                    heif_file.mode, heif_file.size, heif_file.data,
                    'raw', heif_file.mode, heif_file.stride,
                )
            except ImportError:
                raise RuntimeError(
                    "Cannot open image. If this is a HEIC file, "
                    "install pillow-heif: pip install pillow-heif"
                )
            except Exception as e:
                raise RuntimeError(f"Cannot open image: {e}")

    def _fit_to_frame(self, img, w, h):
        """Scale and center-crop the image to exactly w x h."""
        src_w, src_h = img.size
        scale = max(w / src_w, h / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - w) // 2
        top  = (new_h - h) // 2
        return img.crop((left, top, left + w, top + h))
