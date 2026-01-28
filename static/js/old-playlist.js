function initDragAndDrop() {
    const rows = document.querySelectorAll('.photo-row');
    rows.forEach(row => {
        row.draggable = true;
        row.addEventListener('dragstart', handleDragStart);
        row.addEventListener('dragend', handleDragEnd);
    });

    document.querySelector('.playlist').addEventListener('dragover', handleDragOver);
    document.querySelector('.playlist').addEventListener('drop', handleDrop);
    document.querySelector('.bench').addEventListener('dragover', handleDragOver);
    document.querySelector('.bench').addEventListener('drop', handleDrop);
}

function handleDragStart(e) {
    e.target.classList.add('dragging');
    e.dataTransfer.setData('text/plain', e.target.id);
}

function handleDragEnd(e) {
    e.target.classList.remove('dragging');
    updateOrderNumbers();
    savePlaylist();
}

function handleDragOver(e) {
    e.preventDefault();
    const draggable = document.querySelector('.dragging');
    if (!draggable) return;
    
    const container = e.currentTarget;
    const afterElement = getDragAfterElement(container, e.clientY);
    
    if (afterElement) {
        container.insertBefore(draggable, afterElement);
    } else {
        container.appendChild(draggable);
    }
    
    // Update order numbers during drag
    if (container.classList.contains('playlist')) {
        updateOrderNumbers();
    }
}

function getDragAfterElement(container, y) {
    const draggableElements = [...container.querySelectorAll('.photo-row:not(.dragging)')];
    
    return draggableElements.reduce((closest, child) => {
        const box = child.getBoundingClientRect();
        const offset = y - box.top - box.height / 2;
        
        if (offset < 0 && offset > closest.offset) {
            return { offset: offset, element: child };
        } else {
            return closest;
        }
    }, { offset: Number.NEGATIVE_INFINITY }).element;
}

function handleDrop(e) {
    e.preventDefault();
    const draggedId = e.dataTransfer.getData('text/plain');
    const draggedElement = document.getElementById(draggedId);
    const container = e.currentTarget;
    
    if (draggedElement) {
        if (container.classList.contains('playlist')) {
            // Ensure order number exists when dropping into playlist
            if (!draggedElement.querySelector('.order-number')) {
                const orderSpan = document.createElement('span');
                orderSpan.className = 'order-number';
                draggedElement.insertBefore(orderSpan, draggedElement.firstChild);
            }
            
            draggedElement.querySelector('button').outerHTML = `
                <button class="remove-btn" onclick="removeFromPlaylist(${draggedElement.id.split('-')[1]})">Remove</button>
            `;
            draggedElement.id = `playlist-${draggedElement.id.split('-')[1]}`;
        } else if (container.classList.contains('bench')) {
            // Remove order number when moving to bench
            const orderNumber = draggedElement.querySelector('.order-number');
            if (orderNumber) {
                orderNumber.remove();
            }
            
            draggedElement.querySelector('button').outerHTML = `
                <button class="add-btn" onclick="addToPlaylist(${draggedElement.id.split('-')[1]})">Add</button>
            `;
            draggedElement.id = `bench-${draggedElement.id.split('-')[1]}`;
        }
        updateOrderNumbers();
        savePlaylist();
    }
}

function updateOrderNumbers() {
    const playlistRows = document.querySelectorAll('.playlist .photo-row');
    playlistRows.forEach((row, index) => {
        let orderNumber = row.querySelector('.order-number');
        if (!orderNumber) {
            orderNumber = document.createElement('span');
            orderNumber.className = 'order-number';
            row.insertBefore(orderNumber, row.firstChild);
        }
        orderNumber.textContent = index + 1;
    });
}

function removeFromPlaylist(photoId) {
    const row = document.getElementById(`playlist-${photoId}`);
    if (row) {
        // Remove the order number before moving to bench
        const orderNumber = row.querySelector('.order-number');
        if (orderNumber) {
            orderNumber.remove();
        }

        document.querySelector('.bench').appendChild(row);
        row.id = `bench-${photoId}`;
        row.querySelector('.remove-btn').outerHTML = `
            <button class="add-btn" onclick="addToPlaylist(${photoId})">Add</button>
        `;
        updateOrderNumbers();
        // Autosave after removal
        savePlaylist();
    }
}

function addToPlaylist(photoId) {
    const row = document.getElementById(`bench-${photoId}`);
    if (row) {
        const playlistContainer = document.querySelector('.playlist');
        const newRow = row.cloneNode(true); // Clone the row instead of moving it
        
        // Update the new row's ID and button
        newRow.id = `playlist-${photoId}`;
        newRow.querySelector('.add-btn').outerHTML = `
            <button class="remove-btn" onclick="removeFromPlaylist(${photoId})">Remove</button>
        `;
        
        // Add order number if it doesn't exist
        if (!newRow.querySelector('.order-number')) {
            const orderSpan = document.createElement('span');
            orderSpan.className = 'order-number';
            newRow.insertBefore(orderSpan, newRow.firstChild);
        }
        
        // Add to playlist and hide bench item
        playlistContainer.appendChild(newRow);
        row.style.display = 'none';
        
        // Make the new row draggable
        newRow.draggable = true;
        newRow.addEventListener('dragstart', handleDragStart);
        newRow.addEventListener('dragend', handleDragEnd);
        
        updateOrderNumbers();
        savePlaylist(); // Ensure playlist is saved after adding
    }
}

// Modified savePlaylist function to work silently
function savePlaylist() {
    const playlistRows = document.querySelectorAll('.playlist .photo-row');
    const photoIds = Array.from(playlistRows).map(row => 
        row.id.replace('playlist-', '')
    );
    
    // Add visual feedback that saving is in progress
    const playlistContainer = document.querySelector('.playlist');
    playlistContainer.style.opacity = '0.7';
    
    fetch(`/frames/${frameId}/playlist`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            photo_ids: photoIds.join(',')
        })
    })
    .then(response => response.json())
    .then(data => {
        // Restore opacity when save is complete
        playlistContainer.style.opacity = '1';
        if (!data.message) {
            console.error('Error saving playlist');
        }
    })
    .catch(error => {
        // Restore opacity and show error
        playlistContainer.style.opacity = '1';
        console.error('Error saving playlist:', error);
    });
}

document.addEventListener('DOMContentLoaded', () => {
    initDragAndDrop();
    // Remove the save button since we're autosaving
    const saveBtn = document.querySelector('.save-btn');
    if (saveBtn) {
        saveBtn.remove();
    }
}); 