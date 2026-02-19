class MapHandler {
    constructor(containerId, options) {
        this.containerId = containerId;
        this.options = options;
        this.map = null;
        this.draw = null;
        this.init();
    }

    init() {
        this.initializeMap();
    }

    initializeMap() {
        this.map = new maplibregl.Map({
            container: this.containerId,
            style: 'https://demotiles.maplibre.org/style.json',
            center: this.options.center || [-1.6244, 6.6885],
            zoom: this.options.zoom || 10
        });

        this.map.on('load', () => {
            this.setupDrawingTools();
            this.setupMapLayers();
            console.log("Map fully loaded and layers initialized.");
        });

        // This fixes the "updateMapCoordinates is not a function" error
        this.map.on('move', () => {
            this.updateMapCoordinates();
        });
    }

    updateMapCoordinates() {
        const center = this.map.getCenter();
        const latDisp = document.getElementById('lat-display');
        const lngDisp = document.getElementById('lng-display');
        if (latDisp && lngDisp) {
            latDisp.textContent = center.lat.toFixed(4);
            lngDisp.textContent = center.lng.toFixed(4);
        }
    }

    setupDrawingTools() {
        this.draw = new MapboxDraw({
            displayControlsDefault: false,
            controls: { polygon: true, trash: true }
        });
        this.map.addControl(this.draw);

        this.map.on('draw.create', (e) => this.handleAOICreated(e));
        this.map.on('draw.update', (e) => this.handleAOICreated(e));
    }

    setupMapLayers() {
        this.map.addSource('detections', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
        this.map.addLayer({
            id: 'detections-layer',
            type: 'fill',
            source: 'detections',
            paint: { 'fill-color': '#ef4444', 'fill-opacity': 0.5 }
        });
    }

    // This fixes "toggleLayer is not a function"
    toggleLayer(layerId) {
        const fullLayerId = layerId === 'detections' ? 'detections-layer' : layerId;
        if (this.map.getLayer(fullLayerId)) {
            const visibility = this.map.getLayoutProperty(fullLayerId, 'visibility');
            this.map.setLayoutProperty(fullLayerId, 'visibility', visibility === 'none' ? 'visible' : 'none');
        }
    }

    handleAOICreated(e) {
        const data = this.draw.getAll();
        if (data.features.length > 0) {
            const area = turf.area(data);
            const hectares = (area / 10000).toFixed(2);
            document.getElementById('aoi-scan-section').classList.remove('hidden');
            document.getElementById('aoi-area').textContent = hectares;
        }
    }

    async scanAOI() {
        const data = this.draw.getAll();
        if (data.features.length === 0) return;

        // Get dates from UI
        const dateElement = document.getElementById('hls-date');
        const dateString = dateElement ? dateElement.textContent.trim() : null;
        
        // Validate date exists
        if (!dateString) {
            throw new Error("Date not found in UI");
        }
        
        // Use date string directly for both start_date and end_date
        // Backend expects YYYY-MM-DD format
        const endDate = dateString;
        const startDate = new Date(dateString + 'T00:00:00Z');
        startDate.setDate(startDate.getDate() - 30);
        const formattedStartDate = startDate.toISOString().split('T')[0];

        // Show processing state
        const scanBtn = document.getElementById('scan-aoi-btn');
        if (scanBtn) {
            scanBtn.textContent = 'Processing...';
            scanBtn.disabled = true;
        }

        try {
            const response = await fetch('/api/jobs/', {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCookie('csrftoken') 
                },
                body: JSON.stringify({
                    aoi_geometry: data.features[0].geometry,
                    start_date: formattedStartDate,
                    end_date: endDate,
                    model_version: 'v1.0'
                })
            });

            if (!response.ok) throw new Error('Server responded with ' + response.status);
            
            const job = await response.json();
            this.pollJobStatus(job.id);
        } catch (err) {
            console.error("Failed to start job:", err);
            alert("Error starting scan: Ensure you are logged in and CSRF is valid.");
        } finally {
            // Reset button state
            if (scanBtn) {
                scanBtn.textContent = 'Scan AOI';
                scanBtn.disabled = false;
            }
        }
    }

    // This fixes "pollJobStatus is not a function"
    pollJobStatus(jobId) {
        console.log("Polling status for job:", jobId);
        const interval = setInterval(async () => {
            try {
                const res = await fetch(`/api/jobs/${jobId}/`);
                const status = await res.json();
                if (status.state === 'SUCCESS') {
                    clearInterval(interval);
                    this.map.getSource('detections').setData(status.result);
                }
            } catch (e) {
                clearInterval(interval);
            }
        }, 3000);
    }

    updateConfidenceFilter(val) {
        if (this.map.getLayer('detections-layer')) {
            this.map.setFilter('detections-layer', ['>=', ['get', 'confidence'], parseFloat(val)]);
        }
    }

    // Essential for fixing the 403 Forbidden error
    getCookie(name) {
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
}