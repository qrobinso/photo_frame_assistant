import logging
import sys
from logging.handlers import RotatingFileHandler
import os

def setup_logger():
    # Use LOG_PATH environment variable for Docker volume mounting
    # Falls back to local 'logs' directory when not running in Docker
    log_dir = os.environ.get('LOG_PATH', 'logs')
    
    # Create logs directory if it doesn't exist
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, 'server.log')

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            # Console handler with color formatting
            logging.StreamHandler(sys.stdout),
            # File handler
            RotatingFileHandler(
                log_file,
                maxBytes=1024 * 1024,  # 1MB
                backupCount=5
            )
        ]
    )

    # Set Flask's logger level
    logging.getLogger('werkzeug').setLevel(logging.INFO)

    # Create a logger instance
    logger = logging.getLogger(__name__)
    logger.info("Logging setup completed")
    
    return logger 