import logging
import sys
from logging.handlers import RotatingFileHandler
import os

def setup_logger():
    # Create logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.makedirs('logs')

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            # Console handler with color formatting
            logging.StreamHandler(sys.stdout),
            # File handler
            RotatingFileHandler(
                'logs/server.log',
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