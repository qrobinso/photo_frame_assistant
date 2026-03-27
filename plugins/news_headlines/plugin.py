"""
News Headlines Plugin — fetches top headlines from NewsAPI.org and renders them.

Requires a free API key from https://newsapi.org/
"""

import requests
from datetime import datetime

from plugins.base import PluginBase


class NewsHeadlinesPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return 'news_headlines'

    @property
    def display_name(self) -> str:
        return 'News Headlines'

    @property
    def description(self) -> str:
        return 'Fetches top headlines from NewsAPI.org and renders them as a styled image. Updates hourly.'

    @property
    def default_cron(self) -> str:
        return '0 * * * *'  # Every hour

    @property
    def config_schema(self) -> dict:
        return {
            'api_key': {
                'type':     'string',
                'label':    'NewsAPI Key',
                'secret':   True,
                'required': True,
            },
            'country': {
                'type':    'string',
                'label':   'Country Code (e.g. us, gb, au)',
                'default': 'us',
            },
            'category': {
                'type':    'select',
                'label':   'Category',
                'options': ['general', 'technology', 'business', 'science', 'health', 'entertainment', 'sports'],
                'default': 'technology',
            },
            'num_headlines': {
                'type':    'integer',
                'label':   'Number of Headlines',
                'default': 8,
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
        country  = config.get('country', 'us').strip() or 'us'
        category = config.get('category', 'technology')
        num      = int(config.get('num_headlines', 8))
        theme    = config.get('theme', 'dark')

        if not api_key:
            raise ValueError("NewsAPI key is required. Add it in the plugin config.")

        resp = requests.get(
            'https://newsapi.org/v2/top-headlines',
            params={
                'country':  country,
                'category': category,
                'pageSize': min(num, 20),
                'apiKey':   api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        articles = resp.json().get('articles', [])[:num]

        if not articles:
            raise ValueError(f"No articles returned for country='{country}' category='{category}'")

        html = self._build_html(articles, category, theme)
        return self.render_html_to_image(html, width=1200, height=1600)

    def _build_html(self, articles: list, category: str, theme: str) -> str:
        themes = {
            'dark':  {'bg': '#0f0f1a', 'header_bg': '#1a1a2e', 'card_bg': '#1e1e30',
                      'fg': '#e8e8f0', 'accent': '#e94560', 'source': '#888aaa',
                      'border': '#2a2a40'},
            'light': {'bg': '#f0f0f5', 'header_bg': '#ffffff', 'card_bg': '#ffffff',
                      'fg': '#1a1a2e', 'accent': '#c0392b', 'source': '#666688',
                      'border': '#dde'},
        }
        c = themes.get(theme, themes['dark'])
        timestamp = datetime.utcnow().strftime('%H:%M UTC')

        items_html = ''
        for a in articles:
            source = a.get('source', {}).get('name', '')
            title = a.get('title', '').replace('<', '&lt;').replace('>', '&gt;')
            items_html += f"""
            <div class="headline-card">
              <div class="source">{source}</div>
              <div class="title">{title}</div>
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
    font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
    overflow: hidden;
  }}
  .header {{
    background: {c['header_bg']};
    padding: 48px 80px 36px;
    border-bottom: 3px solid {c['accent']};
  }}
  .category-tag {{
    display: inline-block;
    background: {c['accent']};
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
    color: {c['source']};
    letter-spacing: 1px;
  }}
  .headlines {{
    padding: 32px 80px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }}
  .headline-card {{
    background: {c['card_bg']};
    border: 1px solid {c['border']};
    border-left: 4px solid {c['accent']};
    border-radius: 8px;
    padding: 22px 28px;
  }}
  .source {{
    font-size: 18px;
    color: {c['source']};
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 8px;
  }}
  .title {{
    font-size: 28px;
    line-height: 1.35;
    color: {c['fg']};
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="category-tag">{category}</div>
    <div class="header-title">Today's Headlines</div>
    <div class="timestamp">Updated {timestamp}</div>
  </div>
  <div class="headlines">
    {items_html}
  </div>
</body>
</html>"""
