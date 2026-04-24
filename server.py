# server.py — entry point only
import os
import socket

from app import create_app
from settings.persistence import load_server_settings


def _is_production():
    return os.environ.get('FLASK_ENV', '').lower() == 'production'


def _use_reloader():
    # Auto-reload on file changes by default. Docker sets FLASK_ENV=production
    # to opt out. Set FLASK_RELOAD=false to disable explicitly.
    if _is_production():
        return False
    return os.environ.get('FLASK_RELOAD', 'true').lower() != 'false'


def _is_reloader_supervisor():
    return _use_reloader() and os.environ.get('WERKZEUG_RUN_MAIN') != 'true'


app = create_app(skip_services=_is_reloader_supervisor())

if __name__ == '__main__':
    settings = load_server_settings()
    port = settings.get('discovery_port', 5000)
    is_debug = os.environ.get('FLASK_DEBUG', '').lower() == 'true'
    use_reloader = _use_reloader()

    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not use_reloader:
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            local_ip = '127.0.0.1'
        mode = "dev (auto-reload)" if use_reloader else "production"
        print(f" --- Photo Frame Server [{mode}] --- URL: http://{local_ip}:{port}/")

    app.run(host='0.0.0.0', port=port, debug=is_debug, use_reloader=use_reloader)
