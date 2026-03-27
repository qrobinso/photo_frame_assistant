"""
NASA Astronomy Picture of the Day (APOD) Plugin.

Uses NASA's free APOD API. A real API key can be obtained for free at:
https://api.nasa.gov/  — no credit card required.

The special key "DEMO_KEY" works without registration but is rate-limited
to 30 requests/hour and 50/day, which is fine for a daily-refresh plugin.
"""

import io
import textwrap
import requests
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base import PluginBase


class NasaApodPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return 'nasa_apod'

    @property
    def display_name(self) -> str:
        return 'NASA Astronomy Picture of the Day'

    @property
    def description(self) -> str:
        return "Displays NASA's Astronomy Picture of the Day with title and description. Updates daily."

    @property
    def default_cron(self) -> str:
        return '0 7 * * *'  # Daily at 07:00

    @property
    def config_schema(self) -> dict:
        return {
            'api_key': {
                'type':    'string',
                'label':   'NASA API Key (leave blank for DEMO_KEY)',
                'default': 'DEMO_KEY',
            },
            'show_explanation': {
                'type':    'select',
                'label':   'Show Description',
                'options': ['yes', 'no'],
                'default': 'yes',
            },
        }

    def generate(self, config: dict) -> bytes:
        api_key          = (config.get('api_key') or 'DEMO_KEY').strip() or 'DEMO_KEY'
        show_explanation = config.get('show_explanation', 'yes') == 'yes'

        resp = requests.get(
            'https://api.nasa.gov/planetary/apod',
            params={'api_key': api_key, 'hd': 'True'},
            timeout=15,
        )
        resp.raise_for_status()
        apod = resp.json()

        if apod.get('media_type') == 'video':
            raise ValueError(
                f"Today's APOD is a video, not an image. "
                f"Title: {apod.get('title', 'unknown')}. "
                f"Try again tomorrow."
            )

        image_url   = apod.get('hdurl') or apod.get('url')
        title       = apod.get('title', '')
        explanation = apod.get('explanation', '')
        date_str    = apod.get('date', '')
        copyright_  = apod.get('copyright', '').strip().replace('\n', ' ')

        self.log_info(f"APOD: {title} ({date_str})")

        img_resp = requests.get(image_url, timeout=60)
        img_resp.raise_for_status()

        img = Image.open(io.BytesIO(img_resp.content))
        img = ImageOps.exif_transpose(img)
        img = img.convert('RGB')
        img = self._fit_to_frame(img, 1200, 1600)

        img = self._add_overlay(img, title, date_str, explanation if show_explanation else '', copyright_)

        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=92)
        return buf.getvalue()

    # ------------------------------------------------------------------

    def _fit_to_frame(self, img: Image.Image, w: int, h: int) -> Image.Image:
        src_w, src_h = img.size
        scale = max(w / src_w, h / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - w) // 2
        top  = (new_h - h) // 2
        return img.crop((left, top, left + w, top + h))

    def _add_overlay(
        self,
        img: Image.Image,
        title: str,
        date_str: str,
        explanation: str,
        copyright_: str,
    ) -> Image.Image:
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw    = ImageDraw.Draw(overlay)

        # Measure text to determine bar height
        try:
            font_title = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 34)
            font_body  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 24)
            font_meta  = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 20)
        except OSError:
            font_title = font_body = font_meta = ImageFont.load_default()

        padding   = 28
        line_gap  = 8
        max_width = img.width - padding * 2

        # Wrap explanation
        wrapped_lines = []
        if explanation:
            # Truncate to ~400 chars then wrap
            short = explanation[:420].rsplit(' ', 1)[0] + '…' if len(explanation) > 420 else explanation
            for line in textwrap.wrap(short, width=62):
                wrapped_lines.append(line)
            wrapped_lines = wrapped_lines[:5]  # max 5 lines

        # Calculate bar height
        title_h   = 40
        body_h    = (30 + line_gap) * len(wrapped_lines) if wrapped_lines else 0
        meta_h    = 28
        bar_h     = padding + title_h + (12 if wrapped_lines else 0) + body_h + 12 + meta_h + padding

        bar_top = img.height - bar_h
        draw.rectangle([(0, bar_top), (img.width, img.height)], fill=(0, 0, 0, 195))

        # NASA badge line at very top of bar
        draw.rectangle([(0, bar_top), (img.width, bar_top + 4)], fill=(252, 61, 33, 255))

        y = bar_top + padding

        # Title
        draw.text((padding, y), title, font=font_title, fill=(255, 255, 255, 255))
        y += title_h

        # Explanation lines
        if wrapped_lines:
            y += 12
            for line in wrapped_lines:
                draw.text((padding, y), line, font=font_body, fill=(200, 200, 200, 255))
                y += 30 + line_gap

        # Meta line: date + copyright
        y += 12
        meta_parts = []
        if date_str:
            meta_parts.append(date_str)
        if copyright_:
            meta_parts.append(f'© {copyright_}')
        meta_text = '  ·  '.join(meta_parts)
        draw.text((padding, y), meta_text, font=font_meta, fill=(150, 150, 150, 255))

        base     = img.convert('RGBA')
        combined = Image.alpha_composite(base, overlay)
        return combined.convert('RGB')
