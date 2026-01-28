# Photo Workflow

This document explains the complete lifecycle of photos in the Photo Frame Assistant, from upload to display on frames.

## Overview

The Photo Frame Assistant processes photos through several stages, from initial upload or import to final display on photo frames. This workflow includes format conversion, thumbnail generation, metadata extraction, AI analysis, and preparation for display.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │     │             │
│  Upload/    │────►│  Processing │────►│  Analysis   │────►│  Storage    │
│  Import     │     │             │     │             │     │             │
│             │     │             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └──────┬──────┘
                                                                   │
                                                                   ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │     │             │
│  Frame      │◄────┤  Overlay    │◄────┤  Format     │◄────┤  Playlist   │
│  Display    │     │  Application│     │  Adaptation │     │  Assignment │
│             │     │             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

## Photo Sources

Photos can enter the system through various sources:

### 1. Direct Upload

Users can upload photos directly through the web interface:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  User       │────►│  Web        │────►│  Server     │
│  Device     │     │  Interface  │     │             │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

The upload process supports:
- Multiple file selection
- Drag-and-drop functionality
- Progress tracking
- Format validation

### 2. External Service Import

Photos can be imported from external services:

- **Google Photos**: Import from Google Photos library
- **Unsplash**: Import from Unsplash photo service
- **Pixabay**: Import from Pixabay photo service

### 3. AI Generation

Photos can be generated using AI services:

- **DALL-E**: Generate images using OpenAI's DALL-E
- **Stability AI**: Generate images using Stability AI's models

### 4. Scheduled Import

Photos can be automatically imported on a schedule:

- **Unsplash Schedules**: Regularly import photos matching specific queries
- **Pixabay Schedules**: Regularly import photos matching specific queries
- **AI Generation Schedules**: Regularly generate images based on prompts

## Initial Processing

When a photo enters the system, it undergoes several processing steps:

### 1. Format Conversion

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Original   │────►│  Format     │────►│  Converted  │
│  File       │     │  Detection  │     │  File       │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

- **HEIC/HEIF Conversion**: Convert Apple HEIC/HEIF formats to JPEG
- **AVIF Conversion**: Convert AVIF format to JPEG if needed
- **Video Processing**: Extract thumbnails from videos

### 2. Thumbnail Generation

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Original   │────►│  Resize &   │────►│  Thumbnail  │
│  Image      │     │  Compress   │     │  Image      │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

The system generates several versions of each photo:
- **Thumbnail**: Small version for UI display (200px)
- **Portrait Version**: Optimized for portrait orientation frames
- **Landscape Version**: Optimized for landscape orientation frames

### 3. Metadata Extraction

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Image      │────►│  EXIF       │────►│  Extracted  │
│  File       │     │  Parser     │     │  Metadata   │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

The system extracts and stores EXIF metadata:
- Camera model and settings
- Date and time taken
- GPS coordinates (if available)
- Orientation information
- Copyright and author information

## AI Analysis

Photos can be analyzed using AI services to extract additional information:

### 1. Content Analysis

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Image      │────►│  AI Vision  │────►│  Content    │
│             │     │  API        │     │  Description│
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

AI analysis can provide:
- Object detection and identification
- Scene classification
- Descriptive captions
- Color analysis
- Aesthetic quality assessment

### 2. Tagging and Categorization

Based on AI analysis, photos are tagged and categorized:

```json
{
  "tags": ["sunset", "beach", "ocean", "silhouette"],
  "categories": ["nature", "landscape"],
  "colors": ["orange", "blue", "black"],
  "quality_score": 0.87,
  "description": "A beautiful sunset over the ocean with silhouettes of palm trees"
}
```

These tags and categories enable:
- Improved search functionality
- Smart playlist generation
- Dynamic content selection

## Database Storage

After processing and analysis, photo information is stored in the database:

### Database Schema

```
┌───────────────────────────┐
│ Photo                     │
├───────────────────────────┤
│ id: Integer (PK)          │
│ filename: String          │
│ portrait_version: String  │
│ landscape_version: String │
│ thumbnail: String         │
│ uploaded_at: DateTime     │
│ heading: Text             │
│ ai_description: JSON      │
│ ai_analyzed_at: DateTime  │
│ media_type: String        │
│ duration: Float           │
│ exif_metadata: JSON       │
└───────────────────────────┘
```

