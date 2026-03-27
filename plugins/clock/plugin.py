"""
Clock Plugin — renders current time and date as a styled HTML image.

No external API required. Good for testing the plugin system end-to-end.
"""

from datetime import datetime
import zoneinfo

from plugins.base import PluginBase


class ClockPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return 'clock'

    @property
    def display_name(self) -> str:
        return 'Clock'

    @property
    def description(self) -> str:
        return 'Displays the current time and date as a styled full-screen image. No API key needed.'

    @property
    def default_cron(self) -> str:
        return '* * * * *'  # Every minute

    @property
    def config_schema(self) -> dict:
        return {
            'timezone': {
                'type':    'select',
                'label':   'Timezone',
                'default': 'UTC',
                'options': [
                    # UTC
                    'UTC',
                    # Americas
                    'America/New_York', 'America/Chicago', 'America/Denver',
                    'America/Los_Angeles', 'America/Anchorage', 'Pacific/Honolulu',
                    'America/Toronto', 'America/Vancouver', 'America/Montreal',
                    'America/Halifax', 'America/St_Johns', 'America/Regina',
                    'America/Mexico_City', 'America/Bogota', 'America/Lima',
                    'America/Caracas', 'America/La_Paz', 'America/Santiago',
                    'America/Argentina/Buenos_Aires', 'America/Sao_Paulo',
                    # Europe
                    'Europe/London', 'Europe/Dublin', 'Europe/Lisbon',
                    'Europe/Paris', 'Europe/Madrid', 'Europe/Rome',
                    'Europe/Berlin', 'Europe/Amsterdam', 'Europe/Brussels',
                    'Europe/Zurich', 'Europe/Vienna', 'Europe/Warsaw',
                    'Europe/Prague', 'Europe/Budapest', 'Europe/Stockholm',
                    'Europe/Oslo', 'Europe/Copenhagen', 'Europe/Helsinki',
                    'Europe/Riga', 'Europe/Tallinn', 'Europe/Vilnius',
                    'Europe/Bucharest', 'Europe/Sofia', 'Europe/Athens',
                    'Europe/Istanbul', 'Europe/Kyiv', 'Europe/Minsk',
                    'Europe/Moscow', 'Europe/Samara', 'Europe/Yekaterinburg',
                    # Middle East / Africa
                    'Asia/Dubai', 'Asia/Tehran', 'Asia/Riyadh', 'Asia/Beirut',
                    'Asia/Jerusalem', 'Africa/Cairo', 'Africa/Nairobi',
                    'Africa/Johannesburg', 'Africa/Lagos', 'Africa/Casablanca',
                    # Asia
                    'Asia/Karachi', 'Asia/Kolkata', 'Asia/Kathmandu',
                    'Asia/Dhaka', 'Asia/Colombo', 'Asia/Yangon',
                    'Asia/Bangkok', 'Asia/Ho_Chi_Minh', 'Asia/Jakarta',
                    'Asia/Singapore', 'Asia/Kuala_Lumpur', 'Asia/Manila',
                    'Asia/Taipei', 'Asia/Hong_Kong', 'Asia/Shanghai',
                    'Asia/Seoul', 'Asia/Tokyo', 'Asia/Vladivostok',
                    # Australia / Pacific
                    'Australia/Perth', 'Australia/Darwin', 'Australia/Adelaide',
                    'Australia/Brisbane', 'Australia/Sydney', 'Australia/Melbourne',
                    'Pacific/Auckland', 'Pacific/Fiji', 'Pacific/Honolulu',
                ],
            },
            'theme': {
                'type':    'select',
                'label':   'Theme',
                'options': ['dark', 'light', 'midnight', 'sunrise'],
                'default': 'dark',
            },
        }

    def generate(self, config: dict) -> bytes:
        tz_name = config.get('timezone', 'UTC')
        theme = config.get('theme', 'dark')

        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            self.log_warning(f"Unknown timezone '{tz_name}', falling back to UTC")
            tz = zoneinfo.ZoneInfo('UTC')

        now = datetime.now(tz)
        time_str = now.strftime('%H:%M')
        date_str = now.strftime('%A, %B %-d')
        tz_str   = tz_name

        themes = {
            'dark':     {'bg': '#1a1a2e', 'fg': '#e0e0f0', 'accent': '#7c6af7'},
            'light':    {'bg': '#f4f4f0', 'fg': '#1a1a2e', 'accent': '#4a3fbf'},
            'midnight': {'bg': '#0d0d1a', 'fg': '#c0c0e0', 'accent': '#5050cc'},
            'sunrise':  {'bg': '#ff7043', 'fg': '#fff8e1', 'accent': '#ffcc02'},
        }
        c = themes.get(theme, themes['dark'])

        html = f"""<!DOCTYPE html>
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
    font-family: 'Georgia', serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 40px;
  }}
  .time {{
    font-size: 220px;
    font-weight: 200;
    letter-spacing: -8px;
    color: {c['fg']};
    line-height: 1;
  }}
  .divider {{
    width: 120px;
    height: 4px;
    background: {c['accent']};
    border-radius: 2px;
  }}
  .date {{
    font-size: 64px;
    font-weight: 300;
    letter-spacing: 4px;
    color: {c['fg']};
    opacity: 0.85;
  }}
  .tz {{
    font-size: 28px;
    color: {c['accent']};
    letter-spacing: 6px;
    text-transform: uppercase;
    opacity: 0.7;
  }}
</style>
</head>
<body>
  <div class="time">{time_str}</div>
  <div class="divider"></div>
  <div class="date">{date_str}</div>
  <div class="tz">{tz_str}</div>
</body>
</html>"""

        return self.render_html_to_image(html, width=1200, height=1600, wait_until='domcontentloaded')
