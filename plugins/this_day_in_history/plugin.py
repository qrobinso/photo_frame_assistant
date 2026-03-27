"""
This Day in History Plugin — historical events from Wikipedia's On This Day feed.

No API key required. Uses the Wikipedia REST API:
https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month}/{day}

A different random selection of events is shown each time it runs,
so the frame feels fresh even though the underlying pool only changes daily.
"""

import random
import requests
from datetime import datetime, timezone

from plugins.base import PluginBase


class ThisDayInHistoryPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return 'this_day_in_history'

    @property
    def display_name(self) -> str:
        return 'This Day in History'

    @property
    def description(self) -> str:
        return "Shows notable historical events that happened on today's date, sourced from Wikipedia. No API key required."

    @property
    def default_cron(self) -> str:
        return '0 6 * * *'  # Daily at 06:00

    @property
    def config_schema(self) -> dict:
        return {
            'num_events': {
                'type':    'integer',
                'label':   'Number of Events',
                'default': 7,
            },
            'theme': {
                'type':    'select',
                'label':   'Theme',
                'options': ['dark', 'light', 'sepia'],
                'default': 'dark',
            },
        }

    def generate(self, config: dict) -> bytes:
        num_events = max(1, min(int(config.get('num_events', 7)), 15))
        theme      = config.get('theme', 'dark')

        now   = datetime.now(timezone.utc)
        month = now.month
        day   = now.day

        resp = requests.get(
            f'https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month}/{day}',
            headers={'User-Agent': 'PhotoFrameAssistant/1.0 (plugin; contact@example.com)'},
            timeout=15,
        )
        resp.raise_for_status()

        events = resp.json().get('events', [])
        if not events:
            raise ValueError(f"Wikipedia returned no events for {month}/{day}.")

        # Pick a random sample (sorted ascending by year for display)
        sample = random.sample(events, min(num_events, len(events)))
        sample.sort(key=lambda e: e.get('year', 0))

        html = self._build_html(sample, now, theme)
        return self.render_html_to_image(html, width=1200, height=1600)

    # ------------------------------------------------------------------

    def _build_html(self, events: list, now: datetime, theme: str) -> str:
        themes = {
            'dark': {
                'bg':         '#0d0d14',
                'header_bg':  '#13131f',
                'card_bg':    '#17172a',
                'fg':         '#e8e8f5',
                'muted':      '#8888aa',
                'accent':     '#7c6fcd',
                'border':     '#25254a',
                'year_bg':    '#1e1e3a',
                'year_fg':    '#a89bff',
                'dot':        '#7c6fcd',
                'line':       '#2a2a4a',
            },
            'light': {
                'bg':         '#f4f4f8',
                'header_bg':  '#ffffff',
                'card_bg':    '#ffffff',
                'fg':         '#1a1a2e',
                'muted':      '#666688',
                'accent':     '#5b4fcf',
                'border':     '#ddd8ee',
                'year_bg':    '#eeebff',
                'year_fg':    '#4a3db0',
                'dot':        '#5b4fcf',
                'line':       '#d8d4ee',
            },
            'sepia': {
                'bg':         '#1a1510',
                'header_bg':  '#221c14',
                'card_bg':    '#1e1810',
                'fg':         '#e8dcc8',
                'muted':      '#9a8870',
                'accent':     '#c8a45a',
                'border':     '#3a3020',
                'year_bg':    '#2e2418',
                'year_fg':    '#e8c078',
                'dot':        '#c8a45a',
                'line':       '#3a3020',
            },
        }
        c      = themes.get(theme, themes['dark'])
        month_str = now.strftime('%B')
        day_str   = now.strftime('%-d')
        year_str  = now.strftime('%Y')

        # Ordinal suffix
        d = int(day_str)
        suffix = 'th' if 11 <= d <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(d % 10, 'th')

        events_html = ''
        for event in events:
            year = event.get('year', '')
            text = event.get('text', '').replace('<', '&lt;').replace('>', '&gt;')
            # Trim to ~220 chars
            if len(text) > 220:
                text = text[:218].rsplit(' ', 1)[0] + '…'

            events_html += f"""
            <div class="event">
              <div class="event-left">
                <div class="year-badge">{year}</div>
                <div class="timeline-line"></div>
              </div>
              <div class="event-text">{text}</div>
            </div>"""

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1200px;
    height: 1600px;
    background: {c['bg']};
    color: {c['fg']};
    font-family: 'Georgia', 'Times New Roman', serif;
    overflow: hidden;
  }}

  /* ── Header ── */
  .header {{
    background: {c['header_bg']};
    padding: 52px 80px 40px;
    border-bottom: 3px solid {c['accent']};
  }}
  .eyebrow {{
    font-family: -apple-system, Arial, sans-serif;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 4px;
    text-transform: uppercase;
    color: {c['accent']};
    margin-bottom: 14px;
  }}
  .date-display {{
    font-size: 68px;
    font-weight: 700;
    line-height: 1;
    letter-spacing: -1px;
  }}
  .date-sub {{
    font-size: 26px;
    color: {c['muted']};
    margin-top: 10px;
    font-family: -apple-system, Arial, sans-serif;
  }}

  /* ── Events list ── */
  .events {{
    padding: 32px 80px 24px;
    display: flex;
    flex-direction: column;
    gap: 0;
  }}
  .event {{
    display: flex;
    align-items: flex-start;
    gap: 24px;
    padding: 16px 0;
  }}
  .event-left {{
    display: flex;
    flex-direction: column;
    align-items: center;
    flex-shrink: 0;
    width: 88px;
  }}
  .year-badge {{
    background: {c['year_bg']};
    color: {c['year_fg']};
    font-family: -apple-system, Arial, sans-serif;
    font-size: 18px;
    font-weight: 800;
    padding: 5px 0;
    width: 88px;
    text-align: center;
    border-radius: 6px;
    letter-spacing: 0.5px;
  }}
  .timeline-line {{
    width: 2px;
    flex: 1;
    min-height: 20px;
    background: {c['line']};
    margin-top: 6px;
  }}
  .event:last-child .timeline-line {{
    display: none;
  }}
  .event-text {{
    font-size: 26px;
    line-height: 1.45;
    color: {c['fg']};
    padding-top: 4px;
    flex: 1;
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="eyebrow">On This Day in History</div>
    <div class="date-display">{month_str} {day_str}<sup style="font-size:32px">{suffix}</sup></div>
    <div class="date-sub">{year_str} · Notable events throughout history</div>
  </div>
  <div class="events">
    {events_html}
  </div>
</body>
</html>"""
