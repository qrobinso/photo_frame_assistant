from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import JSON, text
import os

# Initialize Flask app
app = Flask(__name__)

# Configure database (use DB_PATH environment variable for Docker compatibility)
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.environ.get('DB_PATH', os.path.join(basedir, 'app.db'))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize SQLAlchemy
db = SQLAlchemy(app)

def migrate_db():
    """Run database migrations."""
    with app.app_context():
        with db.engine.connect() as conn:
            # Create the event_log table using SQL
            conn.execute(text('''
                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    frame_id VARCHAR(50) NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    source VARCHAR(50) NOT NULL,
                    details JSON,
                    FOREIGN KEY (frame_id) REFERENCES photo_frame(id)
                )
            '''))
            conn.commit()
            print("Added event_log table to database")
            
            # Add snap_to_hour column to photo_frame table
            try:
                conn.execute(text('ALTER TABLE photo_frame ADD COLUMN snap_to_hour BOOLEAN DEFAULT 0'))
                conn.commit()
                print("Added snap_to_hour column to photo_frame table")
            except Exception:
                pass  # Column already exists

if __name__ == '__main__':
    migrate_db()