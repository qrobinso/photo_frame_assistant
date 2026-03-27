import os
import json
import time
import subprocess
import logging
from datetime import datetime, timezone

from PIL import Image, ExifTags

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'heic', 'heif', 'mp4', 'mov', 'MOV', 'avif'}


def allowed_file(filename):
    """Check if the filename has an allowed extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_exif_metadata(image_path):
    """Extract EXIF metadata, handling various types and potential errors."""
    try:
        logger.debug(f"Extracting EXIF from: {image_path}")
        with Image.open(image_path) as img:
            exif_data = img._getexif()
            if not exif_data:
                # Create basic metadata using file modification time if EXIF is missing
                mtime = os.path.getmtime(image_path)
                upload_time = datetime.fromtimestamp(mtime, tz=timezone.utc)
                formatted_time_utc = upload_time.strftime('%Y:%m:%d %H:%M:%S')
                metadata = {
                    'DateTime': formatted_time_utc,
                    'DateTimeOriginal': formatted_time_utc,
                    'DateTimeDigitized': formatted_time_utc,
                    'SourceFileInfo': {'FileModifyDate': upload_time.isoformat()},
                    # Add formatted date/time based on server settings later if needed
                }
                logger.info(f"No EXIF found for {os.path.basename(image_path)}, using file modify time.")
                return metadata

            metadata = {}
            for tag_id, value in exif_data.items():
                tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))

                if isinstance(value, bytes):
                    # Attempt to decode bytes, replace errors
                    try:
                        metadata[tag_name] = value.decode('utf-8', errors='replace').strip()
                    except Exception:
                         metadata[tag_name] = repr(value) # Fallback to repr
                elif isinstance(value, tuple) and len(value) > 0 and isinstance(value[0], int):
                     # Handle rational numbers typically stored as (numerator, denominator) tuples
                     if len(value) == 2 and value[1] != 0:
                          try:
                               metadata[tag_name] = float(value[0]) / float(value[1])
                          except ZeroDivisionError:
                               metadata[tag_name] = 0.0
                          except TypeError: # Handle cases where tuple elements are not numbers
                               metadata[tag_name] = str(value)
                     else:
                         # Store other integer tuples as lists
                         metadata[tag_name] = list(value)
                elif isinstance(value, (int, float, str, bool)) or value is None:
                     metadata[tag_name] = value
                else:
                     # Fallback for other non-serializable types
                     try:
                        json.dumps(value) # Test serializability
                        metadata[tag_name] = value
                     except (TypeError, OverflowError):
                        metadata[tag_name] = str(value)

            # Special handling for GPS Info
            if 34853 in exif_data: # GPSInfo IFD tag ID
                gps_info_raw = exif_data[34853]
                gps_data = {}
                for gps_tag_id, gps_value in gps_info_raw.items():
                    gps_tag_name = ExifTags.GPSTAGS.get(gps_tag_id, str(gps_tag_id))
                    # Process GPS values similarly to main EXIF
                    if isinstance(gps_value, bytes):
                        try:
                            gps_data[gps_tag_name] = gps_value.decode('utf-8', errors='replace').strip()
                        except Exception:
                            gps_data[gps_tag_name] = repr(gps_value)
                    elif isinstance(gps_value, tuple) and len(gps_value) > 0 and isinstance(gps_value[0], (int, float)):
                         # GPS Coordinates (Degrees, Minutes, Seconds often as rationals)
                         if len(gps_value) == 3: # DMS format
                             try:
                                 d = float(gps_value[0]) if not isinstance(gps_value[0], tuple) else float(gps_value[0][0])/float(gps_value[0][1])
                                 m = float(gps_value[1]) if not isinstance(gps_value[1], tuple) else float(gps_value[1][0])/float(gps_value[1][1])
                                 s = float(gps_value[2]) if not isinstance(gps_value[2], tuple) else float(gps_value[2][0])/float(gps_value[2][1])
                                 gps_data[gps_tag_name] = [d, m, s] # Store as list [D, M, S]
                             except (ValueError, TypeError, ZeroDivisionError, IndexError):
                                 gps_data[gps_tag_name] = str(gps_value) # Fallback
                         elif len(gps_value) == 2 and isinstance(gps_value[0], int) and gps_value[1] != 0: # Simple rational
                              try:
                                   gps_data[gps_tag_name] = float(gps_value[0]) / float(gps_value[1])
                              except (ZeroDivisionError, TypeError):
                                   gps_data[gps_tag_name] = str(gps_value)
                         else:
                             gps_data[gps_tag_name] = list(gps_value) # Store other tuples as list
                    elif isinstance(gps_value, (int, float, str, bool)) or gps_value is None:
                        gps_data[gps_tag_name] = gps_value
                    else:
                        try:
                           json.dumps(gps_value)
                           gps_data[gps_tag_name] = gps_value
                        except (TypeError, OverflowError):
                           gps_data[gps_tag_name] = str(gps_value)
                metadata['GPSInfo'] = gps_data # Replace raw GPS data with processed dict

                # Attempt to calculate decimal coordinates
                try:
                    lat_dms = gps_data.get('GPSLatitude')
                    lat_ref = gps_data.get('GPSLatitudeRef', 'N')
                    lon_dms = gps_data.get('GPSLongitude')
                    lon_ref = gps_data.get('GPSLongitudeRef', 'E')

                    if isinstance(lat_dms, list) and len(lat_dms) == 3 and isinstance(lon_dms, list) and len(lon_dms) == 3:
                        lat = lat_dms[0] + lat_dms[1] / 60.0 + lat_dms[2] / 3600.0
                        lon = lon_dms[0] + lon_dms[1] / 60.0 + lon_dms[2] / 3600.0
                        if lat_ref == 'S': lat = -lat
                        if lon_ref == 'W': lon = -lon
                        metadata['decimal_latitude'] = round(lat, 6)
                        metadata['decimal_longitude'] = round(lon, 6)
                        metadata['formatted_location'] = f"{abs(lat):.4f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.4f}°{'E' if lon >= 0 else 'W'}"
                except Exception as gps_calc_e:
                    logger.warning(f"Could not calculate decimal GPS coordinates: {gps_calc_e}")

            # Add formatted date/time if DateTime exists (using server's timezone setting)
            if 'DateTimeOriginal' in metadata or 'DateTime' in metadata:
                dt_str = metadata.get('DateTimeOriginal') or metadata.get('DateTime')
                try:
                    # Common EXIF format: 'YYYY:MM:DD HH:MM:SS'
                    naive_dt = datetime.strptime(dt_str, '%Y:%m:%d %H:%M:%S')
                    # Assume the EXIF time is local to where the photo was taken, but store UTC reference
                    # For simplicity here, we just store the naive string, formatting happens on display
                    metadata['formatted_date'] = naive_dt.strftime('%B %d, %Y')
                    metadata['formatted_time'] = naive_dt.strftime('%I:%M %p')
                except (ValueError, TypeError) as fmt_e:
                    logger.warning(f"Could not parse or format EXIF DateTime '{dt_str}': {fmt_e}")

            logger.debug(f"Successfully extracted EXIF for {os.path.basename(image_path)}")
            return metadata

    except Exception as e:
        logger.error(f"Error extracting EXIF metadata from {image_path}: {e}", exc_info=True)
        return None # Return None on failure


def generate_video_thumbnail(video_path, thumbnail_path):
    """Generate a thumbnail from the middle of a video file using ffmpeg."""
    try:
        # Get video duration
        ffprobe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
        duration = float(result.stdout.strip())
        midpoint = duration / 2.0

        # Extract frame
        ffmpeg_cmd = [
            'ffmpeg', '-y', # Overwrite existing thumbnail
            '-i', video_path,
            '-ss', str(midpoint), # Seek to midpoint
            '-vframes', '1', # Extract one frame
            '-vf', 'scale=400:-1', # Scale width to 400px, maintain aspect ratio
            thumbnail_path
        ]
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
        logger.info(f"Generated video thumbnail for {os.path.basename(video_path)} at {thumbnail_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg/ffprobe error generating thumbnail for {video_path}: {e.stderr}")
        return False
    except ValueError as e:
        logger.error(f"Error parsing video duration for {video_path}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error generating video thumbnail for {video_path}: {e}")
        return False


def cleanup_temp_files(directory, max_age_hours=1):
    """Clean up temporary files (e.g., temp_*.jpg) older than max_age_hours."""
    try:
        now = time.time()
        cutoff = now - (max_age_hours * 3600)
        for filename in os.listdir(directory):
            if filename.startswith('temp_'):
                file_path = os.path.join(directory, filename)
                try:
                    if os.path.isfile(file_path):
                        file_mod_time = os.path.getmtime(file_path)
                        if file_mod_time < cutoff:
                            os.remove(file_path)
                            logger.debug(f"Cleaned up old temp file: {file_path}")
                except Exception as e:
                    logger.warning(f"Error processing temp file {file_path} for cleanup: {e}")
    except FileNotFoundError:
        logger.warning(f"Temp file directory not found for cleanup: {directory}")
    except Exception as e:
        logger.error(f"Error during temp file cleanup in {directory}: {e}")
