#!/usr/bin/env python3
"""
Database Manager for Photo Server
This script handles database creation and migrations
"""

import os
import sys
import logging
import argparse
import json
import sqlite3
from datetime import datetime
from sqlalchemy import create_engine, inspect, MetaData, Table, Column, Integer, String, DateTime, Float, Boolean, ForeignKey, Text, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import JSON

# Set up logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Get the absolute path of the directory
basedir = os.path.abspath(os.path.dirname(__file__))
# Use DB_PATH environment variable for Docker compatibility
db_path = os.environ.get('DB_PATH', os.path.join(basedir, 'app.db'))
db_backup_dir = os.path.join(os.path.dirname(db_path), 'db_backups')

def backup_database():
    """Create a backup of the database before making changes"""
    if not os.path.exists(db_path):
        logger.info("No database to backup.")
        return
    
    # Create backup directory if it doesn't exist
    if not os.path.exists(db_backup_dir):
        os.makedirs(db_backup_dir)
    
    # Create backup filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(db_backup_dir, f'app_db_backup_{timestamp}.db')
    
    # Copy the database file
    try:
        import shutil
        shutil.copy2(db_path, backup_path)
        logger.info(f"Database backed up to {backup_path}")
    except Exception as e:
        logger.error(f"Failed to backup database: {e}")
        sys.exit(1)

def create_database():
    """Create a new database with all tables"""
    try:
        # Import models from server.py
        from server import app, db, Photo, PhotoFrame, PlaylistEntry, ScheduledGeneration, GenerationHistory, SyncGroup
        
        # Create all tables within application context
        with app.app_context():
            logger.info("Creating database tables...")
            db.create_all()
            
            # Verify tables were created
            engine = create_engine(f'sqlite:///{db_path}')
            tables = inspect(engine).get_table_names()
            logger.info(f"Created tables: {', '.join(tables)}")
            
            logger.info("Database creation complete!")
            return True
    except Exception as e:
        logger.error(f"Error creating database: {e}")
        return False

def get_column_type_sql(column):
    """Convert SQLAlchemy column type to SQLite type string"""
    from sqlalchemy import Integer, String, Float, Boolean, DateTime, Text
    from sqlalchemy.types import JSON
    
    col_type = type(column.type)
    
    if col_type == Integer:
        return "INTEGER"
    elif col_type == String:
        length = getattr(column.type, 'length', None)
        return f"VARCHAR({length})" if length else "VARCHAR(255)"
    elif col_type == Text:
        return "TEXT"
    elif col_type == Float:
        return "FLOAT"
    elif col_type == Boolean:
        return "BOOLEAN"
    elif col_type == DateTime:
        return "DATETIME"
    elif col_type == JSON or col_type.__name__ == 'JSON':
        return "JSON"
    else:
        # Default to TEXT for unknown types
        return "TEXT"

def get_column_default_sql(column):
    """Get the default value clause for a column"""
    if column.default is not None:
        default_value = column.default.arg
        if isinstance(default_value, bool):
            return f"DEFAULT {1 if default_value else 0}"
        elif isinstance(default_value, (int, float)):
            return f"DEFAULT {default_value}"
        elif isinstance(default_value, str):
            return f"DEFAULT '{default_value}'"
    return ""

def migrate_database():
    """Migrate the database schema to match the current models"""
    try:
        # Import models from server.py
        from server import app, db, Photo, PhotoFrame, PlaylistEntry, ScheduledGeneration, GenerationHistory, SyncGroup
        
        # Get the engine and inspector within application context
        with app.app_context():
            # Get the engine and inspector
            engine = db.engine
            inspector = inspect(engine)
            
            # Get existing tables
            existing_tables = inspector.get_table_names()
            logger.info(f"Existing tables: {', '.join(existing_tables)}")
            
            # Get metadata from models
            metadata = db.metadata
            
            # Create missing tables
            missing_tables = set(metadata.tables.keys()) - set(existing_tables)
            if missing_tables:
                logger.info(f"Creating missing tables: {', '.join(missing_tables)}")
                for table_name in missing_tables:
                    metadata.tables[table_name].create(engine)
            
            # Check for missing columns in existing tables and add them
            for table_name in existing_tables:
                if table_name in metadata.tables:
                    # Get existing columns
                    existing_columns = {col['name'] for col in inspector.get_columns(table_name)}
                    
                    # Get model columns as a dict for easy access
                    model_table = metadata.tables[table_name]
                    model_columns = {col.name: col for col in model_table.columns}
                    
                    # Find missing columns
                    missing_column_names = set(model_columns.keys()) - existing_columns
                    if missing_column_names:
                        logger.info(f"Table '{table_name}' is missing columns: {', '.join(missing_column_names)}")
                        
                        # Add each missing column
                        with engine.connect() as conn:
                            for col_name in missing_column_names:
                                column = model_columns[col_name]
                                col_type = get_column_type_sql(column)
                                col_default = get_column_default_sql(column)
                                
                                sql = f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type} {col_default}".strip()
                                try:
                                    conn.execute(text(sql))
                                    conn.commit()
                                    logger.info(f"  Added column '{col_name}' to table '{table_name}'")
                                except Exception as e:
                                    # Column might already exist (race condition) or other issue
                                    logger.warning(f"  Could not add column '{col_name}': {e}")
            
            logger.info("Database migration complete!")
            return True
    except Exception as e:
        logger.error(f"Error during database migration: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Photo Server Database Manager')
    parser.add_argument('--create', action='store_true', help='Create a new database')
    parser.add_argument('--migrate', action='store_true', help='Migrate the database schema')
    parser.add_argument('--backup', action='store_true', help='Backup the database')
    parser.add_argument('--force', action='store_true', help='Force operation even if database exists')
    
    args = parser.parse_args()
    
    # Default to create if database doesn't exist
    if not os.path.exists(db_path) and not (args.create or args.migrate or args.backup):
        args.create = True
    
    # Backup database before any operations
    if args.backup or args.migrate:
        backup_database()
    
    # Create database
    if args.create:
        if os.path.exists(db_path) and not args.force:
            logger.error(f"Database already exists at {db_path}. Use --force to overwrite.")
            return 1
        
        if os.path.exists(db_path) and args.force:
            logger.warning(f"Removing existing database at {db_path}")
            os.remove(db_path)
        
        success = create_database()
        return 0 if success else 1
    
    # Migrate database
    if args.migrate:
        if not os.path.exists(db_path):
            logger.error(f"Database does not exist at {db_path}. Use --create instead.")
            return 1
        
        success = migrate_database()
        return 0 if success else 1
    
    # If no operation specified and database exists, show help
    if os.path.exists(db_path) and not (args.create or args.migrate or args.backup):
        parser.print_help()
    
    return 0

if __name__ == "__main__":
    sys.exit(main()) 