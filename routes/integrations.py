import logging
import os

from flask import Blueprint, current_app, jsonify, render_template, request

from settings.persistence import load_mqtt_settings, save_mqtt_settings

logger = logging.getLogger(__name__)

integrations_bp = Blueprint('integrations', __name__)


# ------------------------------------------------------------------------------
# MQTT routes
# ------------------------------------------------------------------------------

@integrations_bp.route('/integrations')
def integrations_page():
    logger.info("Accessing integrations page")
    try:
        mqtt_settings = load_mqtt_settings()
        mqtt_status = "Disabled"
        if mqtt_settings.get('enabled'):
            mqtt_status = current_app.mqtt_integration.status if (
                hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration
            ) else "Unknown"

        return render_template('integrations.html',
                               mqtt_enabled=mqtt_settings.get('enabled', False),
                               mqtt_settings=mqtt_settings,
                               mqtt_status=mqtt_status)
    except Exception as e:
        logger.error(f"Error in integrations_page: {e}")
        return f"Error loading integrations page: {str(e)}", 500


@integrations_bp.route('/api/integrations/mqtt/settings', methods=['POST'])
def mqtt_settings():
    from model import db, PhotoFrame, PlaylistEntry, CustomPlaylist
    from integrations.mqtt import MQTTIntegration

    try:
        data = request.json
        mqtt_settings = {
            'enabled': data.get('enabled', False),
            'broker': data.get('broker'),
            'port': data.get('port', 1883),
            'username': data.get('username'),
            'password': data.get('password'),
        }
        save_mqtt_settings(mqtt_settings)

        if mqtt_settings['enabled']:
            if not hasattr(current_app, 'mqtt_integration') or not current_app.mqtt_integration:
                current_app.mqtt_integration = MQTTIntegration(
                    mqtt_settings,
                    current_app.config['UPLOAD_FOLDER'],
                    PhotoFrame,
                    db,
                    PlaylistEntry,
                    current_app._get_current_object(),
                    CustomPlaylist,
                )
            else:
                current_app.mqtt_integration.stop()
                current_app.mqtt_integration = MQTTIntegration(
                    mqtt_settings,
                    current_app.config['UPLOAD_FOLDER'],
                    PhotoFrame,
                    db,
                    PlaylistEntry,
                    current_app._get_current_object(),
                    CustomPlaylist,
                )
        else:
            if hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration:
                current_app.mqtt_integration.stop()
                current_app.mqtt_integration = None

        return jsonify({
            'success': True,
            'status': current_app.mqtt_integration.status if (
                hasattr(current_app, 'mqtt_integration') and current_app.mqtt_integration
            ) else "Disabled",
        })
    except Exception as e:
        logger.error(f"Error updating MQTT settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@integrations_bp.route('/api/integrations/mqtt/test', methods=['POST'])
def test_mqtt():
    from model import db, PhotoFrame, PlaylistEntry, CustomPlaylist
    from integrations.mqtt import MQTTIntegration

    try:
        data = request.json
        test_settings = {
            'broker': data.get('broker'),
            'port': data.get('port', 1883),
            'username': data.get('username'),
            'password': data.get('password'),
        }

        test_integration = MQTTIntegration(
            test_settings,
            current_app.config['UPLOAD_FOLDER'],
            PhotoFrame,
            db,
            PlaylistEntry,
            current_app._get_current_object(),
            CustomPlaylist,
        )
        result = test_integration.test_connection()
        test_integration.stop()

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error testing MQTT connection: {e}")
        return jsonify({
            'success': False,
            'message': f"Test failed: {str(e)}",
            'status': "Test failed",
        }), 500


# ------------------------------------------------------------------------------
# Weather routes
# ------------------------------------------------------------------------------

@integrations_bp.route('/api/weather/settings', methods=['GET'])
def get_weather_settings():
    from integrations.overlays.weather import WeatherIntegration

    try:
        basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        weather_config_path = os.path.join(
            basedir, 'integrations', 'overlays', 'weather_config.json'
        )
        weather_integration = WeatherIntegration(weather_config_path)
        settings = weather_integration.settings

        return jsonify({'success': True, 'settings': settings})
    except Exception as e:
        logger.error(f"Error getting weather settings: {e}")
        return jsonify({'success': False, 'error': str(e)})


@integrations_bp.route('/api/weather/settings', methods=['POST'])
def update_weather_settings():
    from integrations.overlays.weather import WeatherIntegration

    try:
        settings = request.get_json()
        if not settings:
            return jsonify({'success': False, 'error': 'No settings provided'})

        basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        weather_config_path = os.path.join(
            basedir, 'integrations', 'overlays', 'weather_config.json'
        )
        weather_integration = WeatherIntegration(weather_config_path)
        success = weather_integration.save_settings(settings)

        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to save settings'})

    except Exception as e:
        logger.error(f"Error updating weather settings: {e}")
        return jsonify({'success': False, 'error': str(e)})


@integrations_bp.route('/api/weather/test', methods=['POST'])
def test_weather():
    try:
        result = current_app.weather_integration.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ------------------------------------------------------------------------------
# Network locations routes
# ------------------------------------------------------------------------------

_NETWORK_LOCATIONS_FILE = None

def _get_network_locations_path():
    global _NETWORK_LOCATIONS_FILE
    if _NETWORK_LOCATIONS_FILE is None:
        basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        config_dir = os.environ.get('CONFIG_PATH', os.path.join(basedir, 'config'))
        _NETWORK_LOCATIONS_FILE = os.path.join(config_dir, 'network_locations.json')
    return _NETWORK_LOCATIONS_FILE

def _load_network_locations():
    import json
    path = _get_network_locations_path()
    try:
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading network locations: {e}")
    return {'locations': []}

def _save_network_locations(data):
    import json
    path = _get_network_locations_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=4)
        return True
    except Exception as e:
        logger.error(f"Error saving network locations: {e}")
        return False


@integrations_bp.route('/api/network/locations', methods=['GET'])
def get_network_locations():
    try:
        data = _load_network_locations()
        return jsonify({'success': True, 'locations': data.get('locations', [])})
    except Exception as e:
        logger.error(f"Error getting network locations: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@integrations_bp.route('/api/network/locations', methods=['POST'])
def add_network_location():
    import json, uuid
    try:
        location = request.get_json()
        if not location or not location.get('path'):
            return jsonify({'success': False, 'error': 'Path is required'}), 400

        data = _load_network_locations()
        location['id'] = uuid.uuid4().hex[:8]
        data['locations'].append(location)
        _save_network_locations(data)

        return jsonify({'success': True, 'location': location})
    except Exception as e:
        logger.error(f"Error adding network location: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@integrations_bp.route('/api/network/locations/<location_id>', methods=['PUT'])
def update_network_location(location_id):
    try:
        updates = request.get_json()
        data = _load_network_locations()
        for loc in data['locations']:
            if loc.get('id') == location_id:
                loc.update(updates)
                loc['id'] = location_id
                _save_network_locations(data)
                return jsonify({'success': True, 'location': loc})
        return jsonify({'success': False, 'error': 'Location not found'}), 404
    except Exception as e:
        logger.error(f"Error updating network location: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@integrations_bp.route('/api/network/locations/<location_id>', methods=['DELETE'])
def delete_network_location(location_id):
    try:
        data = _load_network_locations()
        data['locations'] = [l for l in data['locations'] if l.get('id') != location_id]
        _save_network_locations(data)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error deleting network location: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
