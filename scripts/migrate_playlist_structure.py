#!/usr/bin/env python3
"""
Migration script for playlist-frame architecture refactor.

This script migrates from the old dual-playlist model (where PlaylistEntry had both
frame_id and custom_playlist_id) to the new model where:
- Playlists are the central entity
- Frames reference a playlist via playlist_id
- PlaylistEntry only references playlist_id

Run this script AFTER updating model.py but BEFORE restarting the server.
"""

import os
import sys
import logging
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import Flask
from sqlalchemy import text, inspect

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_app():
    """Create Flask app instance for database operations."""
    app = Flask(__name__)
    basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'app.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    return app


def column_exists(conn, table_name, column_name):
    """Check if a column exists in a table."""
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    columns = [row[1] for row in result.fetchall()]
    return column_name in columns


def table_exists(conn, table_name):
    """Check if a table exists."""
    result = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=:name"
    ), {"name": table_name})
    return result.fetchone() is not None


def migrate_db():
    """Run the playlist structure migration."""
    app = get_app()
    
    from model import db, init_db
    init_db(app)
    
    with app.app_context():
        with db.engine.connect() as conn:
            logger.info("Starting playlist structure migration...")
            
            # Step 1: Check if we need to rename custom_playlist to playlist
            custom_playlist_exists = table_exists(conn, 'custom_playlist')
            playlist_exists = table_exists(conn, 'playlist')
            
            if custom_playlist_exists and not playlist_exists:
                logger.info("Renaming custom_playlist table to playlist...")
                conn.execute(text("ALTER TABLE custom_playlist RENAME TO playlist"))
                conn.commit()
                logger.info("Renamed custom_playlist to playlist")
            elif not playlist_exists:
                logger.info("Creating playlist table...")
                conn.execute(text('''
                    CREATE TABLE playlist (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name VARCHAR(256) NOT NULL UNIQUE,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                '''))
                conn.commit()
                logger.info("Created playlist table")
            
            # Step 2: Add playlist_id to photo_frame if it doesn't exist
            if not column_exists(conn, 'photo_frame', 'playlist_id'):
                logger.info("Adding playlist_id column to photo_frame...")
                conn.execute(text(
                    "ALTER TABLE photo_frame ADD COLUMN playlist_id INTEGER REFERENCES playlist(id)"
                ))
                conn.commit()
                logger.info("Added playlist_id to photo_frame")
            
            # Step 3: Add playlist_id to playlist_entry if it doesn't exist
            if not column_exists(conn, 'playlist_entry', 'playlist_id'):
                logger.info("Adding playlist_id column to playlist_entry...")
                conn.execute(text(
                    "ALTER TABLE playlist_entry ADD COLUMN playlist_id INTEGER REFERENCES playlist(id)"
                ))
                conn.commit()
                logger.info("Added playlist_id to playlist_entry")
            
            # Step 4: Migrate custom_playlist_id references to playlist_id in playlist_entry
            if column_exists(conn, 'playlist_entry', 'custom_playlist_id'):
                logger.info("Migrating custom_playlist_id to playlist_id in playlist_entry...")
                conn.execute(text('''
                    UPDATE playlist_entry 
                    SET playlist_id = custom_playlist_id 
                    WHERE custom_playlist_id IS NOT NULL AND playlist_id IS NULL
                '''))
                conn.commit()
                logger.info("Migrated custom_playlist_id references")
            
            # Step 5: Migrate frame playlists to the new structure
            # For each frame that has entries with frame_id set, create a playlist
            if column_exists(conn, 'playlist_entry', 'frame_id'):
                logger.info("Migrating frame-based playlists...")
                
                # Get all frames that have playlist entries with frame_id set
                result = conn.execute(text('''
                    SELECT DISTINCT pf.id, pf.name 
                    FROM photo_frame pf
                    INNER JOIN playlist_entry pe ON pe.frame_id = pf.id
                    WHERE pf.playlist_id IS NULL
                '''))
                frames_with_entries = result.fetchall()
                
                for frame_id, frame_name in frames_with_entries:
                    playlist_name = f"{frame_name or frame_id} Playlist"
                    
                    # Check if playlist with this name already exists
                    existing = conn.execute(text(
                        "SELECT id FROM playlist WHERE name = :name"
                    ), {"name": playlist_name}).fetchone()
                    
                    if existing:
                        playlist_id = existing[0]
                        logger.info(f"Using existing playlist '{playlist_name}' (id={playlist_id}) for frame {frame_id}")
                    else:
                        # Create new playlist for this frame
                        conn.execute(text('''
                            INSERT INTO playlist (name, created_at, updated_at)
                            VALUES (:name, :now, :now)
                        '''), {"name": playlist_name, "now": datetime.utcnow()})
                        conn.commit()
                        
                        # Get the new playlist ID
                        result = conn.execute(text(
                            "SELECT id FROM playlist WHERE name = :name"
                        ), {"name": playlist_name})
                        playlist_id = result.fetchone()[0]
                        logger.info(f"Created playlist '{playlist_name}' (id={playlist_id}) for frame {frame_id}")
                    
                    # Update frame to reference this playlist
                    conn.execute(text('''
                        UPDATE photo_frame SET playlist_id = :playlist_id WHERE id = :frame_id
                    '''), {"playlist_id": playlist_id, "frame_id": frame_id})
                    
                    # Update playlist entries to reference the playlist instead of frame
                    conn.execute(text('''
                        UPDATE playlist_entry 
                        SET playlist_id = :playlist_id, frame_id = NULL
                        WHERE frame_id = :frame_id
                    '''), {"playlist_id": playlist_id, "frame_id": frame_id})
                    
                    conn.commit()
                    logger.info(f"Migrated playlist entries for frame {frame_id}")
                
                # Step 6: Create empty playlists for frames without entries
                logger.info("Creating playlists for frames without entries...")
                result = conn.execute(text('''
                    SELECT id, name FROM photo_frame WHERE playlist_id IS NULL
                '''))
                frames_without_playlist = result.fetchall()
                
                for frame_id, frame_name in frames_without_playlist:
                    playlist_name = f"{frame_name or frame_id} Playlist"
                    
                    # Handle duplicate names by adding suffix
                    base_name = playlist_name
                    counter = 1
                    while True:
                        existing = conn.execute(text(
                            "SELECT id FROM playlist WHERE name = :name"
                        ), {"name": playlist_name}).fetchone()
                        if not existing:
                            break
                        counter += 1
                        playlist_name = f"{base_name} ({counter})"
                    
                    # Create playlist
                    conn.execute(text('''
                        INSERT INTO playlist (name, created_at, updated_at)
                        VALUES (:name, :now, :now)
                    '''), {"name": playlist_name, "now": datetime.utcnow()})
                    conn.commit()
                    
                    # Get playlist ID
                    result = conn.execute(text(
                        "SELECT id FROM playlist WHERE name = :name"
                    ), {"name": playlist_name})
                    playlist_id = result.fetchone()[0]
                    
                    # Update frame
                    conn.execute(text('''
                        UPDATE photo_frame SET playlist_id = :playlist_id WHERE id = :frame_id
                    '''), {"playlist_id": playlist_id, "frame_id": frame_id})
                    conn.commit()
                    
                    logger.info(f"Created empty playlist '{playlist_name}' for frame {frame_id}")
            
            logger.info("Migration completed successfully!")
            
            # Print summary
            result = conn.execute(text("SELECT COUNT(*) FROM playlist"))
            playlist_count = result.fetchone()[0]
            
            result = conn.execute(text("SELECT COUNT(*) FROM photo_frame"))
            frame_count = result.fetchone()[0]
            
            result = conn.execute(text("SELECT COUNT(*) FROM photo_frame WHERE playlist_id IS NOT NULL"))
            frames_with_playlist = result.fetchone()[0]
            
            logger.info(f"Summary: {playlist_count} playlists, {frame_count} frames, {frames_with_playlist} frames with playlists")


