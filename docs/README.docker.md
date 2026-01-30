# Photo Frame Assistant Server Docker Setup

This document provides instructions for running the Photo Server application using Docker.

## Prerequisites

- Docker installed on your system
- Docker Compose installed on your system
- **Enough free disk space** for the image build (about **10 GB** recommended; the image includes PyTorch and ML dependencies)

## System Dependencies

The Docker image includes the following system dependencies:
- Python 3 and related packages (python3, python3-pip, python3-venv)
- Image processing libraries (libopenjp2-7, python3-pil, imagemagick)
- Display utilities (fbi, libsdl2-2.0-0)
- Hardware interaction tools (python3-smbus, i2c-tools)
- Media processing (ffmpeg)
- HEIF image support (libheif-dev)
- ImageMagick dependencies (libmagickwand-dev)
- Build tools (gcc, g++, build-essential)

## Getting Started

1. Clone the repository or copy the application files to your local machine.

2. Navigate to the application directory:
   ```
   cd path/to/photo_server
   ```

3. Build and start the Docker container:
   ```
   docker-compose up -d
   ```

   This will:
   - Build the Docker image
   - Start the container in detached mode
   - Map port 5000 to your host machine
   - Create persistent volumes for uploads, database, logs, and credentials

4. Access the application in your web browser:
   ```
   http://localhost:5000
   ```

5. Verify the server is running correctly:
   ```
   chmod +x scripts/check_server.sh
   ./scripts/check_server.sh
   ```

## Configuration

### Timezone

You can change the timezone by editing the `TZ` environment variable in the `docker-compose.yml` file:

```yaml
environment:
  - TZ=America/New_York  # Change to your timezone
```

### Persistent Data

All persistent data (photos, database, logs) is stored in a Docker named volume:

- `photo-frame-data:/app/data` - Contains uploads, database, and logs

Configuration files are stored in a bind mount:

- `./config:/app/config` - Configuration files (server settings, integrations, etc.)

**IMPORTANT: Data Persistence**

Your photos and database are stored in the `photo-frame-data` Docker volume. This volume persists across:
- Container restarts (`docker compose restart`)
- Container recreation (`docker compose up -d` after rebuilds)
- Image updates

**WARNING: Never use `docker compose down -v`** - the `-v` flag deletes volumes and will permanently delete all your photos and database!

To check your volume status:
```bash
docker volume ls | grep photo-frame
docker volume inspect photo-frame-data
```

To safely update/rebuild without losing data:
```bash
# Safe - data is preserved:
docker compose build
docker compose up -d

# Safe - just restarts:
docker compose restart

# DANGEROUS - deletes all data:
# docker compose down -v   # DO NOT USE unless you want to delete everything
```

## Managing the Container

### View logs

```
docker-compose logs -f
```

### Stop the container

```
docker-compose down
```

### Restart the container

```
docker-compose restart
```

### Rebuild the container (after code changes)

```
docker-compose up -d --build
```

## Database Management

The application includes several database management tools:

### Automatic Database Initialization

The database is automatically initialized when the container starts if it doesn't exist. This is handled by the `db_manager.py` script.

### Manual Database Management

You can manually manage the database using the `db_manager.py` script:

```
# Create a new database (will fail if database already exists)
docker-compose exec photo-server python db_manager.py --create

# Force creation of a new database (will overwrite existing database)
docker-compose exec photo-server python db_manager.py --create --force

# Migrate an existing database to match the current models
docker-compose exec photo-server python db_manager.py --migrate

# Backup the database
docker-compose exec photo-server python db_manager.py --backup
```

Database backups are stored in the `db_backups` directory.

## Troubleshooting

### No space left on device (build failure)

Building the image installs large Python dependencies (PyTorch, sentence-transformers, CUDA-related packages), which can use **several GB of free disk space** during the build. If you see:

```text
ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device
```

**Options:**

1. **Free disk space on the build host**
   - Remove unused Docker data:  
     `docker system prune -a`  
     (removes unused images, containers, and build cache)
   - Remove other large files or move the project to a disk with more space.
   - Ensure at least **~10 GB free** before building.

2. **Build on a machine with more space, then use the image on the device**
   - On a machine with enough disk (e.g. a PC or CI), run:  
     `docker build -t ghcr.io/qrobinso/photo_frame_assistant_server:latest -f docker/Dockerfile .`  
     then push to your registry.
   - On the photo frame device, pull and run:  
     `docker pull ghcr.io/qrobinso/photo_frame_assistant_server:latest`  
     and start with your usual `docker-compose` or `docker run`.

3. **Check free space**
   - Before building: `df -h` and `docker system df` to see disk and Docker disk usage.

### Port conflicts

If port 5000 is already in use on your host machine, you can change the port mapping in `docker-compose.yml`:

```yaml
ports:
  - "8080:5000"  # Map container port 5000 to host port 8080
```

### Permission issues

If you encounter permission issues with the mounted volumes, you may need to adjust the permissions on your host machine:

```
chmod -R 777 ./uploads ./logs ./credentials ./db_backups
```

Note: This is not recommended for production environments. Consider using Docker volumes instead.

### Database initialization

If the database doesn't initialize correctly, you can manually run the database manager:

```
docker-compose exec photo-server python db_manager.py --create --force
```

### Data loss after rebuild/recreate

If your data disappeared after rebuilding or recreating the container, your data may be in an old volume with a different name. Check for existing volumes:

```bash
docker volume ls
```

You might see volumes like `docker_photo_data` or `photo_frame_assistant_photo_data` (the old auto-generated names). To migrate data from an old volume to the new `photo-frame-data` volume:

```bash
# 1. Stop the container
docker compose down

# 2. Create a temporary container to copy data
docker run --rm -v OLD_VOLUME_NAME:/old -v photo-frame-data:/new alpine sh -c "cp -a /old/. /new/"

# 3. Start the container
docker compose up -d

# 4. Verify your data is restored, then optionally remove the old volume
docker volume rm OLD_VOLUME_NAME
```

Replace `OLD_VOLUME_NAME` with the actual old volume name (e.g., `docker_photo_data`).

### Hardware Access

For I2C and other hardware access, you may need to run the container with additional privileges. Add the following to your docker-compose.yml:

```yaml
services:
  photo-server:
    # ... existing configuration ...
    privileged: true  # Gives the container full access to host devices
    devices:
      - /dev/i2c-1:/dev/i2c-1  # Map I2C device if needed
```

## Raspberry Pi Setup

For Raspberry Pi users, a setup script is provided to help with Docker installation and configuration:

```
chmod +x scripts/setup_docker_pi.sh
./scripts/setup_docker_pi.sh
```

This script will:
1. Install Docker and Docker Compose if not already installed
2. Enable the I2C interface if not already enabled
3. Create necessary directories and set permissions
4. Build and start the Docker container