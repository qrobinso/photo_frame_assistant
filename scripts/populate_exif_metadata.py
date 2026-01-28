import os
import sys
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS
import piexif
import json

# Add the parent directory to the Python path so we can import from server.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import app, db, Photo, extract_exif_metadata

def populate_exif_metadata():
    with app.app_context():
        # Get all photos from the database
        photos = Photo.query.all()
        total_photos = len(photos)
        print(f"Found {total_photos} photos to process")

        for i, photo in enumerate(photos, 1):
            try:
                # Get the full path to the photo
                photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
                
                if not os.path.exists(photo_path):
                    print(f"Warning: Photo file not found: {photo_path}")
                    continue

                # Extract EXIF metadata using the existing function
                exif_data = extract_exif_metadata(photo_path)
                
                # Update the photo record
                photo.exif_metadata = exif_data
                
                # Commit after each photo to avoid losing progress if there's an error
                db.session.commit()
                
                print(f"Processed {i}/{total_photos}: {photo.filename}")
                
            except Exception as e:
                print(f"Error processing {photo.filename}: {str(e)}")
                continue

        print("Finished processing all photos")

if __name__ == '__main__':
    populate_exif_metadata() 