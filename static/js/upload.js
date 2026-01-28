function deletePhoto(photoId) {
    if (confirm('Are you sure you want to delete this photo?')) {
        fetch(`/photos/${photoId}/delete`, {
            method: 'DELETE',
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Remove the photo element from the grid
                document.getElementById(`photo-${photoId}`).remove();
            } else {
                alert('Error deleting photo: ' + data.error);
            }
        })
        .catch(error => {
            alert('Error deleting photo: ' + error);
        });
    }
} 