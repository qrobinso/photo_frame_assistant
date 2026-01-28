#!/bin/bash

# Check if the server is running
curl -s http://localhost:5000 > /dev/null
if [ $? -eq 0 ]; then
    echo "Photo Server is running correctly!"
    exit 0
else
    echo "Photo Server is not responding!"
    exit 1
fi 