### File Storage

The actual photo files are stored in the filesystem:
- Original files: `uploads/[filename]`
- Thumbnails: `uploads/thumbnails/[filename]`
- Portrait versions: `uploads/portrait/[filename]`
- Landscape versions: `uploads/landscape/[filename]`

## Playlist Assignment

Photos are assigned to playlists for display on frames:

### Manual Assignment

Users can manually assign photos to frame playlists through the web interface:

```
┌───────────────────────────┐
│ PlaylistEntry             │
├───────────────────────────┤
│ id: Integer (PK)          │
│ frame_id: String (FK)     │
│ photo_id: Integer (FK)    │
│ order: Integer            │
│ date_added: DateTime      │
└───────────────────────────┘
```

### Dynamic Playlists

Photos can be dynamically assigned to playlists based on AI-powered criteria:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Dynamic    │────►│  AI         │────►│  Selected   │
│  Playlist   │     │  Matching   │     │  Photos     │
│  Prompt     │     │  Engine     │     │             │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

Dynamic playlists use:
- Natural language prompts
- AI-generated tags and descriptions
- Semantic similarity matching
- User feedback and preferences

## Format Adaptation

Before sending photos to frames, they are adapted to the specific frame requirements:

### 1. Orientation Matching

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Frame      │────►│  Orientation│────►│  Appropriate│
│  Settings   │     │  Detection  │     │  Version    │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

- Portrait frames receive the portrait version
- Landscape frames receive the landscape version
- If orientation changes, the appropriate version is selected

### 2. Image Processing

Photos can be processed according to frame settings:

```python
def process_image_for_frame(image, frame):
    """Apply frame-specific image processing."""
    # Apply contrast adjustment
    if frame.contrast_factor != 1.0:
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(frame.contrast_factor)
    
    # Apply saturation adjustment
    if frame.saturation != 100:
        enhancer = ImageEnhance.Color(image)
        image = enhancer.enhance(frame.saturation / 100.0)
    
    # Apply blue channel adjustment
    if frame.blue_adjustment != 0:
        r, g, b = image.split()
        b = ImageEnhance.Brightness(b).enhance(1.0 + frame.blue_adjustment / 100.0)
        image = Image.merge("RGB", (r, g, b))
    
    # Apply padding if specified
    if frame.padding > 0:
        width, height = image.size
        new_image = Image.new(image.mode, (width, height), (0, 0, 0))
        new_image.paste(image, (frame.padding, frame.padding, 
                               width - frame.padding, height - frame.padding))
        image = new_image
    
    # Apply color map if specified
    if frame.color_map:
        # Convert to palette mode with custom color map
        # Implementation depends on specific color map format
        pass
    
    return image
```

## Overlay Application

Before final delivery to frames, overlays can be applied to photos:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Processed  │────►│  Overlay    │────►│  Final      │
│  Image      │     │  Manager    │     │  Image      │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

Available overlays include:
- Weather information
- Time and date
- Photo metadata
- QR codes
- Custom text

## Frame Delivery

Finally, photos are delivered to frames:

### 1. Frame Request

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Frame      │────►│  API        │────►│  Photo      │
│  Wake-up    │     │  Request    │     │  Selection  │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

When a frame wakes up, it requests the next photo:
1. Frame sends a request to `/api/next_photo?id={frame_id}`
2. Server determines the next photo based on playlist and settings
3. Server returns photo metadata and URL

