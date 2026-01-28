#!/bin/bash
set -e

echo "Setting up Photo Server Docker environment for Raspberry Pi..."

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "Docker not found. Installing Docker..."
    curl -sSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "Docker installed. You may need to log out and back in for group changes to take effect."
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "Docker Compose not found. Installing Docker Compose..."
    sudo apt-get update
    sudo apt-get install -y python3-pip
    sudo pip3 install docker-compose
    echo "Docker Compose installed."
fi

# Enable I2C if not already enabled
if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt; then
    echo "Enabling I2C interface..."
    sudo raspi-config nonint do_i2c 0
    echo "I2C interface enabled. A reboot may be required."
fi

# Create directories if they don't exist
mkdir -p uploads logs credentials

# Set permissions
echo "Setting permissions for data directories..."
chmod -R 777 uploads logs credentials

# Build and start the container
echo "Building and starting the Docker container..."
docker-compose up -d --build

echo "Setup complete! The Photo Server should be running at http://$(hostname -I | awk '{print $1}'):5000"
echo "You may need to reboot your Raspberry Pi if this is the first time enabling I2C." 