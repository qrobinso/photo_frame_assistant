"""
Front Pages Plugin — fetches today's newspaper front page from the Freedom Forum
(frontpages.freedomforum.org) CDN. No API key or browser required.

Image CDN pattern:
  https://cdn.freedomforum.org/dfp/jpg{day_of_month}/lg/{CODE}.jpg

Where {day_of_month} is the numeric day (no zero-padding) and {CODE} is the
newspaper's uppercase identifier (e.g. NY_NYT, DC_WP, WSJ).
"""

import io
from datetime import datetime

import requests
from PIL import Image

from plugins.base_plugin import PluginBase

# (code, label) — code is the Freedom Forum CDN identifier
# All codes verified against cdn.freedomforum.org/dfp/jpg{day}/lg/{CODE}.jpg
NEWSPAPERS = [
    # United States — National
    ('NY_NYT',   'The New York Times'),
    ('DC_WP',    'The Washington Post'),
    ('WSJ',      'The Wall Street Journal'),
    ('USAT',     'USA Today'),
    # United States — Top 10 Cities
    ('CA_LAT',   'Los Angeles Times'),
    ('IL_CT',    'Chicago Tribune'),
    ('TX_HC',    'Houston Chronicle'),
    ('PA_PI',    'Philadelphia Inquirer'),
    ('TX_SAEN',  'San Antonio Express-News'),
    ('TX_DMN',   'The Dallas Morning News'),
    # United States — Regional
    ('MA_BG',    'The Boston Globe'),
    ('CA_SFC',   'San Francisco Chronicle'),
    ('NY_DN',    'New York Daily News'),
    ('FL_MH',    'Miami Herald'),
    ('GA_AJC',   'Atlanta Journal-Constitution'),
    ('MI_DFP',   'Detroit Free Press'),
    ('OH_CPD',   'Columbus Dispatch'),
    ('MO_KCS',   'Kansas City Star'),
    ('NJ_SL',    'The Star-Ledger (NJ)'),
    ('NE_OWH',   'Omaha World-Herald'),
    ('VA_RTD',   'Richmond Times-Dispatch'),
    ('UT_DH',    'Deseret News'),
    # International
    ('UAE_GN',   'Gulf News (UAE)'),
    ('CAN_TGAM', 'The Globe and Mail (Canada)'),
]

_CODE_OPTIONS = [code for code, _ in NEWSPAPERS]
_CODE_TO_LABEL = {code: label for code, label in NEWSPAPERS}


class FrontPagesPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return 'front_pages'

    @property
    def display_name(self) -> str:
        return 'Newspaper Front Pages'

    @property
    def description(self) -> str:
        return (
            "Shows today's newspaper front page via the Freedom Forum "
            "(frontpages.freedomforum.org). No API key required."
        )

    @property
    def default_cron(self) -> str:
        return '0 7 * * *'  # 07:00 every day

    @property
    def config_schema(self) -> dict:
        return {
            'newspaper': {
                'type':    'select',
                'label':   'Newspaper',
                'options': _CODE_OPTIONS,
                'default': 'NY_NYT',
            },
            'custom_code': {
                'type':        'string',
                'label':       'Custom newspaper code (overrides selection above)',
                'default':     '',
                'placeholder': 'e.g. CA_LAT — find codes at frontpages.freedomforum.org',
            },
        }

    def generate(self, config: dict) -> bytes:
        code = (config.get('custom_code') or '').strip().upper() or config.get('newspaper', 'NY_NYT')

        raw_bytes = self._fetch_front_page(code)

        img = Image.open(io.BytesIO(raw_bytes)).convert('RGB')
        img = self._fit_to_frame(img, 1200, 1600)

        buf = io.BytesIO()
        img.save(buf, 'JPEG', quality=92)
        return buf.getvalue()

    # ------------------------------------------------------------------

    def _fetch_front_page(self, code: str) -> bytes:
        """
        Download today's front page JPG from the Freedom Forum CDN.
        URL pattern: https://cdn.freedomforum.org/dfp/jpg{day}/lg/{CODE}.jpg
        """
        day = datetime.now().day  # day of month, no zero-padding
        url = f'https://cdn.freedomforum.org/dfp/jpg{day}/lg/{code}.jpg'

        self.log_info(f'Fetching front page: {url}')

        resp = requests.get(url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; PhotoFrameAssistant/1.0)',
            'Referer':    'https://frontpages.freedomforum.org/',
        })

        if resp.status_code == 404:
            raise ValueError(
                f"No front page found for '{code}' today (day {day}). "
                "The code may be incorrect or today's edition isn't available yet. "
                "Browse frontpages.freedomforum.org to find the correct code."
            )
        if resp.status_code != 200:
            raise ValueError(
                f"CDN returned HTTP {resp.status_code} for {url}"
            )

        content_type = resp.headers.get('Content-Type', '')
        if 'image' not in content_type:
            raise ValueError(
                f"Unexpected content type '{content_type}' — expected an image."
            )

        return resp.content

    # ------------------------------------------------------------------

    def _fit_to_frame(self, img: Image.Image, w: int, h: int) -> Image.Image:
        """Scale image to fit width, crop from top so the masthead is visible."""
        src_w, src_h = img.size
        # Scale to match frame width exactly; newspapers are tall so this
        # usually leaves excess height — crop from the top, not the centre.
        scale = w / src_w
        new_w = w
        new_h = int(src_h * scale)
        if new_h < h:
            # Image is wider than tall (unlikely for a newspaper) — fall back
            # to filling the frame and centre-cropping vertically.
            scale = h / src_h
            new_w = int(src_w * scale)
            new_h = h
            img = img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - w) // 2
            return img.crop((left, 0, left + w, h))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        return img.crop((0, 0, w, h))

