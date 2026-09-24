import logging
import sys
from logging.handlers import RotatingFileHandler
import os

def get_log_file_path():
    # Use LOG_PATH environment variable for Docker volume mounting
    # Falls back to local 'logs' directory when not running in Docker
    return os.path.join(os.environ.get('LOG_PATH', 'logs'), 'server.log')


def setup_logger():
    log_file = get_log_file_path()
    log_dir = os.path.dirname(log_file)

    # Create logs directory if it doesn't exist
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

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

    # Silence chatty third-party loggers
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Logging setup completed")
    
    return logger 