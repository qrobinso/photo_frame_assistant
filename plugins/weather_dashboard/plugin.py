"""
Weather Dashboard Plugin — current conditions + 5-day forecast.

Uses OpenWeatherMap's free API (no credit card required):
https://openweathermap.org/api  →  sign up → copy your API key.

Free tier: 60 calls/min, 1 million calls/month.
"""

import requests
from collections import defaultdict
from datetime import datetime, timezone

from plugins.base import PluginBase


# OWM condition code → emoji + short label
# https://openweathermap.org/weather-conditions
def _condition(code: int) -> tuple[str, str]:
    if code == 800:
        return '☀️', 'Clear'
    if code == 801:
        return '🌤', 'Mostly Clear'
    if code in (802, 803):
        return '⛅', 'Partly Cloudy'
    if code == 804:
        return '☁️', 'Overcast'
    if 200 <= code < 300:
        return '⛈', 'Thunderstorm'
    if 300 <= code < 400:
        return '🌦', 'Drizzle'
    if 500 <= code < 600:
        return '🌧', 'Rain'
    if 600 <= code < 700:
        return '🌨', 'Snow'
    if code in (701, 711, 721, 731, 741, 751, 761, 762):
        return '🌫', 'Fog / Haze'
    if code == 771:
        return '💨', 'Squalls'
    if code == 781:
        return '🌪', 'Tornado'
    return '🌡', 'Unknown'


_UNIT_LABELS = {
    'metric':   {'temp': 'C', 'speed': 'm/s'},
    'imperial': {'temp': 'F', 'speed': 'mph'},
    'standard': {'temp': 'K', 'speed': 'm/s'},
}

_DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


class WeatherDashboardPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return 'weather_dashboard'

    @property
    def display_name(self) -> str:
        return 'Weather Dashboard'

    @property
    def description(self) -> str:
        return 'Shows current conditions and a 5-day forecast for any city. Requires a free OpenWeatherMap API key.'

    @property
    def default_cron(self) -> str:
        return '0 * * * *'  # Every hour

    @property
    def config_schema(self) -> dict:
        return {
            'api_key': {
                'type':     'string',
                'label':    'OpenWeatherMap API Key',
                'secret':   True,
                'required': True,
            },
            'location': {
                'type':     'string',
                'label':    'Location — city (e.g. London), city+country (London,UK), US city+state+country (Springfield,NJ,US), or lat,lon',
                'required': True,
                'default':  'London',
            },
            'units': {
                'type':    'select',
                'label':   'Units',
                'options': ['metric', 'imperial', 'standard'],
                'default': 'metric',
            },
            'theme': {
                'type':    'select',
                'label':   'Theme',
                'options': ['dark', 'light'],
                'default': 'dark',
            },
        }

    def generate(self, config: dict) -> bytes:
        api_key  = config.get('api_key', '').strip()
        location = config.get('location', 'London').strip()
        units    = config.get('units', 'metric')
        theme    = config.get('theme', 'dark')

        if not api_key:
            raise ValueError("OpenWeatherMap API key is required.")
        if not location:
            raise ValueError("Location is required.")

        # Normalise location: strip whitespace around commas so "Springfield, NJ"
        # becomes "Springfield,NJ" and "Springfield, NJ, US" → "Springfield,NJ,US".
        # OWM requires no spaces and uses ISO codes: city,state_code,country_code
        # e.g. "Springfield,NJ,US"  or just  "London,UK"
        normalised_location = ','.join(p.strip() for p in location.split(','))

        # Build query params — support "lat,lon" numeric format
        def _params(extra: dict) -> dict:
            base = {'appid': api_key, 'units': units}
            parts = normalised_location.split(',')
            if len(parts) == 2 and all(
                p.lstrip('-').replace('.', '', 1).isdigit() for p in parts
            ):
                base.update({'lat': parts[0], 'lon': parts[1]})
            else:
                base['q'] = normalised_location
            base.update(extra)
            return base

        current_resp = requests.get(
            'https://api.openweathermap.org/data/2.5/weather',
            params=_params({}),
            timeout=15,
        )
        current_resp.raise_for_status()
        current = current_resp.json()

        forecast_resp = requests.get(
            'https://api.openweathermap.org/data/2.5/forecast',
            params=_params({'cnt': 40}),
            timeout=15,
        )
        forecast_resp.raise_for_status()
        forecast = forecast_resp.json()

        html = self._build_html(current, forecast, units, theme)
        return self.render_html_to_image(html, width=1200, height=1600)

    # ------------------------------------------------------------------

    def _build_html(self, current: dict, forecast: dict, units: str, theme: str) -> str:
        themes = {
            'dark': {
                'bg':        '#0b0f1a',
                'card':      '#141928',
                'border':    '#1e2740',
                'fg':        '#e8eaf6',
                'muted':     '#7986a8',
                'accent':    '#5c9eff',
                'sub_card':  '#1a2035',
                'divider':   '#1e2740',
                'badge_bg':  '#1e2f55',
                'badge_fg':  '#82aaff',
            },
            'light': {
                'bg':        '#eef2f8',
                'card':      '#ffffff',
                'border':    '#d0d8e8',
                'fg':        '#1a1f30',
                'muted':     '#6b7a9a',
                'accent':    '#2563eb',
                'sub_card':  '#f5f7fc',
                'divider':   '#dde3ee',
                'badge_bg':  '#dbeafe',
                'badge_fg':  '#1d4ed8',
            },
        }
        c = themes.get(theme, themes['dark'])

        ul = _UNIT_LABELS.get(units, _UNIT_LABELS['metric'])
        t_unit = ul['temp']
        s_unit = ul['speed']

        # --- Current conditions ---
        city_name   = current.get('name', '')
        country     = current.get('sys', {}).get('country', '')
        temp        = round(current['main']['temp'])
        feels_like  = round(current['main']['feels_like'])
        humidity    = current['main']['humidity']
        pressure    = current['main']['pressure']
        wind_speed  = round(current.get('wind', {}).get('speed', 0))
        visibility  = round(current.get('visibility', 0) / 1000, 1)
        cond_desc   = current['weather'][0]['description'].title()
        icon_code   = current['weather'][0].get('icon', '01d')
        sunrise_ts  = current.get('sys', {}).get('sunrise', 0)
        sunset_ts   = current.get('sys', {}).get('sunset', 0)
        tz_offset   = current.get('timezone', 0)

        def _icon_img(code: str, size: int) -> str:
            url = f'https://openweathermap.org/img/wn/{code}@2x.png'
            return f'<img src="{url}" width="{size}" height="{size}" style="display:block">'

        def _local_time(ts: int) -> str:
            if not ts:
                return ''
            local_dt = datetime.fromtimestamp(ts + tz_offset, tz=timezone.utc)
            return local_dt.strftime('%H:%M')

        local_now = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + tz_offset, tz=timezone.utc
        )
        now_str = local_now.strftime('%H:%M local')

        # --- 5-day forecast: group 3-hour slots by calendar day ---
        daily: dict[str, dict] = {}
        for slot in forecast.get('list', []):
            slot_dt  = datetime.fromtimestamp(slot['dt'] + tz_offset, tz=timezone.utc)
            day_key  = slot_dt.strftime('%Y-%m-%d')
            day_name = _DAY_NAMES[slot_dt.weekday()]
            if day_key not in daily:
                daily[day_key] = {
                    'name':   day_name,
                    'temps':  [],
                    'icons':  [],
                }
            daily[day_key]['temps'].append(slot['main']['temp'])
            # Prefer daytime icon codes (those ending in 'd')
            slot_icon = slot['weather'][0].get('icon', '01d')
            if slot_icon.endswith('d'):
                daily[day_key]['icons'].insert(0, slot_icon)
            else:
                daily[day_key]['icons'].append(slot_icon)

        # Take up to 5 days (skip today)
        today_key  = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        forecast_days = [
            v for k, v in list(daily.items()) if k != today_key
        ][:5]

        forecast_cards = ''
        for day in forecast_days:
            hi          = round(max(day['temps']))
            lo          = round(min(day['temps']))
            day_icon    = day['icons'][0] if day['icons'] else '01d'
            forecast_cards += f"""
            <div class="fc-card">
              <div class="fc-day">{day['name']}</div>
              <div class="fc-icon">{_icon_img(day_icon, 80)}</div>
              <div class="fc-hi">{hi}°{t_unit}</div>
              <div class="fc-lo">{lo}°{t_unit}</div>
            </div>"""

        # --- Stat tiles ---
        def stat(label: str, value: str) -> str:
            return f"""
            <div class="stat-tile">
              <div class="stat-label">{label}</div>
              <div class="stat-value">{value}</div>
            </div>"""

        stats_html = (
            stat('Humidity',    f'{humidity}%') +
            stat('Wind',        f'{wind_speed} {s_unit}') +
            stat('Pressure',    f'{pressure} hPa') +
            stat('Visibility',  f'{visibility} km') +
            stat('Sunrise',     _local_time(sunrise_ts)) +
            stat('Sunset',      _local_time(sunset_ts))
        )

        location_label = f'{city_name}, {country}' if country else city_name

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1200px; height: 1600px;
    background: {c['bg']};
    color: {c['fg']};
    font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
    overflow: hidden;
    padding: 56px 72px 48px;
    display: flex;
    flex-direction: column;
    gap: 28px;
  }}

  /* ── Header ── */
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
  }}
  .location {{ font-size: 36px; font-weight: 700; }}
  .updated  {{ font-size: 20px; color: {c['muted']}; margin-top: 6px; }}
  .badge {{
    background: {c['badge_bg']};
    color: {c['badge_fg']};
    font-size: 18px;
    font-weight: 600;
    padding: 6px 18px;
    border-radius: 20px;
  }}

  /* ── Current conditions ── */
  .current-card {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 16px;
    padding: 44px 52px;
    display: flex;
    align-items: center;
    gap: 40px;
  }}
  .main-icon   {{ width: 160px; height: 160px; flex-shrink: 0; }}
  .main-right  {{ flex: 1; }}
  .main-temp   {{ font-size: 110px; font-weight: 800; line-height: 1; letter-spacing: -4px; }}
  .main-desc   {{ font-size: 36px; color: {c['muted']}; margin-top: 8px; }}
  .feels-like  {{ font-size: 24px; color: {c['muted']}; margin-top: 10px; }}

  /* ── Stats grid ── */
  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
  }}
  .stat-tile {{
    background: {c['sub_card']};
    border: 1px solid {c['border']};
    border-radius: 12px;
    padding: 20px 24px;
  }}
  .stat-label {{ font-size: 20px; color: {c['muted']}; margin-bottom: 6px; }}
  .stat-value {{ font-size: 32px; font-weight: 700; }}

  /* ── 5-day forecast ── */
  .forecast-row {{
    display: flex;
    gap: 16px;
  }}
  .fc-card {{
    background: {c['card']};
    border: 1px solid {c['border']};
    border-radius: 12px;
    flex: 1;
    padding: 24px 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
  }}
  .fc-day  {{ font-size: 22px; font-weight: 700; color: {c['muted']}; }}
  .fc-icon {{ display: flex; justify-content: center; }}
  .fc-hi   {{ font-size: 30px; font-weight: 800; }}
  .fc-lo   {{ font-size: 24px; color: {c['muted']}; }}

  /* ── Section label ── */
  .section-label {{
    font-size: 20px;
    font-weight: 700;
    color: {c['muted']};
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: -10px;
  }}
</style>
</head>
<body>
  <div class="header">
    <div>
      <div class="location">{location_label}</div>
      <div class="updated">Updated {now_str}</div>
    </div>
    <div class="badge">Weather</div>
  </div>

  <div class="current-card">
    <div class="main-icon">{_icon_img(icon_code, 160)}</div>
    <div class="main-right">
      <div class="main-temp">{temp}°{t_unit}</div>
      <div class="main-desc">{cond_desc}</div>
      <div class="feels-like">Feels like {feels_like}°{t_unit}</div>
    </div>
  </div>

  <div class="section-label">Details</div>
  <div class="stats-grid">
    {stats_html}
  </div>

  <div class="section-label">5-Day Forecast</div>
  <div class="forecast-row">
    {forecast_cards}
  </div>
</body>
</html>"""
