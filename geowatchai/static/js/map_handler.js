class MapHandler {
    constructor(containerId, options) {
        this.containerId = containerId;
        this.options = options;
        this.map = null;
        this.draw = null;
        this._onSiteClick = null;
        this.init();
    }

    init() {
        this.initializeMap();
    }

    initializeMap() {
        this.map = new maplibregl.Map({
            container: this.containerId,
            style: 'https://tiles.openfreemap.org/styles/liberty',
            center: this.options.center || [-1.6244, 6.6885],
            zoom: this.options.zoom || 8,
            zoomControl: true
        });

        this.map.on('load', () => {
            this.setupDrawingTools();
            this.setupMapLayers();
            this.setupClickHandlers();
            this.setupKeyboardControls();
            console.log("Map fully loaded and layers initialized.");
        });

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
        console.log('setupDrawingTools called');
        console.log('MapboxDraw available:', typeof MapboxDraw !== 'undefined');
        
        if (typeof MapboxDraw === 'undefined') {
            console.warn('MapboxDraw not loaded - drawing tools disabled');
            return;
        }
        
        console.log('Initializing MapboxDraw...');
        this.draw = new MapboxDraw({
            displayControlsDefault: false,
            controls: { polygon: true, trash: true }
        });
        console.log('MapboxDraw initialized:', this.draw);
        
        this.map.addControl(this.draw);
        console.log('MapboxDraw added to map');
        
        this.map.on('draw.create', (e) => this.handleAOICreated(e));
        this.map.on('draw.update', (e) => this.handleAOICreated(e));
        console.log('Drawing event listeners added');
    }

    setupMapLayers() {
        // --- Legal concessions layer (green outline, semi-transparent fill) ---
        this.map.addSource('concessions', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
        this.map.addLayer({
            id: 'concessions-fill',
            type: 'fill',
            source: 'concessions',
            paint: { 'fill-color': '#22c55e', 'fill-opacity': 0.08 }
        });
        this.map.addLayer({
            id: 'concessions-outline',
            type: 'line',
            source: 'concessions',
            paint: { 'line-color': '#16a34a', 'line-width': 1.2, 'line-dasharray': [3, 2] }
        });

        // Fetch and populate concessions
        fetch('/api/concessions/', { credentials: 'include' })
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.features) {
                    this.map.getSource('concessions').setData(data);
                    console.log('[Map] Loaded', data.features.length, 'legal concessions');
                }
            })
            .catch(e => console.warn('[Map] Could not load concessions:', e));

        // --- Detection layers ---
        this.map.addSource('detections', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
        this.map.addLayer({
            id: 'detections-layer',
            type: 'fill',
            source: 'detections',
            paint: { 'fill-color': '#ef4444', 'fill-opacity': 0.45 }
        });
        this.map.addLayer({
            id: 'detections-outline',
            type: 'line',
            source: 'detections',
            paint: { 'line-color': '#b91c1c', 'line-width': 1.5 }
        });
        this.map.addLayer({
            id: 'detections-hover',
            type: 'fill',
            source: 'detections',
            paint: { 'fill-color': '#f97316', 'fill-opacity': 0.6 },
            filter: ['==', ['get', 'id'], '']
        });
    }

    setupClickHandlers() {
        this.map.on('mouseenter', 'detections-layer', () => {
            this.map.getCanvas().style.cursor = 'pointer';
        });
        this.map.on('mouseleave', 'detections-layer', () => {
            this.map.getCanvas().style.cursor = '';
            this.map.setFilter('detections-hover', ['==', ['get', 'id'], '']);
        });
        this.map.on('mousemove', 'detections-layer', (e) => {
            if (e.features.length > 0) {
                const id = e.features[0].properties.id || '';
                this.map.setFilter('detections-hover', ['==', ['get', 'id'], id]);
            }
        });
        this.map.on('click', 'detections-layer', (e) => {
            if (e.features.length === 0) return;
            const props = e.features[0].properties;
            const siteId = props.site_id || props.id || null;
            if (siteId && typeof this._onSiteClick === 'function') {
                this._onSiteClick(siteId);
            }
        });
    }

    onSiteClick(callback) {
        this._onSiteClick = callback;
    }

    toggleLayer(layerId) {
        const layerMap = {
            'detections': ['detections-layer', 'detections-outline'],
            'concessions': ['concessions-fill', 'concessions-outline'],
        };
        const layers = layerMap[layerId] || [layerId];
        layers.forEach(id => {
            if (this.map.getLayer(id)) {
                const vis = this.map.getLayoutProperty(id, 'visibility');
                this.map.setLayoutProperty(id, 'visibility', vis === 'none' ? 'visible' : 'none');
            }
        });
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

        const dateElement = document.getElementById('hls-date');
        const dateString = dateElement ? dateElement.textContent.trim() : null;
        if (!dateString) throw new Error("Date not found in UI");

        const endDate = dateString;
        const startDate = new Date(dateString + 'T00:00:00Z');
        startDate.setDate(startDate.getDate() - 30);
        const formattedStartDate = startDate.toISOString().split('T')[0];

        const scanBtn = document.getElementById('scan-aoi-btn');
        if (scanBtn) { scanBtn.textContent = 'Processing...'; scanBtn.disabled = true; }

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
            if (scanBtn) { scanBtn.textContent = 'Scan AOI'; scanBtn.disabled = false; }
        }
    }

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

    toggleDrawingMode() {
        console.log('toggleDrawingMode called');
        console.log('draw object:', this.draw);
        console.log('MapboxDraw available:', typeof MapboxDraw !== 'undefined');
        
        if (!this.draw) {
            console.warn('Drawing tools not available - draw object not initialized');
            // Try to initialize drawing tools
            this.setupDrawingTools();
            if (!this.draw) {
                console.error('Failed to initialize drawing tools');
                return;
            }
        }
        
        const drawBtns = document.querySelectorAll('#draw-aoi-btn');
        console.log('Found draw buttons:', drawBtns.length);
        
        // Toggle between drawing and navigation modes
        if (this.draw.getMode() === 'draw_polygon') {
            this.draw.changeMode('simple_select');
            drawBtns.forEach(btn => {
                if (btn) {
                    btn.textContent = btn.textContent.includes('AOI') ? 'Draw AOI' : 'Draw Area';
                    btn.classList.remove('bg-red-600');
                    btn.classList.add('bg-sidebar-green');
                }
            });
        } else {
            this.draw.changeMode('draw_polygon');
            drawBtns.forEach(btn => {
                if (btn) {
                    btn.textContent = btn.textContent.includes('AOI') ? 'Stop Drawing' : 'Stop Drawing';
                    btn.classList.remove('bg-sidebar-green');
                    btn.classList.add('bg-red-600');
                }
            });
        }
    }

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

    setupKeyboardControls() {
        // Add keyboard shortcuts for zoom
        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            
            switch(e.key) {
                case '+':
                case '=':
                    e.preventDefault();
                    this.map.zoomIn();
                    break;
                case '-':
                case '_':
                    e.preventDefault();
                    this.map.zoomOut();
                    break;
            }
        });
    }
}