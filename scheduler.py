import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import current_app
import os
import json
import time  

logger = logging.getLogger(__name__)

class GenerationScheduler:
    def __init__(self, app, photo_generator, db, models):
        """Initialize scheduler with app, photo generator and database models."""
        self.app = app
        self.photo_generator = photo_generator
        self.db = db
        self.Photo = models['Photo']
        self.ScheduledGeneration = models['ScheduledGeneration']
        self.GenerationHistory = models['GenerationHistory']
        self.PlaylistEntry = models['PlaylistEntry']
        self.CustomPlaylist = models['CustomPlaylist']
        
        self.scheduler = BackgroundScheduler()
        self.scheduler.start()   
        
        # Initialize last run time tracking
        self.last_immich_run_time = None
        self.last_network_run_time = None
        
        # Calculate a staggered start time for each job (30 seconds after startup)
        network_start_time = datetime.now() + timedelta(seconds=30)
        immich_start_time = datetime.now() + timedelta(seconds=60)  # 1 minute after startup
        
        # Add the auto-import job that runs every hour
        self.scheduler.add_job(
            func=self.check_network_locations_for_new_media,
            trigger='interval',
            minutes=60,
            id='auto_import_media',
            next_run_time=network_start_time  # Run 30 seconds after startup
        )
        logger.info(f"Scheduled auto-import media job to run at {network_start_time} and then every 60 minutes")
        
        # Add the Immich auto-import job that runs every hour
        self.scheduler.add_job(
            func=self.check_immich_for_new_media,
            trigger='interval',
            minutes=60,
            id='immich_auto_import',
            next_run_time=immich_start_time  # Run 1 minute after startup
        )
        logger.info(f"Scheduled Immich auto-import job to run at {immich_start_time} and then every 60 minutes")
        
        # Load and schedule all active generations
        self.load_scheduled_generations()

    def get_api_settings(self):
        """Get API settings from photogen_settings.json."""
        try:
            settings_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "/config/photogen_settings.json")
            with open(settings_path, 'r') as f:
                settings = json.load(f)
            return settings
        except Exception as e:
            logging.error(f"Error loading photogen settings: {e}")
            return {}

    def execute_generation(self, schedule_id):
        """Execute a scheduled generation or playlist application."""
        with self.app.app_context():
            try:
                schedule = self.ScheduledGeneration.query.get(schedule_id)
                if not schedule:
                    logging.error(f"Schedule {schedule_id} not found")
                    return

                # Handle custom playlist schedules
                if schedule.service == 'custom_playlist':
                    try:
                        playlist_id = int(schedule.model)
                        playlist = self.db.session.query(self.CustomPlaylist).get(playlist_id)
                        if not playlist:
                            raise Exception(f"Playlist {playlist_id} not found")

                        # Get valid entries
                        entries = playlist.entries.order_by(self.PlaylistEntry.order).all()
                        valid_entries = [e for e in entries if e.photo_id and e.photo]
                        
                        if not valid_entries:
                            raise Exception("No valid photos in playlist")

                        # Clear existing frame playlist
                        self.db.session.query(self.PlaylistEntry)\
                            .filter_by(frame_id=schedule.frame_id)\
                            .delete()

                        # Add new entries
                        for order, entry in enumerate(valid_entries, 1):
                            new_entry = self.PlaylistEntry(
                                frame_id=schedule.frame_id,
                                photo_id=entry.photo_id,
                                order=order
                            )
                            self.db.session.add(new_entry)

                        # Record successful generation
                        history = self.GenerationHistory(
                            schedule_id=schedule.id,
                            success=True,
                            photo_id=valid_entries[0].photo_id,
                            name=schedule.name
                        )
                        self.db.session.add(history)
                        self.db.session.commit()

                        logging.info(f"Successfully applied playlist {playlist_id} to frame {schedule.frame_id}")

                    except Exception as e:
                        error_msg = f"Error applying playlist: {str(e)}"
                        logging.error(error_msg)
                        # Record failed generation
                        history = self.GenerationHistory(
                            schedule_id=schedule.id,
                            success=False,
                            error_message=error_msg,
                            name=schedule.name
                        )
                        self.db.session.add(history)
                        self.db.session.commit()
                        raise

                else:
                    # Handle existing image generation services
                    return self.execute_image_generation(schedule)

            except Exception as e:
                logging.error(f"Error in scheduled execution {schedule_id}: {str(e)}")
                raise

    def execute_image_generation(self, schedule):
        """Execute a scheduled image generation."""
        with self.app.app_context():
            try:
                # Get schedule details
                schedule = self.ScheduledGeneration.query.get(schedule.id)
                if not schedule:
                    logging.error(f"Schedule {schedule.id} not found")
                    return

                api_key = None
                base_url = None
                settings = {} # Initialize settings

                # Define config directory path relative to this file's location
                # Assumes scheduler.py is in the root or a subdir, and config/ is at the root level
                config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "config")
                if not os.path.isdir(config_dir):
                     # Fallback if the above path doesn't work (e.g., running from a different structure)
                     # This assumes config is accessible directly relative to where the script might be run from
                     # Or potentially using an absolute path if necessary and known.
                     # For now, let's try finding it relative to the script's directory first.
                     config_dir_alt = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config")
                     if os.path.isdir(config_dir_alt):
                         config_dir = config_dir_alt
                     else:
                         # As a last resort, maybe it's just /config if running in a specific container setup
                         config_dir = "/config" 
                         logger.warning(f"Config directory detection complex, trying absolute path: {config_dir}")


                if schedule.service == 'stability' or schedule.service == 'dalle':
                    # Load general API settings for Stability/Dalle
                    settings = self.get_api_settings() # Uses /config/photogen_settings.json
                    if schedule.service == 'stability':
                        api_key = settings.get('stability_api_key')
                        base_url = settings.get('stability_base_url')
                    else:  # dalle
                        api_key = settings.get('dalle_api_key')
                        base_url = settings.get('dalle_base_url')
                
                elif schedule.service == 'unsplash':
                    # Import UnsplashIntegration class from the correct module
                    from integrations.unsplash_integration import UnsplashIntegration
                    
                    # Get the config file path
                    unsplash_config_path = os.path.join(config_dir, "unsplash_config.json")
                    
                    # Initialize the integration with config path
                    unsplash = UnsplashIntegration(unsplash_config_path)
                    
                    # Validate orientation parameter
                    orientation = schedule.orientation
                    if orientation and orientation not in ['landscape', 'portrait', 'squarish']:
                        logger.warning(f"Invalid orientation value: {orientation}, setting to None")
                        orientation = None
                    
                    # Log all parameters being used
                    logger.info(f"Using query: '{schedule.prompt}', orientation: '{orientation}'")
                    
                    # Get a random photo using the instance method
                    results = unsplash.get_random_photos(
                        query=schedule.prompt,
                        orientation=orientation,
                        count=1
                    )

                    if not results:
                        raise Exception('No photos found from Unsplash')

                    # Get the upload folder from app config
                    upload_folder = current_app.config.get('UPLOAD_FOLDER')
                    if not upload_folder:
                        upload_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "uploads")
                    
                    # Download the photo using the instance method
                    photo_data = results[0]
                    download_result = unsplash.download_photo(photo_data, upload_folder)

                    # Create photo record
                    photo = self.Photo(
                        filename=download_result['filename'],
                        heading=download_result.get('heading', '')
                    )
                    self.db.session.add(photo)
                    self.db.session.flush()
                    
                    # Now photo.id should be available
                    if not photo.id:
                        raise Exception("Failed to create photo record for Unsplash image")

                elif schedule.service == 'pixabay':
                    # Import PixabayIntegration class if available
                    from integrations.pixabay_integration import PixabayIntegration
                    
                    # Get the config file path
                    pixabay_config_path = os.path.join(config_dir, "pixabay_config.json")
                    
                    # Initialize the integration with config path
                    pixabay = PixabayIntegration(pixabay_config_path)
                    
                    # Get a random photo from Pixabay using the instance method
                    results = pixabay.get_random_photos(
                        query=schedule.prompt,
                        orientation=schedule.orientation,
                        count=1
                    )

                    if not results:
                        raise Exception('No photos found from Pixabay')

                    # Get the upload folder from app config
                    upload_folder = current_app.config.get('UPLOAD_FOLDER')
                    if not upload_folder:
                        upload_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "uploads")
                    
                    # Download the photo using the instance method
                    photo_data = results[0]
                    download_result = pixabay.download_photo(photo_data, upload_folder)

                    # Create photo record
                    photo = self.Photo(
                        filename=download_result['filename'],
                        heading=download_result.get('heading', '')
                    )
                    self.db.session.add(photo)
                    self.db.session.flush()
                    
                    # Now photo.id should be available
                    if not photo.id:
                        raise Exception("Failed to create photo record for Pixabay image")
                
                else:
                    # Handle unknown service type if necessary, or let the check below catch it
                    logger.warning(f"Unknown service type encountered: {schedule.service}")


                if not api_key:
                    # Log error and raise exception if API key couldn't be found/configured for the service
                    error_msg = f"No API key could be configured or found for service: {schedule.service}"
                    logger.error(error_msg)
                    raise Exception(error_msg)

                # Handle different services
                if schedule.service in ['stability', 'dalle']:
                    # Use the existing photo_generator.generate_images method for AI services
                    result = self.photo_generator.generate_images(
                        service=schedule.service,
                        model=schedule.model,
                        prompt=schedule.prompt,
                        orientation=schedule.orientation,
                        api_key=api_key,
                        base_url=base_url
                    )

                    if 'error' in result:
                        raise Exception(result['error'])

                    # Use the existing save_to_gallery method
                    filename = self.photo_generator.save_to_gallery(result['images'][0])

                    # Create photo record
                    photo = self.Photo(filename=filename)
                    self.db.session.add(photo)
                    self.db.session.commit()

                elif schedule.service == 'unsplash':
                    # Import unsplash integration module
                    from unsplash_integration import get_random_photos, download_photo
                    
                    # Validate orientation parameter
                    orientation = schedule.orientation
                    if orientation and orientation not in ['landscape', 'portrait', 'squarish']:
                        logger.warning(f"Invalid orientation value: {orientation}, setting to None")
                        orientation = None
                    
                    # Log all parameters being used
                    logger.info(f"Using query: '{schedule.prompt}', orientation: '{orientation}'")
                    
                    # Get a random photo
                    results = get_random_photos(
                        query=schedule.prompt,
                        orientation=orientation,
                        count=1
                    )

                    if not results:
                        raise Exception('No photos found from Unsplash')

                    # Get the upload folder from app config
                    upload_folder = current_app.config.get('UPLOAD_FOLDER')
                    if not upload_folder:
                        upload_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "uploads")
                    
                    # Download the photo
                    photo_data = results[0]
                    download_result = download_photo(photo_data, upload_folder)

                    # Create photo record
                    photo = self.Photo(
                        filename=download_result['filename'],
                        heading=download_result.get('heading', '')
                    )
                    self.db.session.add(photo)
                    self.db.session.flush()
                    
                    # Now photo.id should be available
                    if not photo.id:
                        raise Exception("Failed to create photo record for Unsplash image")

                elif schedule.service == 'pixabay':
                    # Import pixabay integration module if available
                    from pixabay_integration import get_random_photos, download_photo
                    
                    # Get a random photo from Pixabay
                    results = get_random_photos(
                        query=schedule.prompt,
                        orientation=schedule.orientation,
                        count=1
                    )

                    if not results:
                        raise Exception('No photos found from Pixabay')

                    # Get the upload folder from app config
                    upload_folder = current_app.config.get('UPLOAD_FOLDER')
                    if not upload_folder:
                        upload_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "uploads")
                    
                    # Download the photo
                    photo_data = results[0]
                    download_result = download_photo(photo_data, upload_folder)

                    # Create photo record
                    photo = self.Photo(
                        filename=download_result['filename'],
                        heading=download_result.get('heading', '')
                    )
                    self.db.session.add(photo)
                    self.db.session.flush()
                    
                    # Now photo.id should be available
                    if not photo.id:
                        raise Exception("Failed to create photo record for Pixabay image")
                
                else:
                    raise Exception(f"Unsupported service: {schedule.service}")

                # Shift all existing entries up by 1
                self.db.session.query(self.PlaylistEntry)\
                    .filter_by(frame_id=schedule.frame_id)\
                    .update({self.PlaylistEntry.order: self.PlaylistEntry.order + 1})

                # Insert new entry as first in playlist (order = 1)
                playlist_entry = self.PlaylistEntry(
                    frame_id=schedule.frame_id,
                    photo_id=photo.id,
                    order=1  # Always first position
                )
                self.db.session.add(playlist_entry)

                # Record successful generation with schedule name
                history = self.GenerationHistory(
                    schedule_id=schedule.id,
                    success=True,
                    photo_id=photo.id,
                    name=schedule.name  # Add the schedule name
                )
                self.db.session.add(history)
                self.db.session.commit()

                logging.info(f"Scheduled generation {schedule.id} ({schedule.name}) completed successfully")

            except Exception as e:
                logging.error(f"Error in scheduled generation {schedule.id}: {str(e)}")
                # Record failed generation with schedule name
                history = self.GenerationHistory(
                    schedule_id=schedule.id,
                    success=False,
                    error_message=str(e),
                    name=schedule.name if schedule else "Unknown"  # Add the schedule name
                )
                self.db.session.add(history)
                self.db.session.commit()

    def add_job(self, schedule_id, cron_expression):
        """Add a new scheduled job."""
        try:
            job_id = str(schedule_id)
            # Check if job already exists
            existing_job = self.scheduler.get_job(job_id)
            
            if existing_job:
                # Job exists, reschedule it
                self.scheduler.reschedule_job(
                    job_id=job_id,
                    trigger='cron',
                    **self._parse_cron(cron_expression)
                )
                logger.info(f"Rescheduled existing job {schedule_id} with cron: {cron_expression}")
            else:
                # Job doesn't exist, add it
                self.scheduler.add_job(
                    func=self.execute_generation,
                    trigger='cron',
                    args=[schedule_id],
                    id=job_id,
                    **self._parse_cron(cron_expression)
                )
                logger.info(f"Added scheduled generation job {schedule_id} with cron: {cron_expression}")
        except Exception as e:
            logger.error(f"Error adding scheduled job {schedule_id}: {str(e)}")

    def modify_job(self, schedule_id, new_cron_expression):
        """Modify an existing job's schedule."""
        try:
            self.scheduler.reschedule_job(
                job_id=str(schedule_id),
                trigger='cron',
                **self._parse_cron(new_cron_expression)
            )
            logger.info(f"Modified schedule for job {schedule_id} to: {new_cron_expression}")
        except Exception as e:
            logger.error(f"Error modifying job {schedule_id}: {str(e)}")
            raise

    def remove_job(self, schedule_id):
        """Remove a scheduled job."""
        try:
            self.scheduler.remove_job(str(schedule_id))
            logger.info(f"Removed scheduled generation job {schedule_id}")
        except Exception as e:
            logger.error(f"Error removing scheduled job {schedule_id}: {str(e)}")

    def _parse_cron(self, cron_expression):
        """Parse cron expression into kwargs for scheduler."""
        parts = cron_expression.split()
        return {
            'minute': parts[0],
            'hour': parts[1],
            'day': parts[2],
            'month': parts[3],
            'day_of_week': parts[4]
        }

    def check_network_locations_for_new_media(self):
        """Check network locations for new media and import to selected frames."""
        with self.app.app_context():
            try:
                # Check if this is a duplicate run (within 5 minutes of the last run)
                current_time = datetime.now()
                if self.last_network_run_time and (current_time - self.last_network_run_time).total_seconds() < 300:
                    logger.info("Skipping network location import - last run was less than 5 minutes ago")
                    return
                
                logger.info("Starting automatic check for new media in network locations")
                
                # Import necessary functions from integration_routes
                from integration_routes import (
                    load_network_locations,
                    load_imported_files,
                    save_imported_files,
                    get_media_files_in_location
                )
                
                # Load network locations
                data = load_network_locations()
                locations = data.get('locations', [])
                
                # Filter locations with auto-add enabled
                auto_add_locations = [loc for loc in locations if loc.get('autoAddNewMedia', False) and loc.get('autoAddTargetFrameId')]
                
                if not auto_add_locations:
                    logger.info("No network locations with auto-add enabled")
                    return
                
                # Process each location
                for location in auto_add_locations:
                    location_id = location.get('id')
                    target_frame_id = location.get('autoAddTargetFrameId')
                    network_path = location.get('network_path')
                    username = location.get('username', '')
                    password = location.get('password', '')
                    
                    logger.info(f"Checking location '{location.get('name')}' (ID: {location_id}) for new media")
                    
                    # Load previously imported files
                    imported_files = load_imported_files(location_id)
                    
                    # Get all media files in the location
                    media_files = get_media_files_in_location(network_path, username, password)
                    
                    # Filter out already imported files
                    new_files = [f for f in media_files if f not in imported_files]
                    
                    if not new_files:
                        logger.info(f"No new media files found in location '{location.get('name')}'")
                        continue
                    
                    logger.info(f"Found {len(new_files)} new media files in location '{location.get('name')}'")
                    
                    # Import each new file
                    for file_path in new_files:
                        try:
                            # Import the file using our updated method
                            result = self.import_media_file(file_path, target_frame_id, location)
                            
                            if result:
                                # Add to imported files list
                                imported_files.append(file_path)
                                logger.info(f"Successfully imported file '{file_path}' to frame {target_frame_id}")
                            else:
                                logger.error(f"Failed to import file '{file_path}' to frame {target_frame_id}")
                        except Exception as e:
                            logger.error(f"Error importing file '{file_path}': {str(e)}")
                    
                    # Save updated imported files list
                    save_imported_files(location_id, imported_files)
                
                # Update the last run time
                self.last_network_run_time = current_time
                
                logger.info("Completed automatic check for new media in network locations")
                
            except Exception as e:
                logger.error(f"Error in automatic media import: {str(e)}")
    
    def load_network_locations(self):
        """Load network locations configuration from file."""
        try:
            config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "config")
            network_locations_path = os.path.join(config_dir, "network_locations.json")
            
            if not os.path.exists(network_locations_path):
                logger.warning("Network locations file does not exist")
                return []
                
            with open(network_locations_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading network locations: {e}")
            return []
    
    def get_imported_files(self, location_id):
        """Get list of files already imported from this location."""
        try:
            imported_files_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                "config",
                f"imported_files_{location_id}.json"
            )
            
            if not os.path.exists(imported_files_path):
                return []
                
            with open(imported_files_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading imported files for location {location_id}: {e}")
            return []
    
    def save_imported_files(self, location_id, imported_files):
        """Save list of imported files for this location."""
        try:
            config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "config")
            if not os.path.exists(config_dir):
                os.makedirs(config_dir)
                
            imported_files_path = os.path.join(config_dir, f"imported_files_{location_id}.json")
            
            with open(imported_files_path, 'w') as f:
                json.dump(imported_files, f)
        except Exception as e:
            logger.error(f"Error saving imported files for location {location_id}: {e}")
    
    def import_new_media_files(self, directory, frame_id, imported_files, location_id):
        """Scan directory for new media files and import them."""
        try:
            # Get all files in the directory
            all_files = []
            for root, _, files in os.walk(directory):
                for file in files:
                    if self.is_supported_media_file(file):
                        file_path = os.path.join(root, file)
                        rel_path = os.path.relpath(file_path, directory)
                        all_files.append(rel_path)
            
            # Find new files (not in imported_files list)
            new_files = [f for f in all_files if f not in imported_files]
            
            if not new_files:
                logger.info(f"No new media files found in location {location_id}")
                return
            
            logger.info(f"Found {len(new_files)} new media files in location {location_id}")
            
            # Import each new file
            for file in new_files:
                full_path = os.path.join(directory, file)
                try:
                    self.import_media_file(full_path, frame_id, location_id)
                    imported_files.append(file)
                except Exception as e:
                    logger.error(f"Error importing file {full_path}: {e}")
            
            # Save updated imported files list
            self.save_imported_files(location_id, imported_files)
            
        except Exception as e:
            logger.error(f"Error scanning directory {directory}: {e}")
    
    def is_supported_media_file(self, filename):
        """Check if file is a supported media type."""
        extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        return any(filename.lower().endswith(ext) for ext in extensions)
    
    def import_media_file(self, file_path, frame_id, location):
        """Import a media file to the specified frame using the existing import functionality."""
        with self.app.app_context():
            try:
                logger.info(f"Importing file {file_path} to frame {frame_id}")
                
                # Use the existing import_file_to_frame function from integration_routes
                from integration_routes import import_file_to_frame
                
                # Import necessary modules
                from server import Photo, PlaylistEntry, photo_processor, extract_exif_metadata, generate_video_thumbnail
                
                # Call the import function
                result = import_file_to_frame(
                    location,
                    file_path,
                    frame_id,
                    self.app,
                    self.db,
                    Photo,
                    PlaylistEntry,
                    photo_processor,
                    extract_exif_metadata,
                    generate_video_thumbnail
                )
                
                if result:
                    logger.info(f"Successfully imported {file_path} to frame {frame_id}")
                    return True
                else:
                    logger.error(f"Failed to import {file_path} to frame {frame_id}")
                    return False
                
            except Exception as e:
                logger.error(f"Error importing file {file_path}: {e}")
                return False

    def shutdown(self):
        """Shutdown the scheduler gracefully."""
        if self.scheduler:
            self.scheduler.shutdown()
            logging.info("Scheduler shutdown complete")

    def check_immich_for_new_media(self):
        """Check Immich for new media and import to selected frames."""
        with self.app.app_context():
            try:
                # Check if this is a duplicate run (within 5 minutes of the last run)
                current_time = datetime.now()
                if self.last_immich_run_time and (current_time - self.last_immich_run_time).total_seconds() < 300:
                    logger.info("Skipping Immich import - last run was less than 5 minutes ago")
                    return
                
                logger.info("Starting automatic check for new media in Immich")
                
                # Import the function from integration_routes
                from integration_routes import check_immich_for_new_media as check_immich
                
                # Call the existing function which should handle importing to the correct frames
                # based on the user's configuration in the Immich integration settings
                check_immich()
                
                logger.info("Completed automatic check for new media in Immich")
                
                # Update the last run time
                self.last_immich_run_time = current_time
                
            except Exception as e:
                logger.error(f"Error in automatic Immich media import: {str(e)}")

    def load_scheduled_generations(self):
        """Load all active scheduled generations from the database and schedule them."""
        with self.app.app_context():
            try:
                # Query all active scheduled generations
                scheduled_generations = self.ScheduledGeneration.query.filter_by(is_active=True).all()
                
                for schedule in scheduled_generations:
                    # Check if job already exists before adding
                    job_id = str(schedule.id)
                    if job_id not in [job.id for job in self.scheduler.get_jobs()]:
                        # Only add if it doesn't exist
                        self.add_job(schedule.id, schedule.cron_expression)
                        logger.info(f"Scheduled generation job {schedule.id}: {schedule.name}")
                    else:
                        logger.info(f"Job {schedule.id} already exists, skipping")
                
                logger.info(f"Loaded and scheduled {len(scheduled_generations)} active generation jobs")
            except Exception as e:
                logger.error(f"Error loading scheduled generations: {str(e)}")