### 2. Photo Download

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Frame      │────►│  Download   │────►│  Display    │
│  Request    │     │  Photo      │     │  Photo      │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘
```

The frame downloads the actual photo file:
1. Frame requests the photo file from `/photos/{filename}`
2. Server returns the binary image data
3. Frame displays the photo

## Photo Lifecycle Management

The system includes features for managing the lifecycle of photos:

### 1. Cleanup and Archiving

Old or unused photos can be automatically cleaned up:
- Photos not in any playlist for a specified period
- Photos with low quality scores
- Duplicate or similar photos

### 2. Storage Management

The system monitors and manages storage usage:
- Storage usage statistics
- Automatic cleanup when storage is low
- Compression of older photos

## Implementation Details

### Upload Handler

```python
@app.route('/upload', methods=['GET', 'POST'])
def upload_photo():
    if request.method == 'POST':
        # Check if this is an API request
        is_api_request = request.headers.get('accept') == 'application/json'
        
        if 'photo' not in request.files:
            if is_api_request:
                return jsonify({'success': False, 'error': 'No photo file provided'}), 400
            flash('No photo file provided.')
            return redirect(request.url)
        
        file = request.files['photo']
        if file.filename == '':
            if is_api_request:
                return jsonify({'success': False, 'error': 'No selected file'}), 400
            flash('No selected file.')
            return redirect(request.url)
            
        if file and allowed_file(file.filename):
            # Get frame_id from form data
            frame_id = request.form.get('frame_id')
            
            # Create thumbnails directory if it doesn't exist
            thumbnails_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails')
            os.makedirs(thumbnails_dir, exist_ok=True)
            
            # Save original file
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Extract EXIF metadata
            exif_metadata = extract_exif_metadata(filepath)
            
            # Process the file (convert format if needed, generate thumbnails)
            processed_file = process_uploaded_file(filepath, filename)
            
            # Create database entry
            photo = Photo(
                filename=processed_file['filename'],
                portrait_version=processed_file.get('portrait_version'),
                landscape_version=processed_file.get('landscape_version'),
                thumbnail=processed_file.get('thumbnail'),
                media_type=processed_file.get('media_type', 'photo'),
                duration=processed_file.get('duration'),
                exif_metadata=exif_metadata
            )
            
            db.session.add(photo)
            db.session.commit()
            
            # If frame_id is provided, add to that frame's playlist
            if frame_id:
                add_to_playlist(frame_id, photo.id)
            
            # Start AI analysis in background if enabled
            if app.config.get('AI_ANALYSIS_ENABLED', False):
                Thread(target=async_analyze, args=(app, db, photo.id)).start()
            
            if is_api_request:
                return jsonify({
                    'success': True, 
                    'photo_id': photo.id,
                    'filename': photo.filename
                })
                
            flash('Photo uploaded successfully!')
            return redirect(url_for('index'))
    
    # GET request - show upload form
    frames = PhotoFrame.query.all()
    return render_template('upload.html', frames=frames)
```

### File Processing

```python
def process_uploaded_file(filepath, filename):
    """Process an uploaded file, converting formats and generating thumbnails."""
    result = {
        'filename': filename
    }
    
    # Determine file type
    media_type = 'photo'
    if filename.lower().endswith(('.mp4', '.mov', '.avi')):
        media_type = 'video'
        # Generate video thumbnail
        thumbnail_filename = f"thumb_{filename.rsplit('.', 1)[0]}.jpg"
        thumbnail_path = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails', thumbnail_filename)
        duration = generate_video_thumbnail(filepath, thumbnail_path)
        result['thumbnail'] = f"thumbnails/{thumbnail_filename}"
        result['duration'] = duration
        result['media_type'] = 'video'
        return result
    
    # Convert HEIC/HEIF to JPG
    if filename.lower().endswith(('.heic', '.heif')):
        heif_file = pyheif.read(filepath)
        img = Image.frombytes(
            heif_file.mode, 
            heif_file.size, 
            heif_file.data,
            "raw", 
            heif_file.mode, 
            heif_file.stride,
        )
        
        # Save as JPG
        new_filename = f"{filename.rsplit('.', 1)[0]}.jpg"
        new_filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
        img.save(new_filepath, "JPEG")
        
        # Update result
        result['filename'] = new_filename
        filepath = new_filepath
    
    # Generate thumbnail
    try:
        img = Image.open(filepath)
        
        # Generate thumbnail
        thumbnail_filename = f"thumb_{result['filename']}"
        thumbnail_path = os.path.join(app.config['UPLOAD_FOLDER'], 'thumbnails', thumbnail_filename)
        img.thumbnail((200, 200))
        img.save(thumbnail_path)
        result['thumbnail'] = f"thumbnails/{thumbnail_filename}"
        
        # Generate portrait and landscape versions
        width, height = img.size
        
        # Portrait version
        portrait_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'portrait')
        os.makedirs(portrait_dir, exist_ok=True)
        portrait_filename = f"portrait_{result['filename']}"
        portrait_path = os.path.join(portrait_dir, portrait_filename)
        
        if width > height:
            # Landscape image needs cropping for portrait
            img_portrait = img.copy()
            left = (width - height) / 2
            top = 0
            right = (width + height) / 2
            bottom = height
            img_portrait = img_portrait.crop((left, top, right, bottom))
        else:
            # Already portrait
            img_portrait = img.copy()
        
        img_portrait.save(portrait_path)
        result['portrait_version'] = f"portrait/{portrait_filename}"
        
        # Landscape version
        landscape_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'landscape')
        os.makedirs(landscape_dir, exist_ok=True)
        landscape_filename = f"landscape_{result['filename']}"
        landscape_path = os.path.join(landscape_dir, landscape_filename)
        
        if height > width:
            # Portrait image needs cropping for landscape
            img_landscape = img.copy()
            left = 0
            top = (height - width) / 2
            right = width
            bottom = (height + width) / 2
            img_landscape = img_landscape.crop((left, top, right, bottom))
        else:
            # Already landscape
            img_landscape = img.copy()
        
        img_landscape.save(landscape_path)
        result['landscape_version'] = f"landscape/{landscape_filename}"
        
    except Exception as e:
        app.logger.error(f"Error processing image: {e}")
    
    result['media_type'] = media_type
    return result
