"""
Product Hunt Plugin — shows today's featured products from Product Hunt.

Requires a developer access token from https://www.producthunt.com/v2/oauth/applications
Create an application and use the Developer Token (no OAuth flow needed for read-only access).
"""

import requests
from datetime import datetime, timezone

from plugins.base import PluginBase


GRAPHQL_QUERY = """
query TodaysPosts($first: Int!) {
  posts(first: $first, order: VOTES) {
    edges {
      node {
        name
        tagline
        votesCount
        commentsCount
        topics {
          edges {
            node {
              name
            }
          }
        }
      }
    }
  }
}
"""


class ProductHuntPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return 'product_hunt'

    @property
    def display_name(self) -> str:
        return 'Product Hunt'

    @property
    def description(self) -> str:
        return "Shows today's featured products from Product Hunt — name, tagline, vote count, and topics."

    @property
    def default_cron(self) -> str:
        return '0 * * * *'  # Every hour

    @property
    def config_schema(self) -> dict:
        return {
            'access_token': {
                'type':     'string',
                'label':    'Developer Access Token',
                'secret':   True,
                'required': True,
            },
            'num_products': {
                'type':    'integer',
                'label':   'Number of Products',
                'default': 8,
            },
            'theme': {
                'type':    'select',
                'label':   'Theme',
                'options': ['dark', 'light', 'orange'],
                'default': 'dark',
            },
        }

    def generate(self, config: dict) -> bytes:
        token       = config.get('access_token', '').strip()
        num         = max(1, min(int(config.get('num_products', 8)), 20))
        theme       = config.get('theme', 'dark')

        if not token:
            raise ValueError("Product Hunt access token is required.")

        resp = requests.post(
            'https://api.producthunt.com/v2/api/graphql',
            json={'query': GRAPHQL_QUERY, 'variables': {'first': num}},
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type':  'application/json',
                'Accept':        'application/json',
            },
            timeout=15,
        )
        resp.raise_for_status()

        data = resp.json()
        if 'errors' in data:
            raise ValueError(f"Product Hunt API error: {data['errors'][0]['message']}")

        edges = data.get('data', {}).get('posts', {}).get('edges', [])
        if not edges:
            raise ValueError("No products returned from Product Hunt API.")

        products = [e['node'] for e in edges]
        html = self._build_html(products, theme)
        return self.render_html_to_image(html, width=1200, height=1600)

    # ------------------------------------------------------------------

    def _build_html(self, products: list, theme: str) -> str:
        themes = {
            'dark': {
                'bg':         '#0d0d14',
                'header_bg':  '#1a1a2e',
                'card_bg':    '#1e1e2e',
                'fg':         '#e8e8f0',
                'accent':     '#da552f',
                'muted':      '#8888aa',
                'border':     '#2a2a3e',
                'tag_bg':     '#2a2020',
                'tag_fg':     '#ff8c6b',
            },
            'light': {
                'bg':         '#f5f0ee',
                'header_bg':  '#ffffff',
                'card_bg':    '#ffffff',
                'fg':         '#1a1a1a',
                'accent':     '#da552f',
                'muted':      '#777777',
                'border':     '#e8e0dd',
                'tag_bg':     '#fde8e2',
                'tag_fg':     '#c0341d',
            },
            'orange': {
                'bg':         '#1a0d08',
                'header_bg':  '#2d1508',
                'card_bg':    '#230f06',
                'fg':         '#f5e6df',
                'accent':     '#ff6b35',
                'muted':      '#b07050',
                'border':     '#3d2010',
                'tag_bg':     '#3d1c0a',
                'tag_fg':     '#ff9a6c',
            },
        }
        c = themes.get(theme, themes['dark'])
        now = datetime.now(timezone.utc).strftime('%H:%M UTC')
        today = datetime.now(timezone.utc).strftime('%B %-d, %Y')

        cards_html = ''
        for i, p in enumerate(products, start=1):
            name     = p.get('name', '').replace('<', '&lt;').replace('>', '&gt;')
            tagline  = p.get('tagline', '').replace('<', '&lt;').replace('>', '&gt;')
            votes    = p.get('votesCount', 0)
            comments = p.get('commentsCount', 0)

            topic_nodes = p.get('topics', {}).get('edges', [])
            topics_html = ''.join(
                f'<span class="tag">{e["node"]["name"]}</span>'
                for e in topic_nodes[:3]
            )

            cards_html += f"""
            <div class="card">
              <div class="rank">#{i}</div>
              <div class="content">
                <div class="name">{name}</div>
                <div class="tagline">{tagline}</div>
                <div class="meta">
                  <span class="votes">▲ {votes:,}</span>
                  <span class="comments">💬 {comments:,}</span>
                  <span class="tags">{topics_html}</span>
                </div>
              </div>
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
    padding: 44px 72px 32px;
    border-bottom: 3px solid {c['accent']};
    display: flex;
    align-items: center;
    gap: 24px;
  }}
  .ph-logo {{
    font-size: 52px;
    line-height: 1;
  }}
  .header-text {{ flex: 1; }}
  .header-title {{
    font-size: 52px;
    font-weight: 800;
    line-height: 1.1;
  }}
  .header-sub {{
    font-size: 22px;
    color: {c['muted']};
    margin-top: 6px;
  }}
  .timestamp {{
    font-size: 20px;
    color: {c['muted']};
    text-align: right;
    white-space: nowrap;
  }}
  .list {{
    padding: 24px 72px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }}
  .card {{
    background: {c['card_bg']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    padding: 20px 24px;
    display: flex;
    align-items: flex-start;
    gap: 18px;
  }}
  .rank {{
    font-size: 26px;
    font-weight: 800;
    color: {c['accent']};
    min-width: 44px;
    padding-top: 2px;
  }}
  .content {{ flex: 1; min-width: 0; }}
  .name {{
    font-size: 30px;
    font-weight: 700;
    line-height: 1.2;
    margin-bottom: 6px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .tagline {{
    font-size: 22px;
    color: {c['muted']};
    line-height: 1.3;
    margin-bottom: 10px;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }}
  .meta {{
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
  }}
  .votes {{
    font-size: 20px;
    font-weight: 700;
    color: {c['accent']};
  }}
  .comments {{
    font-size: 20px;
    color: {c['muted']};
  }}
  .tags {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }}
  .tag {{
    font-size: 17px;
    background: {c['tag_bg']};
    color: {c['tag_fg']};
    padding: 3px 12px;
    border-radius: 20px;
    font-weight: 600;
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="ph-logo">🐱</div>
    <div class="header-text">
      <div class="header-title">Product Hunt</div>
      <div class="header-sub">Today's Featured Products · {today}</div>
    </div>
    <div class="timestamp">Updated {now}</div>
  </div>
  <div class="list">
    {cards_html}
  </div>
</body>
</html>"""
