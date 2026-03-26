"""
Sports Scores Plugin — fetches live and recent scores from ESPN.

Uses ESPN's free public API (no key required).
"""

import requests
from datetime import datetime, timezone

from plugins.base_plugin import PluginBase


_LEAGUE_MAP = {
    'NFL':            ('football',   'nfl'),
    'NBA':            ('basketball', 'nba'),
    'MLB':            ('baseball',   'mlb'),
    'NHL':            ('hockey',     'nhl'),
    'Premier League': ('soccer',     'eng.1'),
    'MLS':            ('soccer',     'usa.1'),
    'La Liga':        ('soccer',     'esp.1'),
    'Serie A':        ('soccer',     'ita.1'),
    'Bundesliga':     ('soccer',     'ger.1'),
    'Champions League': ('soccer',   'uefa.champions'),
}


class SportsScoresPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return 'sports_scores'

    @property
    def display_name(self) -> str:
        return 'Sports Scores'

    @property
    def description(self) -> str:
        return 'Shows live and recent scores from ESPN. No API key required. Updates every 15 minutes.'

    @property
    def default_cron(self) -> str:
        return '*/15 * * * *'

    @property
    def config_schema(self) -> dict:
        return {
            'league': {
                'type':    'select',
                'label':   'League',
                'options': list(_LEAGUE_MAP.keys()),
                'default': 'NBA',
            },
            'num_games': {
                'type':    'integer',
                'label':   'Max Games to Show',
                'default': 8,
            },
        }

    def generate(self, config: dict) -> bytes:
        league = config.get('league', 'NBA')
        num_games = int(config.get('num_games', 8))

        sport, slug = _LEAGUE_MAP.get(league, ('basketball', 'nba'))

        url = f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard'
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()

        events = resp.json().get('events', [])[:num_games]

        if not events:
            html = self._build_no_games_html(league)
        else:
            games = [self._parse_event(e) for e in events]
            html = self._build_html(games, league)

        return self.render_html_to_image(html, width=1200, height=1600)

    def _parse_event(self, event: dict) -> dict:
        competition = event.get('competitions', [{}])[0]
        competitors = competition.get('competitors', [])

        away = home = None
        for comp in competitors:
            team_data = {
                'abbrev': comp.get('team', {}).get('abbreviation', '???'),
                'name':   comp.get('team', {}).get('shortDisplayName', ''),
                'score':  comp.get('score', '-'),
                'winner': comp.get('winner', False),
            }
            if comp.get('homeAway') == 'home':
                home = team_data
            else:
                away = team_data

        if not away:
            away = {'abbrev': '???', 'name': '', 'score': '-', 'winner': False}
        if not home:
            home = {'abbrev': '???', 'name': '', 'score': '-', 'winner': False}

        status_type = event.get('status', {}).get('type', {})
        state = status_type.get('state', 'pre')  # pre, in, post
        detail = status_type.get('detail', '')
        short_detail = status_type.get('shortDetail', detail)

        return {
            'away': away,
            'home': home,
            'state': state,
            'detail': short_detail,
        }

    def _build_html(self, games: list, league: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime('%H:%M UTC')

        cards_html = ''
        for g in games:
            state = g['state']
            if state == 'in':
                border_color = '#00c853'
                status_class = 'status-live'
                dot = '<span class="live-dot"></span>'
            elif state == 'post':
                border_color = '#555'
                status_class = 'status-final'
                dot = ''
            else:
                border_color = '#5c9eff'
                status_class = 'status-scheduled'
                dot = ''

            away = g['away']
            home = g['home']
            away_bold = ' style="font-weight:800"' if away['winner'] else ''
            home_bold = ' style="font-weight:800"' if home['winner'] else ''

            cards_html += f"""
            <div class="game-card" style="border-left-color: {border_color}">
              <div class="teams-row">
                <div class="team away">
                  <span class="abbrev"{away_bold}>{away['abbrev']}</span>
                  <span class="score"{away_bold}>{away['score']}</span>
                </div>
                <span class="at-sign">@</span>
                <div class="team home">
                  <span class="score"{home_bold}>{home['score']}</span>
                  <span class="abbrev"{home_bold}>{home['abbrev']}</span>
                </div>
              </div>
              <div class="team-names">
                <span class="team-name">{away['name']}</span>
                <span class="team-name">{home['name']}</span>
              </div>
              <div class="game-status {status_class}">{dot}{g['detail']}</div>
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
    background: #0f0f1a;
    color: #e8e8f0;
    font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
    overflow: hidden;
  }}
  .header {{
    background: #1a1a2e;
    padding: 48px 80px 36px;
    border-bottom: 3px solid #e94560;
  }}
  .league-tag {{
    display: inline-block;
    background: #e94560;
    color: #fff;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 3px;
    text-transform: uppercase;
    padding: 6px 18px;
    border-radius: 4px;
    margin-bottom: 16px;
  }}
  .header-title {{
    font-size: 60px;
    font-weight: 700;
    line-height: 1.1;
    margin-bottom: 12px;
  }}
  .timestamp {{
    font-size: 22px;
    color: #888aaa;
    letter-spacing: 1px;
  }}
  .games {{
    padding: 32px 80px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }}
  .game-card {{
    background: #1e1e30;
    border: 1px solid #2a2a40;
    border-left: 4px solid #555;
    border-radius: 8px;
    padding: 22px 28px;
  }}
  .teams-row {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 20px;
    margin-bottom: 6px;
  }}
  .team {{
    display: flex;
    align-items: baseline;
    gap: 14px;
  }}
  .team.away {{
    flex-direction: row;
    justify-content: flex-end;
    flex: 1;
  }}
  .team.home {{
    flex-direction: row;
    justify-content: flex-start;
    flex: 1;
  }}
  .abbrev {{
    font-size: 32px;
    font-weight: 600;
    letter-spacing: 2px;
  }}
  .score {{
    font-size: 38px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }}
  .at-sign {{
    font-size: 24px;
    color: #555;
    font-weight: 300;
  }}
  .team-names {{
    display: flex;
    justify-content: space-between;
    padding: 0 4px;
    margin-bottom: 8px;
  }}
  .team-name {{
    font-size: 16px;
    color: #888aaa;
  }}
  .game-status {{
    font-size: 18px;
    text-align: center;
    letter-spacing: 1px;
  }}
  .status-final {{
    color: #888aaa;
  }}
  .status-live {{
    color: #00c853;
    font-weight: 600;
  }}
  .status-scheduled {{
    color: #5c9eff;
  }}
  .live-dot {{
    display: inline-block;
    width: 8px;
    height: 8px;
    background: #00c853;
    border-radius: 50%;
    margin-right: 8px;
    vertical-align: middle;
    animation: pulse 1.5s infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.3; }}
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="league-tag">{league}</div>
    <div class="header-title">Scores</div>
    <div class="timestamp">Updated {timestamp}</div>
  </div>
  <div class="games">
    {cards_html}
  </div>
</body>
</html>"""

    def _build_no_games_html(self, league: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime('%H:%M UTC')
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1200px;
    height: 1600px;
    background: #0f0f1a;
    color: #e8e8f0;
    font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
  }}
  .league-tag {{
    display: inline-block;
    background: #e94560;
    color: #fff;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 3px;
    text-transform: uppercase;
    padding: 6px 18px;
    border-radius: 4px;
    margin-bottom: 24px;
  }}
  .message {{
    font-size: 36px;
    color: #888aaa;
    margin-bottom: 12px;
  }}
  .timestamp {{
    font-size: 22px;
    color: #555;
  }}
</style>
</head>
<body>
  <div class="league-tag">{league}</div>
  <div class="message">No games scheduled today</div>
  <div class="timestamp">Checked {timestamp}</div>
</body>
</html>"""
