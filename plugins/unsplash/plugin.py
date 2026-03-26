"""
Unsplash Plugin — fetches a random photo from Unsplash matching a search query.

Requires a free API key from https://unsplash.com/developers
Register an application and use the Access Key (sent as Client-ID).
"""

import io
import requests
from PIL import Image, ImageOps

from plugins.base_plugin import PluginBase


class UnsplashPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return 'unsplash'

    @property
    def display_name(self) -> str:
        return 'Unsplash Photos'

    @property
    def description(self) -> str:
        return 'Fetches a fresh random photo from Unsplash matching your search query and displays it full-screen.'

    @property
    def default_cron(self) -> str:
        return '0 * * * *'  # Every hour

    @property
    def config_schema(self) -> dict:
        return {
            'api_key': {
                'type':     'string',
                'label':    'Unsplash Access Key',
                'secret':   True,
                'required': True,
            },
            'query': {
                'type':     'string',
                'label':    'Search Query (e.g. mountain, ocean, architecture)',
                'required': True,
            },
            'orientation': {
                'type':    'select',
                'label':   'Orientation',
                'options': ['portrait', 'landscape', 'squarish', 'any'],
                'default': 'portrait',
            },
        }

    def generate(self, config: dict) -> bytes:
        api_key     = config.get('api_key', '').strip()
        query       = config.get('query', '').strip()
        orientation = config.get('orientation', 'portrait')

        if not api_key:
            raise ValueError("Unsplash Access Key is required.")
        if not query:
            raise ValueError("A search query is required.")

        params = {
            'query':          query,
            'count':          1,
            'content_filter': 'high',
        }
        if orientation and orientation != 'any':
            params['orientation'] = orientation

        resp = requests.get(
            'https://api.unsplash.com/photos/random',
            params=params,
            headers={
                'Authorization':  f'Client-ID {api_key}',
                'Accept-Version': 'v1',
            },
            timeout=15,
        )
        resp.raise_for_status()

        data = resp.json()
        # When count=1 the API may return a list or a single object
        photo = data[0] if isinstance(data, list) else data

        # Notify Unsplash of the download (required by API guidelines)
        try:
            requests.get(
                photo['links']['download_location'],
                headers={'Authorization': f'Client-ID {api_key}'},
                timeout=10,
            )
        except Exception:
            pass  # Non-fatal; don't fail the whole run

        # Download the full-resolution image
        img_resp = requests.get(photo['urls']['full'], timeout=30)
        img_resp.raise_for_status()

        img = Image.open(io.BytesIO(img_resp.content))
        img = ImageOps.exif_transpose(img)

        img = self._fit_to_frame(img, 1200, 1600)

        # Add a small photographer credit strip at the bottom
        photographer = photo.get('user', {}).get('name', '')
        if photographer:
            img = self._add_credit(img, photographer)

        buf = io.BytesIO()
        img.convert('RGB').save(buf, format='JPEG', quality=92)
        return buf.getvalue()

    # ------------------------------------------------------------------

    def _fit_to_frame(self, img: Image.Image, w: int, h: int) -> Image.Image:
        """Scale and center-crop the image to exactly w×h."""
        src_w, src_h = img.size
        scale = max(w / src_w, h / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - w) // 2
        top  = (new_h - h) // 2
        return img.crop((left, top, left + w, top + h))

    def _add_credit(self, img: Image.Image, photographer: str) -> Image.Image:
        """Overlay a semi-transparent credit bar at the bottom."""
        from PIL import ImageDraw, ImageFont
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        bar_h = 56
        bar_top = img.height - bar_h
        draw.rectangle([(0, bar_top), (img.width, img.height)], fill=(0, 0, 0, 160))

        text = f'Photo by {photographer} on Unsplash'
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 26)
        except OSError:
            font = ImageFont.load_default()

        draw.text((24, bar_top + 14), text, font=font, fill=(220, 220, 220, 255))

        base = img.convert('RGBA')
        combined = Image.alpha_composite(base, overlay)
        return combined.convert('RGB')
