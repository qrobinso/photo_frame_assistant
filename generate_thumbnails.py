import os
from PIL import Image
from server import app, db, Photo
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generate_thumbnails():
    """Generate thumbnails for all existing photos that don't have them."""
    with app.app_context():
        # Get upload and thumbnail directories
        upload_folder = app.config['UPLOAD_FOLDER']
        thumbnails_dir = os.path.join(upload_folder, 'thumbnails')
        
        # Create thumbnails directory if it doesn't exist
        os.makedirs(thumbnails_dir, exist_ok=True)
        
        # Get all photos from database
        photos = Photo.query.all()
        total_photos = len(photos)
        logger.info(f"Found {total_photos} photos in database")
        
        success_count = 0
        error_count = 0
        skipped_count = 0
        
        for i, photo in enumerate(photos, 1):
            logger.info(f"Processing photo {i}/{total_photos}: {photo.filename}")
            
            # Skip if photo already has a thumbnail
            if photo.thumbnail and os.path.exists(os.path.join(thumbnails_dir, photo.thumbnail)):
                logger.info(f"Skipping {photo.filename} - thumbnail already exists")
                skipped_count += 1
                continue
            
            # Check if original photo exists
            original_path = os.path.join(upload_folder, photo.filename)
            if not os.path.exists(original_path):
                logger.error(f"Original file not found for {photo.filename}")
                error_count += 1
                continue
            
            try:
                # Generate thumbnail
                with Image.open(original_path) as img:
                    # Create thumbnail
                    img.thumbnail((400, 400))  # Max size 200x200
                    thumb_filename = f"thumb_{photo.filename}"
                    thumb_path = os.path.join(thumbnails_dir, thumb_filename)
                    
                    # Save thumbnail
                    img.save(thumb_path, "JPEG")
                    
                    # Update database record
                    photo.thumbnail = thumb_filename
                    db.session.commit()
                    
                    success_count += 1
                    logger.info(f"Successfully created thumbnail for {photo.filename}")
                    
            except Exception as e:
                error_count += 1
                logger.error(f"Error processing {photo.filename}: {str(e)}")
                db.session.rollback()
        
        # Print summary
        logger.info("\nThumbnail Generation Summary:")
        logger.info(f"Total photos processed: {total_photos}")
        logger.info(f"Successfully generated: {success_count}")
        logger.info(f"Skipped (already exist): {skipped_count}")
        logger.info(f"Errors: {error_count}")

if __name__ == '__main__':
    generate_thumbnails() 