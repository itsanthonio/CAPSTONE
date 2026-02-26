class MapHandler {
    constructor(containerId, options) {
        this.containerId = containerId;
        this.options = options;
        this.map = null;
        this.draw = null;
        this.isDrawing = false;
        this.isPolling = false; // Add polling state tracker
        this.pollingTimeout = null; // Add polling timeout tracker
        this.pollingCacheBuster = 0; // Add polling cache buster
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
            if (this.options.drawing === true) this.setupDrawingTools();
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
            displayControlsDefault: false
        });
        console.log('MapboxDraw initialized:', this.draw);

        this.map.addControl(this.draw);
        console.log('MapboxDraw added to map');

        this.map.on('draw.create', (e) => this.handleAOICreated(e));
        this.map.on('draw.update', (e) => this.handleAOICreated(e));
        console.log('Drawing event listeners added');

        this._drawClickCount = 0;
        this._drawCoords = [];

        this.map.on('click', (e) => {
            if (!this.draw || this.draw.getMode() !== 'draw_polygon') return;
            this._drawClickCount++;
            this._drawCoords.push([e.lngLat.lng, e.lngLat.lat]);

            // Auto-close after 4 clicks
            if (this._drawClickCount >= 4) {
                this._finalizePolygon();
            }
        });

        // Intercept Enter on keyup in capture phase — fires before MapboxDraw's canvas
        // handler so we can stopPropagation and handle the finalization ourselves.
        this._drawKeyHandler = (e) => {
            if (e.key !== 'Enter') return;
            if (!this.draw || this.draw.getMode() !== 'draw_polygon') return;
            if (this._drawCoords.length < 3) return;
            e.stopPropagation(); // prevent MapboxDraw from silently cancelling the draw
            this._finalizePolygon();
        };
        document.addEventListener('keyup', this._drawKeyHandler, true); // capture phase
    }

    _finalizePolygon() {
        const ring = [...this._drawCoords, this._drawCoords[0]];
        this._drawClickCount = 0;
        this._drawCoords = [];

        setTimeout(() => {
            this.draw.deleteAll();
            const ids = this.draw.add({
                type: 'Feature',
                geometry: { type: 'Polygon', coordinates: [ring] }
            });
            this.draw.changeMode('simple_select', { featureIds: ids });
            this.handleAOICreated({ features: this.draw.getAll().features });

            document.querySelectorAll('#draw-aoi-btn').forEach(btn => {
                btn.textContent = 'Draw Area';
                btn.classList.remove('bg-red-600');
                btn.classList.add('bg-sidebar-green');
            });
        }, 50);
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

        // --- Water bodies layer (blue) ---
        this.map.addSource('water-bodies', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
        this.map.addLayer({
            id: 'water-bodies-fill',
            type: 'fill',
            source: 'water-bodies',
            paint: { 'fill-color': '#3b82f6', 'fill-opacity': 0.15 }
        });
        this.map.addLayer({
            id: 'water-bodies-outline',
            type: 'line',
            source: 'water-bodies',
            paint: { 'line-color': '#1d4ed8', 'line-width': 1.5 }
        });

        fetch('/api/regions/?type=water_body', { credentials: 'include' })
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.features) {
                    this.map.getSource('water-bodies').setData(data);
                    console.log('[Map] Loaded', data.features.length, 'water body regions');
                }
            })
            .catch(e => console.warn('[Map] Could not load water bodies:', e));

        // --- Protected forests layer (dark green) ---
        this.map.addSource('protected-forests', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
        this.map.addLayer({
            id: 'protected-forests-fill',
            type: 'fill',
            source: 'protected-forests',
            paint: { 'fill-color': '#15803d', 'fill-opacity': 0.12 }
        });
        this.map.addLayer({
            id: 'protected-forests-outline',
            type: 'line',
            source: 'protected-forests',
            paint: { 'line-color': '#14532d', 'line-width': 1.5, 'line-dasharray': [4, 2] }
        });

        fetch('/api/regions/?type=protected_forest', { credentials: 'include' })
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.features) {
                    this.map.getSource('protected-forests').setData(data);
                    console.log('[Map] Loaded', data.features.length, 'protected forest regions');
                }
            })
            .catch(e => console.warn('[Map] Could not load protected forests:', e));

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

        // --- Detection layers --- (must be added BEFORE the sites fetch so setData works)
        this.map.addSource('detections', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
        this.map.addLayer({
            id: 'detections-layer',
            type: 'fill',
            source: 'detections',
            paint: {
                'fill-color': [
                    'match', ['get', 'legal_status'],
                    'legal', '#3b82f6',
                    '#ef4444'  // illegal or unknown
                ],
                'fill-opacity': 0.45
            }
        });
        this.map.addLayer({
            id: 'detections-outline',
            type: 'line',
            source: 'detections',
            paint: {
                'line-color': [
                    'match', ['get', 'legal_status'],
                    'legal', '#1d4ed8',
                    '#b91c1c'  // illegal or unknown
                ],
                'line-width': 1.5
            }
        });
        this.map.addLayer({
            id: 'detections-hover',
            type: 'fill',
            source: 'detections',
            paint: { 'fill-color': '#f97316', 'fill-opacity': 0.6 },
            filter: ['==', ['get', 'site_id'], '']
        });

        // Load existing detections from DB — runs AFTER source is added so setData works
        fetch('/api/sites/', { credentials: 'include' })
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                const features = (data && data.features) ? data.features : [];
                if (features.length > 0) {
                    this.map.getSource('detections').setData(data);
                }
                console.log('[Map] Loaded', features.length, 'existing detections');
                if (typeof this._onDetectionsUpdated === 'function') {
                    this._onDetectionsUpdated(features, { source: 'load' });
                }
            })
            .catch(e => {
                console.warn('[Map] Could not load existing detections:', e);
                if (typeof this._onDetectionsUpdated === 'function') {
                    this._onDetectionsUpdated([], { source: 'load' });
                }
            });
    }

    setupClickHandlers() {
        this.map.on('mouseenter', 'detections-layer', () => {
            this.map.getCanvas().style.cursor = 'pointer';
        });
        this.map.on('mouseleave', 'detections-layer', () => {
            this.map.getCanvas().style.cursor = '';
            this.map.setFilter('detections-hover', ['==', ['get', 'site_id'], '']);
        });
        this.map.on('mousemove', 'detections-layer', (e) => {
            if (e.features.length > 0) {
                const siteId = e.features[0].properties.site_id || '';
                this.map.setFilter('detections-hover', ['==', ['get', 'site_id'], siteId]);
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

    onDetectionsUpdated(callback) {
        this._onDetectionsUpdated = callback;
    }

    toggleLayer(layerId) {
        const layerMap = {
            'detections': ['detections-layer', 'detections-outline'],
            'concessions': ['concessions-fill', 'concessions-outline'],
            'water-bodies': ['water-bodies-fill', 'water-bodies-outline'],
            'protected-forests': ['protected-forests-fill', 'protected-forests-outline'],
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
        if (data.features.length === 0) return;

        // Reset draw button regardless of how the polygon was completed
        // (4-click auto-close, Enter key, or MapboxDraw double-click)
        document.querySelectorAll('#draw-aoi-btn').forEach(btn => {
            btn.textContent = 'Draw Area';
            btn.classList.remove('bg-red-600');
            btn.classList.add('bg-sidebar-green');
        });

        const feature = data.features[0];
        const area    = turf.area(feature);
        const ha      = area / 10000;

        document.getElementById('aoi-scan-section').classList.remove('hidden');
        document.getElementById('aoi-area').textContent = ha.toFixed(2);

        const scanBtn   = document.getElementById('scan-aoi-btn');
        const warningEl = document.getElementById('aoi-warning');

        if (ha < 100) {
            if (warningEl) {
                warningEl.textContent = `Area too small (${ha.toFixed(2)} ha). Minimum is 100 ha.`;
                warningEl.classList.remove('hidden');
            }
            if (scanBtn) {
                scanBtn.disabled = true;
                scanBtn.classList.add('opacity-50', 'cursor-not-allowed');
                scanBtn.classList.remove('hover:bg-opacity-90');
            }
        } else if (ha > 1000) {
            if (warningEl) {
                warningEl.textContent = `Area too large (${ha.toFixed(2)} ha). Maximum is 1,000 ha.`;
                warningEl.classList.remove('hidden');
            }
            if (scanBtn) {
                scanBtn.disabled = true;
                scanBtn.classList.add('opacity-50', 'cursor-not-allowed');
                scanBtn.classList.remove('hover:bg-opacity-90');
            }
        } else {
            if (warningEl) warningEl.classList.add('hidden');
            if (scanBtn) {
                scanBtn.disabled = false;
                scanBtn.classList.remove('opacity-50', 'cursor-not-allowed');
                scanBtn.classList.add('hover:bg-opacity-90');
            }
        }
    }


    async scanAOI() {
        // Strong debounce protection - prevent multiple simultaneous scans
        if (this.isPolling) {
            console.log("⚠️ Scan already in progress, ignoring duplicate request");
            alert("Scan already in progress. Please wait for current scan to complete.");
            return;
        }

        const data = this.draw.getAll();
        if (data.features.length === 0) {
            alert('Please draw an area on the map before scanning.');
            return;
        }

        const area = turf.area(data.features[0]);
        const ha   = area / 10000;
        if (ha < 100) {
            alert(`Area too small (${ha.toFixed(2)} ha). Please draw an area of at least 100 hectares.`);
            return;
        }
        if (ha > 1000) {
            alert(`Area too large (${ha.toFixed(2)} ha). Please draw an area no greater than 1,000 hectares.`);
            return;
        }

        const dateElement = document.getElementById('hls-date');
        const dateString = dateElement ? dateElement.textContent.trim() : null;
        // Fall back to today when the hls-date element isn't present on this page
        const endDate = dateString || new Date().toISOString().split('T')[0];
        const startDate = new Date(endDate + 'T00:00:00Z');
        startDate.setDate(startDate.getDate() - 30);
        const formattedStartDate = startDate.toISOString().split('T')[0];

        const scanBtn = document.getElementById('scan-aoi-btn');
        if (scanBtn) { 
            scanBtn.textContent = 'Starting scan...'; 
            scanBtn.disabled = true;
            scanBtn.style.opacity = '0.6';
            scanBtn.style.cursor = 'not-allowed';
        }

        this.isPolling = true; // Set BEFORE fetch so re-entrant calls are blocked immediately
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
            this.isPolling = false; // Release lock on error so user can retry
            console.error("Failed to start job:", err);
            alert("Error starting scan: Ensure you are logged in and CSRF is valid.");
        } finally {
            if (scanBtn) { scanBtn.textContent = 'Scan AOI'; scanBtn.disabled = false; }
        }
    }

    pollJobStatus(jobId) {
        console.log("Starting job status poll at:", new Date().toISOString());
        console.log("Polling status for job:", jobId);
        let pollCount = 0;
        const maxPolls = 200; // Max 10 minutes of polling (200 * 3 seconds)
        
        const interval = setInterval(async () => {
            pollCount++;
            if (pollCount > maxPolls) {
                clearInterval(interval);
                console.error("Job polling timed out after 10 minutes");
                alert("Scan timed out. The job may be taking longer than expected or may have failed.");
                return;
            }
            
            try {
                const res = await fetch(`/api/jobs/${jobId}/?t=${new Date().getTime()}`);
                if (!res.ok) {
                    throw new Error(`Server responded with ${res.status}`);
                }
                const status = await res.json();
                console.log("Full API Response:", status);
                console.log("Current Backend Status:", status.status);
                console.log("Poll count:", pollCount, "of", maxPolls);
                
                // Force direct check on status.status field
                if (status.status === 'completed') {
                    clearInterval(interval);
                    console.log("Job completed - forcing immediate success handling");
                    this.handleJobSuccess(status);
                    return;
                }
                
                if (status.status === 'failed') {
                    clearInterval(interval);
                    this.handleJobFailure(status);
                    return;
                }
                
                // Handle all non-final states - continue polling
                const processingStates = ['queued', 'validating', 'exporting', 'preprocessing', 'inferring', 'postprocessing', 'storing'];
                if (processingStates.includes(status.status)) {
                    console.log("Job still processing at poll count:", pollCount, "Status:", status.status);
                    // Update scan button to show progress
                    const scanBtn = document.getElementById('scan-aoi-btn');
                    if (scanBtn) {
                        scanBtn.textContent = `Scanning... (${pollCount * 3}s) - ${status.status}`;
                        scanBtn.disabled = true;
                    }
                } else {
                    console.warn("Unknown job status at poll count:", pollCount, "Status:", status.status);
                    console.warn("Available fields:", Object.keys(status));
                }
            } catch (e) {
                clearInterval(interval);
                this.isPolling = false; // Reset polling state
                console.error("Error polling job status at poll count:", pollCount);
                console.error("Fetch error:", e);
                alert("Error checking scan status: " + e.message);
            }
        }, 3000);
    }

    handleJobSuccess(data) {
        this.isPolling = false; // Reset polling state
        console.log("Job completed successfully!");
        console.log("Job result data:", data.result);
        console.log("Job total_detections:", data.total_detections);
        console.log("Job detection_data:", data.detection_data);
        
        // Reset scan button
        const scanBtn = document.getElementById('scan-aoi-btn');
        if (scanBtn) {
            scanBtn.textContent = 'Scan AOI';
            scanBtn.disabled = false;
            scanBtn.style.opacity = '1';
            scanBtn.style.cursor = 'pointer';
        }
        
        // Show alert based on job result
        const fc = data.detection_data;
        const newCount = (fc && fc.features) ? fc.features.length : 0;
        const illegal = data.illegal_count || 0;
        if (newCount > 0) {
            alert(`Scan complete: ${newCount} site(s) detected (${illegal} illegal).`);
        } else {
            alert("Scan complete: No mining sites detected in this area.");
        }

        // Re-fetch all sites so the sidebar shows everything (not just the new scan)
        fetch('/api/sites/', { credentials: 'include' })
            .then(r => r.ok ? r.json() : null)
            .then(allData => {
                const allFeatures = (allData && allData.features) ? allData.features : [];
                this.map.getSource('detections').setData(allData || { type: 'FeatureCollection', features: [] });
                if (typeof this._onDetectionsUpdated === 'function') {
                    this._onDetectionsUpdated(allFeatures, { source: 'scan' });
                }
            })
            .catch(() => {
                // Fallback: show at least the new scan's features
                if (fc && fc.features && fc.features.length > 0) {
                    this.map.getSource('detections').setData(fc);
                    if (typeof this._onDetectionsUpdated === 'function') {
                        this._onDetectionsUpdated(fc.features, { source: 'scan' });
                    }
                }
            });
    }

    handleJobFailure(data) {
        this.isPolling = false; // Reset polling state
        console.error("Job failed:", data);
        
        // Reset scan button
        const scanBtn = document.getElementById('scan-aoi-btn');
        if (scanBtn) {
            scanBtn.textContent = 'Scan AOI';
            scanBtn.disabled = false;
            scanBtn.style.opacity = '1';
            scanBtn.style.cursor = 'pointer';
        }
        
        alert("Scan failed: " + (data.error || data.failure_reason || 'Unknown error occurred'));
    }

    updateConfidenceFilter(val) {
        if (this.map.getLayer('detections-layer')) {
            this.map.setFilter('detections-layer', ['>=', ['get', 'confidence_score'], parseFloat(val)]);
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
            this._drawClickCount = 0;
            this._drawCoords = [];
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
                    this.zoomIn();
                    break;
                case '-':
                case '_':
                    e.preventDefault();
                    this.zoomOut();
                    break;
            }
        });
    }

    zoomIn() {
        if (this.map) {
            this.map.zoomIn();
        }
    }

    zoomOut() {
        if (this.map) {
            this.map.zoomOut();
        }
    }

    startSitePolling(intervalMs = 60000) {
        // Polls /api/sites/ and silently updates the map + sidebar if the
        // detection count changes (e.g. another admin ran a scan).
        let knownCount = null;
        this.sitePollingInterval = setInterval(async () => {
            if (this.isPolling) return;  // skip if a job scan is in progress
            try {
                const r = await fetch('/api/sites/', { credentials: 'include' });
                if (!r.ok) return;
                const data = await r.json();
                const features = data.features || [];
                if (knownCount !== null && features.length !== knownCount) {
                    this.map.getSource('detections').setData(data);
                    if (typeof this._onDetectionsUpdated === 'function') {
                        this._onDetectionsUpdated(features, { source: 'poll' });
                    }
                }
                knownCount = features.length;
            } catch (_) {}
        }, intervalMs);
    }
}