```

### AI Analysis

```python
def async_analyze(app, db, photo_id):
    """Analyze a photo using AI in the background."""
    with app.app_context():
        try:
            # Get the photo
            photo = db.session.get(Photo, photo_id)
            if not photo:
                return
            
            # Get the file path
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
            if not os.path.exists(filepath):
                return
            
            # Initialize the analyzer
            analyzer = PhotoAnalyzer()
            
            # Analyze the photo
            analysis_result = analyzer.analyze_photo(filepath)
            
            # Update the photo record
            photo.ai_description = analysis_result
            photo.ai_analyzed_at = datetime.utcnow()
            db.session.commit()
            
            app.logger.info(f"AI analysis completed for photo {photo_id}")
            
        except Exception as e:
            app.logger.error(f"Error in AI analysis for photo {photo_id}: {e}")
```

### Next Photo Selection

```python
@app.route('/api/next_photo')
def get_next_photo():
    """Get the next photo for a frame."""
    frame_id = request.args.get('id')
    if not frame_id:
        return jsonify({'error': 'Frame ID is required'}), 400
    
    # Get the frame
    frame = db.session.get(PhotoFrame, frame_id)
    if not frame:
        return jsonify({'error': 'Frame not found'}), 404
    
    # Update last wake time
    frame.last_wake_time = datetime.now(timezone.utc)
    
    # Get the next photo
    next_photo = PhotoHelper.get_next_photo(frame_id)
    if not next_photo:
        return jsonify({'error': 'No photos available'}), 404
    
    # Update current photo
    frame.current_photo_id = next_photo.id
    
    # Calculate next wake time
    sleep_interval = calculate_sleep_interval(frame)
    next_wake_time = frame.last_wake_time + timedelta(seconds=sleep_interval)
    frame.next_wake_time = next_wake_time
    
    db.session.commit()
    
    # Prepare response
    photo_url = url_for('serve_photo', filename=next_photo.filename, _external=True)
    
    # Get appropriate version based on frame orientation
    if frame.orientation == 'portrait' and next_photo.portrait_version:
        photo_url = url_for('serve_photo', filename=next_photo.portrait_version, _external=True)
    elif frame.orientation == 'landscape' and next_photo.landscape_version:
        photo_url = url_for('serve_photo', filename=next_photo.landscape_version, _external=True)
    
    # Get weather data if needed
    weather_data = None
    if frame.overlay_preferences:
        preferences = json.loads(frame.overlay_preferences)
        if preferences.get('weather', False):
            weather_integration = WeatherIntegration()
            weather_data = weather_integration.get_current_weather()
    
    response = {
        'photo_id': next_photo.id,
        'filename': next_photo.filename,
        'url': photo_url,
        'heading': next_photo.heading,
        'sleep_interval': sleep_interval,
        'next_wake_time': next_wake_time.isoformat(),
        'exif_metadata': next_photo.exif_metadata,
        'weather_data': weather_data
    }
    
    return jsonify(response)
```

## References

- [EXIF Specification](https://www.exif.org/specifications.html)
- [Pillow Image Processing Library](https://pillow.readthedocs.io/)
- [OpenAI Vision API](https://platform.openai.com/docs/guides/vision)
- [FFmpeg Documentation](https://ffmpeg.org/documentation.html) 