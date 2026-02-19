// Main JavaScript for GalamseyWatch AI
document.addEventListener('DOMContentLoaded', function() {
    // Initialize navigation
    initializeNavigation();
    
    // Initialize mobile menu toggle
    initializeMobileMenu();
    
    // Initialize notification system
    initializeNotifications();
    
    // Initialize form validation
    initializeFormValidation();
});

/**
 * Navigation functionality
 */
function initializeNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    
    navItems.forEach(item => {
        item.addEventListener('click', function(e) {
            // Remove active state from all items
            navItems.forEach(navItem => {
                navItem.classList.remove('bg-active-gold', 'text-white');
                navItem.classList.add('text-gray-300');
            });
            
            // Add active state to clicked item
            this.classList.add('bg-active-gold', 'text-white');
            this.classList.remove('text-gray-300');
        });
    });
}

/**
 * Mobile menu functionality
 */
function initializeMobileMenu() {
    const mobileMenuButton = document.getElementById('mobile-menu-button');
    const sidebar = document.querySelector('aside');
    
    if (mobileMenuButton && sidebar) {
        mobileMenuButton.addEventListener('click', function() {
            sidebar.classList.toggle('open');
        });
        
        // Close menu when clicking outside
        document.addEventListener('click', function(e) {
            if (!sidebar.contains(e.target) && !mobileMenuButton.contains(e.target)) {
                sidebar.classList.remove('open');
            }
        });
    }
}

/**
 * Notification system
 */
function initializeNotifications() {
    const notificationButton = document.querySelector('[aria-label="Notifications"]');
    const notificationBadge = document.querySelector('.notification-badge');
    
    if (notificationButton) {
        notificationButton.addEventListener('click', function() {
            // Placeholder for notification panel
            showNotificationPanel();
        });
    }
}

/**
 * Form validation
 */
function initializeFormValidation() {
    const forms = document.querySelectorAll('form');
    
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            if (!validateForm(form)) {
                e.preventDefault();
            }
        });
    });
}

/**
 * Validate form inputs
 */
function validateForm(form) {
    const requiredFields = form.querySelectorAll('[required]');
    let isValid = true;
    
    requiredFields.forEach(field => {
        if (!field.value.trim()) {
            showFieldError(field, 'This field is required');
            isValid = false;
        } else {
            clearFieldError(field);
        }
    });
    
    return isValid;
}

/**
 * Show field error
 */
function showFieldError(field, message) {
    field.classList.add('border-red-500');
    
    // Remove existing error message
    const existingError = field.parentNode.querySelector('.field-error');
    if (existingError) {
        existingError.remove();
    }
    
    // Add error message
    const errorElement = document.createElement('div');
    errorElement.className = 'field-error text-red-500 text-sm mt-1';
    errorElement.textContent = message;
    field.parentNode.appendChild(errorElement);
}

/**
 * Clear field error
 */
function clearFieldError(field) {
    field.classList.remove('border-red-500');
    const errorElement = field.parentNode.querySelector('.field-error');
    if (errorElement) {
        errorElement.remove();
    }
}

/**
 * Show notification panel (placeholder)
 */
function showNotificationPanel() {
    // This would be implemented to show a notification panel
    console.log('Notification panel clicked');
}

/**
 * Loading state management
 */
function showLoading(element) {
    element.classList.add('loading');
    element.disabled = true;
}

function hideLoading(element) {
    element.classList.remove('loading');
    element.disabled = false;
}

/**
 * AJAX request helper
 */
async function makeRequest(url, options = {}) {
    const defaultOptions = {
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken'),
        },
    };
    
    const finalOptions = { ...defaultOptions, ...options };
    
    try {
        const response = await fetch(url, finalOptions);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error('Request failed:', error);
        throw error;
    }
}

/**
 * Get CSRF token
 */
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

/**
 * Format date helper
 */
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

/**
 * Format number helper
 */
function formatNumber(num) {
    return new Intl.NumberFormat().format(num);
}

/**
 * Debounce function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * File upload helper
 */
function handleFileUpload(file, uploadUrl, progressCallback) {
    const formData = new FormData();
    formData.append('file', file);
    
    const xhr = new XMLHttpRequest();
    
    // Upload progress
    xhr.upload.addEventListener('progress', function(e) {
        if (e.lengthComputable) {
            const percentComplete = (e.loaded / e.total) * 100;
            if (progressCallback) {
                progressCallback(percentComplete);
            }
        }
    });
    
    // Request completed
    xhr.addEventListener('load', function() {
        if (xhr.status === 200) {
            console.log('Upload completed successfully');
        } else {
            console.error('Upload failed');
        }
    });
    
    xhr.open('POST', uploadUrl);
    xhr.setRequestHeader('X-CSRFToken', getCookie('csrftoken'));
    xhr.send(formData);
}

/**
 * Map helper functions
 */
const MapHelpers = {
    /**
     * Create a marker for detected sites
     */
    createDetectionMarker: function(coordinates, type, confidence) {
        const color = this.getDetectionColor(type);
        return {
            type: 'Feature',
            geometry: {
                type: 'Point',
                coordinates: coordinates
            },
            properties: {
                type: type,
                confidence: confidence,
                color: color
            }
        };
    },
    
    /**
     * Get color based on detection type
     */
    getDetectionColor: function(type) {
        const colors = {
            'critical': '#ef4444',
            'high': '#f97316',
            'moderate': '#eab308',
            'legal': '#3b82f6',
            'conflict': '#8b5cf6'
        };
        return colors[type] || '#6b7280';
    },
    
    /**
     * Format coordinates for display
     */
    formatCoordinates: function(lng, lat) {
        return `${lat.toFixed(4)}°N, ${Math.abs(lng).toFixed(4)}°W`;
    }
};

/**
 * Chart helper functions
 */
const ChartHelpers = {
    /**
     * Create default chart options
     */
    getDefaultOptions: function() {
        return {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                },
                tooltip: {
                    backgroundColor: 'rgba(0, 0, 0, 0.8)',
                    padding: 12,
                    cornerRadius: 4,
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: {
                        color: 'rgba(0, 0, 0, 0.05)'
                    }
                },
                x: {
                    grid: {
                        display: false
                    }
                }
            }
        };
    },
    
    /**
     * Create line chart
     */
    createLineChart: function(ctx, data, options = {}) {
        const defaultOptions = this.getDefaultOptions();
        return new Chart(ctx, {
            type: 'line',
            data: data,
            options: { ...defaultOptions, ...options }
        });
    },
    
    /**
     * Create bar chart
     */
    createBarChart: function(ctx, data, options = {}) {
        const defaultOptions = this.getDefaultOptions();
        return new Chart(ctx, {
            type: 'bar',
            data: data,
            options: { ...defaultOptions, ...options }
        });
    }
};

// Export helpers for use in other files
window.GalamseyWatch = {
    MapHelpers,
    ChartHelpers,
    makeRequest,
    showLoading,
    hideLoading,
    formatDate,
    formatNumber,
    handleFileUpload
};
