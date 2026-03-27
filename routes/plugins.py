"""
Plugin system Blueprint — all /plugins and /api/plugins/* routes.

"""

import os
import subprocess
import sys
import threading
from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request

from plugins.crypto import SENTINEL, decrypt_secrets, encrypt_secrets, mask_for_api

plugin_bp = Blueprint('plugins', __name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _runner():
    return current_app.plugin_runner


def _schema_for(plugin_id: str) -> dict:
    cls = _runner().get_plugin_class(plugin_id)
    try:
        return cls().config_schema if cls else {}
    except Exception:
        return {}


def _instance_to_dict(inst):
    thumb_url = None
    photo_url = None
    if inst.photo:
        ts = int(inst.last_run_at.timestamp()) if inst.last_run_at else 0
        if inst.photo.thumbnail:
            thumb_url = f'/photos/thumbnails/{inst.photo.thumbnail}?t={ts}'
        if inst.photo.filename:
            photo_url = f'/photos/{inst.photo.filename}?t={ts}'

    # Mask any encrypted secret values before sending to the browser
    safe_config = mask_for_api(inst.config or {})

    return {
        'id':           inst.id,
        'plugin_id':    inst.plugin_id,
        'name':         inst.name,
        'cron':         inst.cron,
        'enabled':      inst.enabled,
        'photo_id':     inst.photo_id,
        'config':       safe_config,
        'thumbnail_url': thumb_url,
        'photo_url':    photo_url,
        'created_at':   inst.created_at.isoformat() if inst.created_at else None,
        'last_run_at':  inst.last_run_at.isoformat() if inst.last_run_at else None,
        'last_run_ok':  inst.last_run_ok,
        'last_error':   inst.last_error,
    }


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@plugin_bp.route('/plugins')
def plugins_page():
    return render_template('plugins.html')


# ---------------------------------------------------------------------------
# Available plugin classes (catalog)
# ---------------------------------------------------------------------------

@plugin_bp.route('/api/plugins/available')
def list_available_plugins():
    plugins = _runner().list_available_plugins()
    return jsonify(plugins)


@plugin_bp.route('/api/plugins/reload', methods=['POST'])
def reload_plugins():
    """Re-scan the plugins/ directory and register any newly added plugins."""
    runner = _runner()
    runner.discover_and_register()
    return jsonify({'success': True, 'count': len(runner.list_available_plugins())})


# ---------------------------------------------------------------------------
# Plugin instances — CRUD
# ---------------------------------------------------------------------------

@plugin_bp.route('/api/plugins/instances', methods=['GET'])
def list_instances():
    from model import PluginInstance
    instances = PluginInstance.query.order_by(PluginInstance.created_at.desc()).all()
    return jsonify([_instance_to_dict(i) for i in instances])


@plugin_bp.route('/api/plugins/instances', methods=['POST'])
def create_instance():
    from model import db, Photo, PluginInstance

    data = request.get_json() or {}
    plugin_id = data.get('plugin_id', '').strip()
    name      = data.get('name', '').strip()
    config    = data.get('config', {})
    cron      = data.get('cron', '').strip()

    if not plugin_id:
        return jsonify({'error': 'plugin_id is required'}), 400
    if not name:
        return jsonify({'error': 'name is required'}), 400

    runner = _runner()

    if runner.get_plugin_class(plugin_id) is None:
        return jsonify({'error': f"Unknown plugin '{plugin_id}'"}), 400

    # Use the plugin's default_cron if none provided
    if not cron:
        available = {p['plugin_id']: p for p in runner.list_available_plugins()}
        cron = available.get(plugin_id, {}).get('default_cron', '0 * * * *')

    # Create the Photo record with a placeholder image first (id needed for filename)
    placeholder = Photo(
        filename='_placeholder.jpg',
        portrait_version='_placeholder.jpg',
        landscape_version='_placeholder.jpg',
        thumbnail='_placeholder.jpg',
        source='plugin',
        heading=name,
        uploaded_at=datetime.utcnow(),
    )
    db.session.add(placeholder)
    db.session.flush()  # get placeholder.id

    # Encrypt any secret config fields before storing
    schema = _schema_for(plugin_id)
    config = encrypt_secrets(config, schema)

    # Create the PluginInstance record (id needed for stable filename)
    instance = PluginInstance(
        plugin_id=plugin_id,
        name=name,
        config=config,
        cron=cron,
        enabled=True,
        photo_id=placeholder.id,
        created_at=datetime.utcnow(),
    )
    db.session.add(instance)
    db.session.flush()  # get instance.id

    # Now generate the actual placeholder files using stable instance id
    try:
        base_name, portrait_name, landscape_name, thumb_name = runner.create_placeholder_photo(instance.id)
        placeholder.filename = base_name
        placeholder.portrait_version = portrait_name
        placeholder.landscape_version = landscape_name
        placeholder.thumbnail = thumb_name
    except Exception as exc:
        current_app.logger.error(f"Failed to create placeholder image: {exc}", exc_info=True)
        # Non-fatal — instance still created, placeholder filenames remain

    db.session.commit()

    # Schedule the job
    runner._add_job(instance)

    # Run immediately so the placeholder is replaced right away
    runner.trigger_now(instance.id)

    return jsonify(_instance_to_dict(instance)), 201


@plugin_bp.route('/api/plugins/instances/<int:instance_id>', methods=['GET'])
def get_instance(instance_id):
    from model import PluginInstance
    inst = PluginInstance.query.get_or_404(instance_id)
    return jsonify(_instance_to_dict(inst))


@plugin_bp.route('/api/plugins/instances/<int:instance_id>', methods=['PUT'])
def update_instance(instance_id):
    from model import db, PluginInstance

    inst = PluginInstance.query.get_or_404(instance_id)
    data = request.get_json() or {}

    cron_changed = False

    if 'name' in data:
        inst.name = data['name'].strip()
    if 'config' in data:
        new_config = data['config']
        existing   = inst.config or {}
        schema     = _schema_for(inst.plugin_id)
        # Any field sent as SENTINEL means "keep the stored encrypted value"
        for key, val in new_config.items():
            if val == SENTINEL and key in existing:
                new_config[key] = existing[key]
        inst.config = encrypt_secrets(new_config, schema)
    if 'cron' in data and data['cron'].strip():
        new_cron = data['cron'].strip()
        if new_cron != inst.cron:
            inst.cron = new_cron
            cron_changed = True

    db.session.commit()

    if cron_changed and inst.enabled:
        _runner()._add_job(inst)

    return jsonify(_instance_to_dict(inst))


@plugin_bp.route('/api/plugins/instances/<int:instance_id>', methods=['DELETE'])
def delete_instance(instance_id):
    from model import db, PluginInstance, PlaylistEntry

    inst = PluginInstance.query.get_or_404(instance_id)
    runner = _runner()

    # Remove scheduler job
    runner._remove_job(instance_id)

    photo = inst.photo
    upload_folder = current_app.config['UPLOAD_FOLDER']

    # Remove from all playlists
    if photo:
        PlaylistEntry.query.filter_by(photo_id=photo.id).delete()

    db.session.delete(inst)
    db.session.flush()

    # Delete files and Photo record after instance is gone (FK constraint)
    if photo:
        for attr, subfolder in [
            ('filename', ''),
            ('portrait_version', ''),
            ('landscape_version', ''),
            ('thumbnail', 'thumbnails'),
        ]:
            fname = getattr(photo, attr)
            if fname and not fname.startswith('_placeholder'):
                fpath = os.path.join(upload_folder, subfolder, fname) if subfolder else os.path.join(upload_folder, fname)
                try:
                    if os.path.exists(fpath):
                        os.remove(fpath)
                except Exception as exc:
                    current_app.logger.warning(f"Could not delete plugin file {fpath}: {exc}")
        db.session.delete(photo)

    db.session.commit()
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------

@plugin_bp.route('/api/plugins/instances/<int:instance_id>/enable', methods=['POST'])
def enable_instance(instance_id):
    from model import db, PluginInstance
    inst = PluginInstance.query.get_or_404(instance_id)
    inst.enabled = True
    db.session.commit()
    _runner()._add_job(inst)
    return jsonify(_instance_to_dict(inst))


@plugin_bp.route('/api/plugins/instances/<int:instance_id>/disable', methods=['POST'])
def disable_instance(instance_id):
    from model import db, PluginInstance
    inst = PluginInstance.query.get_or_404(instance_id)
    inst.enabled = False
    db.session.commit()
    _runner()._remove_job(instance_id)
    return jsonify(_instance_to_dict(inst))


# ---------------------------------------------------------------------------
# Manual run
# ---------------------------------------------------------------------------

@plugin_bp.route('/api/plugins/instances/<int:instance_id>/run', methods=['POST'])
def run_instance(instance_id):
    from model import PluginInstance
    inst = PluginInstance.query.get_or_404(instance_id)
    _runner().trigger_now(instance_id)
    return jsonify({'success': True, 'message': f"Plugin '{inst.name}' triggered in background."})


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------

@plugin_bp.route('/api/plugins/instances/<int:instance_id>/history')
def instance_history(instance_id):
    from model import PluginInstance, PluginRunLog
    PluginInstance.query.get_or_404(instance_id)  # 404 guard
    logs = (
        PluginRunLog.query
        .filter_by(instance_id=instance_id)
        .order_by(PluginRunLog.ran_at.desc())
        .limit(20)
        .all()
    )
    return jsonify([
        {
            'id':          l.id,
            'ran_at':      l.ran_at.isoformat(),
            'success':     l.success,
            'error':       l.error,
            'duration_ms': l.duration_ms,
        }
        for l in logs
    ])


# ---------------------------------------------------------------------------
# Playwright status + install
# ---------------------------------------------------------------------------

_playwright_install_status = {'running': False, 'output': '', 'success': None}


@plugin_bp.route('/api/plugins/playwright-status')
def playwright_status():
    status = _runner().check_playwright()
    return jsonify(status)


@plugin_bp.route('/api/plugins/install-playwright', methods=['POST'])
def install_playwright():
    global _playwright_install_status
    if _playwright_install_status['running']:
        return jsonify({'success': False, 'error': 'Install already in progress'}), 409

    def _do_install():
        global _playwright_install_status
        _playwright_install_status = {'running': True, 'output': '', 'success': None}
        try:
            # Step 1: download Chromium binary if not already present
            r1 = subprocess.run(
                [sys.executable, '-m', 'playwright', 'install', 'chromium'],
                capture_output=True, text=True, timeout=300
            )
            output = r1.stdout + r1.stderr

            # Step 2: attempt system-level deps install (requires root — may fail)
            r2 = subprocess.run(
                [sys.executable, '-m', 'playwright', 'install-deps', 'chromium'],
                capture_output=True, text=True, timeout=120
            )
            output += '\n' + r2.stdout + r2.stderr

            _playwright_install_status['output'] = output
            # Success if binary install worked (deps may fail without root — handled separately)
            _playwright_install_status['success'] = r1.returncode == 0
            # Invalidate cache
            from plugins.runner import _playwright_cache
            _playwright_cache['result'] = None
        except Exception as exc:
            _playwright_install_status['output'] = str(exc)
            _playwright_install_status['success'] = False
        finally:
            _playwright_install_status['running'] = False

    threading.Thread(target=_do_install, daemon=True).start()
    return jsonify({'success': True, 'message': 'Chromium install started in background.'})


@plugin_bp.route('/api/plugins/install-playwright/status')
def install_playwright_status():
    return jsonify(_playwright_install_status)
