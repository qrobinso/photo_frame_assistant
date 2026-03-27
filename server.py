# server.py — entry point only
import os
import socket

from app import create_app
from settings.persistence import load_server_settings

app = create_app()

if __name__ == '__main__':
    settings = load_server_settings()
    port = settings.get('discovery_port', 5000)
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            local_ip = '127.0.0.1'
        print(f" --- Photo Frame Server --- URL: http://{local_ip}:{port}/")
    is_debug = os.environ.get('FLASK_DEBUG', '').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=is_debug, use_reloader=is_debug)
