import os
import json
import importlib
import logging
import sys
from typing import Dict, List, Type, Any

logger = logging.getLogger(__name__)

class IntegrationRegistry:
    """Registry for all photo server integrations."""
    
    def __init__(self, app, db):
        self.app = app
        self.db = db
        self.integrations = {}
        self.integration_types = {}
        
    def register_integration_type(self, integration_class):
        """Register an integration type."""
        self.integration_types[integration_class.__name__] = integration_class
        
    def register_integration(self, name, integration):
        """Register an integration instance."""
        self.integrations[name] = integration
        
    def load_integrations(self):
        """Load all registered integrations."""
        # This would typically load from database or config files
        pass
        
    def get_integration(self, name):
        """Get an integration by name."""
        return self.integrations.get(name)
        
    def get_all_integrations(self):
        """Get all loaded integrations."""
        return self.integrations
        
    def initialize_all(self):
        """Initialize all registered integrations."""
        for name, integration in self.integrations.items():
            try:
                integration.initialize()
                logger.info(f"Initialized integration: {name}")
            except Exception as e:
                logger.error(f"Failed to initialize integration {name}: {e}", exc_info=True)
                
    def shutdown_all(self):
        """Shutdown all registered integrations."""
        for name, integration in self.integrations.items():
            try:
                integration.shutdown()
                logger.info(f"Shut down integration: {name}")
            except Exception as e:
                logger.error(f"Failed to shutdown integration {name}: {e}", exc_info=True)


class PluginManager:
    """Manager for discovering and loading plugins."""
    
    def __init__(self, app, db, plugins_dir):
        self.app = app
        self.db = db
        self.plugins_dir = plugins_dir
        self.registry = IntegrationRegistry(app, db)
        
    def discover_plugins(self):
        """Discover all available plugins."""
        plugins = []
        
        # Ensure the plugins directory exists
        if not os.path.exists(self.plugins_dir):
            logger.warning(f"Plugins directory {self.plugins_dir} does not exist")
            return plugins
            
        for item in os.listdir(self.plugins_dir):
            plugin_dir = os.path.join(self.plugins_dir, item)
            manifest_path = os.path.join(plugin_dir, 'manifest.json')
            
            # Skip if not a directory or doesn't have a manifest
            if not os.path.isdir(plugin_dir) or not os.path.exists(manifest_path):
                continue
                
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                    plugins.append({
                        'dir': plugin_dir,
                        'manifest': manifest
                    })
                    logger.info(f"Discovered plugin: {manifest.get('name', 'unknown')}")
            except Exception as e:
                logger.error(f"Failed to load manifest from {manifest_path}: {e}")
                
        return plugins
        
    def load_plugin(self, plugin_info):
        """Load a plugin based on its manifest."""
        try:
            manifest = plugin_info['manifest']
            plugin_dir = plugin_info['dir']
            plugin_name = manifest['name']
            
            # Add the plugin directory to the Python path if needed
            plugin_parent_dir = os.path.dirname(plugin_dir)
            if plugin_parent_dir not in sys.path:
                sys.path.append(plugin_parent_dir)
            
            # Import the module
            module_name = os.path.basename(plugin_dir)
            module_path = f"{module_name}.{module_name}_integration"
            
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                # Try with full path
                module_path = f"integrations.{module_name}.{module_name}_integration"
                module = importlib.import_module(module_path)
            
            # Get the integration class
            integration_class = getattr(module, manifest['main_class'])
            
            # Register the integration type
            self.registry.register_integration_type(integration_class)
            
            # Create an instance of the integration
            config_path = os.path.join(plugin_dir, f"{module_name}_config.json")
            integration = integration_class(config_path)
            
            # Register the integration instance
            self.registry.register_integration(plugin_name, integration)
            
            logger.info(f"Loaded plugin: {manifest['name']} v{manifest['version']}")
            
            return True
        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_info.get('manifest', {}).get('name', 'unknown')}: {e}", exc_info=True)
            return False
            
    def load_all_plugins(self):
        """Discover and load all plugins."""
        plugins = self.discover_plugins()
        for plugin in plugins:
            self.load_plugin(plugin)
        
        # Initialize all integrations
        self.registry.initialize_all()
        
        return self.registry 