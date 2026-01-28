#!/usr/bin/env python3
"""
Database generation script for Photo Server
This script creates the database tables based on the models defined in server.py
"""

import os
import sys
import logging
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Set up logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Get the absolute path of the directory
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'app.db')

# Check if database already exists
if os.path.exists(db_path):
    logger.info(f"Database already exists at {db_path}")
    sys.exit(0)

logger.info("Generating database...")

try:
    # Import Flask app and models from server.py
    from server import app, db, Photo, PhotoFrame, PlaylistEntry, ScheduledGeneration, GenerationHistory, SyncGroup
    
    # Create all tables within application context
    with app.app_context():
        logger.info("Creating database tables...")
        db.create_all()
        
        # Verify tables were created
        engine = create_engine(f'sqlite:///{db_path}')
        tables = engine.table_names()
        logger.info(f"Created tables: {', '.join(tables)}")
        
        logger.info("Database generation complete!")
except Exception as e:
    logger.error(f"Error generating database: {e}")
    sys.exit(1) 