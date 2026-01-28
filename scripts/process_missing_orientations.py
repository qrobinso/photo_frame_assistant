#!/usr/bin/env python3
import os
import sys
import logging
from datetime import datetime

# Add parent directory to path so we can import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import app, db, Photo
from photo_processing import PhotoProcessor
from logger_config import setup_logger

# Set up logging
logger = setup_logger()

def process_photos():
    """
    Process all photos in the database to ensure both portrait and landscape versions exist.
    """
    logger.info("Starting to process photos for missing orientations")
    
    # Initialize the photo processor
    photo_processor = PhotoProcessor()
    
    # Get all photos from the database
    photos = Photo.query.all()
    logger.info(f"Found {len(photos)} photos in the database")
    
    processed_count = 0
    skipped_count = 0
    error_count = 0
    
    for photo in photos:
        try:
            # Skip videos
            if photo.media_type == 'video':
                logger.info(f"Skipping video: {photo.filename}")
                skipped_count += 1
                continue
                
            # Get the full path to the original image
            original_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
            
            # Check if the original file exists
            if not os.path.exists(original_path):
                logger.warning(f"Original file not found: {original_path}")
                skipped_count += 1
                continue
            
            # Check if portrait version is missing
            if not photo.portrait_version:
                logger.info(f"Processing portrait version for: {photo.filename}")
                portrait_path = photo_processor.process_for_orientation(original_path, 'portrait')
                if portrait_path:
                    # Update the database record
                    photo.portrait_version = os.path.basename(portrait_path)
                    db.session.commit()
                    logger.info(f"Added portrait version: {photo.portrait_version}")
                    processed_count += 1
                else:
                    logger.error(f"Failed to create portrait version for: {photo.filename}")
                    error_count += 1
            
            # Check if landscape version is missing
            if not photo.landscape_version:
                logger.info(f"Processing landscape version for: {photo.filename}")
                landscape_path = photo_processor.process_for_orientation(original_path, 'landscape')
                if landscape_path:
                    # Update the database record
                    photo.landscape_version = os.path.basename(landscape_path)
                    db.session.commit()
                    logger.info(f"Added landscape version: {photo.landscape_version}")
                    processed_count += 1
                else:
                    logger.error(f"Failed to create landscape version for: {photo.filename}")
                    error_count += 1
                    
        except Exception as e:
            logger.error(f"Error processing photo {photo.id} ({photo.filename}): {str(e)}")
            error_count += 1
    
    logger.info(f"Processing complete. Processed: {processed_count}, Skipped: {skipped_count}, Errors: {error_count}")
    return processed_count, skipped_count, error_count

if __name__ == "__main__":
    with app.app_context():
        start_time = datetime.now()
        logger.info(f"Script started at: {start_time}")
        
        processed, skipped, errors = process_photos()
        
        end_time = datetime.now()
        duration = end_time - start_time
        logger.info(f"Script completed at: {end_time}")
        logger.info(f"Total duration: {duration}")
        logger.info(f"Summary: Processed: {processed}, Skipped: {skipped}, Errors: {errors}") 