def verify_migration():
    """Verify the migration was successful."""
    app = get_app()
    
    from model import db, init_db
    init_db(app)
    
    with app.app_context():
        with db.engine.connect() as conn:
            logger.info("Verifying migration...")
            
            # Check all frames have playlists
            result = conn.execute(text('''
                SELECT id FROM photo_frame WHERE playlist_id IS NULL
            '''))
            frames_without_playlist = result.fetchall()
            
            if frames_without_playlist:
                logger.warning(f"Found {len(frames_without_playlist)} frames without playlists!")
                for (frame_id,) in frames_without_playlist:
                    logger.warning(f"  - Frame: {frame_id}")
                return False
            
            # Check no playlist entries still reference frame_id
            if column_exists(conn, 'playlist_entry', 'frame_id'):
                result = conn.execute(text('''
                    SELECT COUNT(*) FROM playlist_entry WHERE frame_id IS NOT NULL
                '''))
                entries_with_frame_id = result.fetchone()[0]
                
                if entries_with_frame_id > 0:
                    logger.warning(f"Found {entries_with_frame_id} playlist entries still referencing frame_id!")
                    return False
            
            logger.info("Migration verification passed!")
            return True


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate playlist structure')
    parser.add_argument('--verify', action='store_true', help='Only verify migration without making changes')
    args = parser.parse_args()
    
    if args.verify:
        success = verify_migration()
        sys.exit(0 if success else 1)
    else:
        migrate_db()
        verify_migration()
