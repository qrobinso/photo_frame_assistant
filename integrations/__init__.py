"""
Photo Server Integrations Package

This package contains the plugin framework and integrations for the Photo Server.
"""

from .base import Integration, PhotoSourceIntegration, SmartHomeIntegration, OverlayIntegration
from .plugin_manager import PluginManager, IntegrationRegistry

__all__ = [
    'Integration',
    'PhotoSourceIntegration',
    'SmartHomeIntegration',
    'OverlayIntegration',
    'PluginManager',
    'IntegrationRegistry'
] 