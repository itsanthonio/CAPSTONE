class MapHandler {
    constructor(containerId, options) {
        this.containerId = containerId;
        this.options = options;
        this.map = null;
        this.draw = null;
        this.isDrawing = false;
        this.isPolling = false;
        this.pollingTimeout = null;
        this.pollingCacheBuster = 0;
        this._batchJobs = new Map();   // jobId → {name,geometry,status,elapsed,pollCount}
        this._focusedJobId = null;
        this._batchPollInterval = null;
        this.init();
    }

    init() {
        this.initializeMap();
    }


    initializeMap() {
        this.map = new maplibregl.Map({
            container: this.containerId,
            style: {
                version: 8,
                glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
                sources: {
                    'google-satellite': {
                        type: 'raster',
                        tiles: [
                            'https://mt0.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
                            'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
                            'https://mt2.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
                            'https://mt3.google.com/vt/lyrs=s&x={x}&y={y}&z={z}'
                        ],
                        tileSize: 256,
                        attribution: '© Google'
                    }
                },
                layers: [{
                    id: 'google-satellite-base',
                    type: 'raster',
                    source: 'google-satellite'
                }]
            },
            center: this.options.center || [-1.6244, 6.6885],
            zoom: this.options.zoom || 8,
        });


        this.map.on('load', () => {
            if (this.options.drawing === true) this.setupDrawingTools();
            this.setupMapLayers();
            this.setupBatchAOILayer();
            this.setupClickHandlers();
            this.setupKeyboardControls();
            this.startPulseAnimation();
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
        // --- Street labels overlay (Google hybrid roads/labels, hidden by default) ---
        this.map.addSource('google-labels', {
            type: 'raster',
            tiles: [
                'https://mt0.google.com/vt/lyrs=h&x={x}&y={y}&z={z}',
                'https://mt1.google.com/vt/lyrs=h&x={x}&y={y}&z={z}',
                'https://mt2.google.com/vt/lyrs=h&x={x}&y={y}&z={z}',
                'https://mt3.google.com/vt/lyrs=h&x={x}&y={y}&z={z}'
            ],
            tileSize: 256,
            attribution: '© Google'
        });
        // Default ON; restore persisted preference (user may have toggled it off)
        const savedLabels = localStorage.getItem('streetLabels');
        const labelsVis = savedLabels === 'none' ? 'none' : 'visible';
        this.map.addLayer({
            id: 'street-labels-layer',
            type: 'raster',
            source: 'google-labels',
            layout: { visibility: labelsVis }
        });

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

        // --- Administrative regions layer (amber outline, off by default) ---
        this.map.addSource('admin-regions', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
        this.map.addLayer({
            id: 'admin-regions-fill',
            type: 'fill',
            source: 'admin-regions',
            paint: { 'fill-color': '#f59e0b', 'fill-opacity': 0.04 },
            layout: { visibility: 'none' }
        });
        this.map.addLayer({
            id: 'admin-regions-outline',
            type: 'line',
            source: 'admin-regions',
            paint: { 'line-color': '#f59e0b', 'line-width': 1.8, 'line-dasharray': [4, 2] },
            layout: { visibility: 'none' }
        });
        this.map.addLayer({
            id: 'admin-regions-labels',
            type: 'symbol',
            source: 'admin-regions',
            layout: {
                visibility: 'none',
                'text-field': ['get', 'name'],
                'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'],
                'text-size': 13,
                'text-anchor': 'center',
                'symbol-placement': 'point',
                'text-max-width': 10,
            },
            paint: {
                'text-color': '#f59e0b',
                'text-halo-color': '#0d1117',
                'text-halo-width': 2,
            }
        });

        fetch('/api/regions/?type=district', { credentials: 'include' })
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.features)
                    this.map.getSource('admin-regions').setData(data);
            })
            .catch(e => console.warn('[Map] Could not load admin regions:', e));

        // --- Administrative districts layer (white outline, off by default) ---
        this.map.addSource('admin-districts', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
        this.map.addLayer({
            id: 'admin-districts-fill',
            type: 'fill',
            source: 'admin-districts',
            paint: { 'fill-color': '#e2e8f0', 'fill-opacity': 0.03 },
            layout: { visibility: 'none' }
        });
        this.map.addLayer({
            id: 'admin-districts-outline',
            type: 'line',
            source: 'admin-districts',
            paint: { 'line-color': '#cbd5e1', 'line-width': 0.8 },
            layout: { visibility: 'none' }
        });
        this.map.addLayer({
            id: 'admin-districts-labels',
            type: 'symbol',
            source: 'admin-districts',
            minzoom: 8,
            layout: {
                visibility: 'none',
                'text-field': ['get', 'name'],
                'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'],
                'text-size': 11,
                'text-anchor': 'center',
                'symbol-placement': 'point',
                'text-max-width': 8,
            },
            paint: {
                'text-color': '#e2e8f0',
                'text-halo-color': '#0d1117',
                'text-halo-width': 1.5,
            }
        });

        fetch('/api/regions/?type=admin_district', { credentials: 'include' })
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.features)
                    this.map.getSource('admin-districts').setData(data);
            })
            .catch(e => console.warn('[Map] Could not load admin districts:', e));

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
        // Animated glow layer (behind the crisp outline, pulsed via startPulseAnimation)
        this.map.addLayer({
            id: 'detections-glow',
            type: 'line',
            source: 'detections',
            paint: {
                'line-color': ['match', ['get', 'legal_status'], 'legal', '#3b82f6', '#ef4444'],
                'line-width': 9,
                'line-opacity': 0.2,
                'line-blur': 4
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

    // ── Batch AOI layers ──────────────────────────────────────────────────
    setupBatchAOILayer() {
        this.map.addSource('batch-aois', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
        // Semi-transparent fill
        this.map.addLayer({
            id: 'batch-aois-fill',
            type: 'fill',
            source: 'batch-aois',
            paint: {
                'fill-color': [
                    'match', ['get', 'status'],
                    'completed', '#4ade80',
                    'failed',    '#ef4444',
                    '#fbbf24'   // default: in-progress amber
                ],
                'fill-opacity': 0.08
            }
        });
        // Dashed outline
        this.map.addLayer({
            id: 'batch-aois-outline',
            type: 'line',
            source: 'batch-aois',
            paint: {
                'line-color': [
                    'match', ['get', 'status'],
                    'completed', '#4ade80',
                    'failed',    '#ef4444',
                    '#fbbf24'
                ],
                'line-width': ['case', ['==', ['get', 'focused'], true], 2.5, 1.5],
                'line-dasharray': [3, 2],
                'line-opacity': 0.85
            }
        });
        // Click to focus
        this.map.on('click', 'batch-aois-fill', (e) => {
            if (e.features.length > 0) {
                this._focusBatchJob(e.features[0].properties.jobId);
            }
        });
        this.map.on('mouseenter', 'batch-aois-fill', () => {
            this.map.getCanvas().style.cursor = 'pointer';
        });
        this.map.on('mouseleave', 'batch-aois-fill', () => {
            this.map.getCanvas().style.cursor = '';
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
            'street-labels': ['street-labels-layer'],
            'detections': ['detections-layer', 'detections-outline'],
            'concessions': ['concessions-fill', 'concessions-outline'],
            'admin-regions': ['admin-regions-fill', 'admin-regions-outline', 'admin-regions-labels'],
            'admin-districts': ['admin-districts-fill', 'admin-districts-outline', 'admin-districts-labels'],
        };
        const layers = layerMap[layerId] || [layerId];
        layers.forEach(id => {
            if (this.map.getLayer(id)) {
                const vis = this.map.getLayoutProperty(id, 'visibility');
                this.map.setLayoutProperty(id, 'visibility', vis === 'none' ? 'visible' : 'none');
            }
        });
        if (layerId === 'street-labels') {
            const newVis = this.map.getLayoutProperty('street-labels-layer', 'visibility');
            localStorage.setItem('streetLabels', newVis);
        }
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
        } else if (ha > 6000) {
            if (warningEl) {
                warningEl.textContent = `Area too large (${ha.toFixed(2)} ha). Maximum is 6000 ha.`;
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
            if (window.showToast) window.showToast('Scan already in progress — please wait', 'info');
            else alert("Scan already in progress. Please wait for current scan to complete.");
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
        if (ha > 6000) {
            alert(`Area too large (${ha.toFixed(2)} ha). Please draw an area no greater than 6000 hectares.`);
            return;
        }

        const today = new Date().toISOString().split('T')[0];
        const startInput = document.getElementById('scan-start-date');
        const endInput   = document.getElementById('scan-end-date');
        const formattedStartDate = (startInput && startInput.value) ? startInput.value : '2023-01-01';
        const endDate            = (endInput   && endInput.value)   ? endInput.value   : today;

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
            if (!response.ok) {
                let detail = '';
                try {
                    const errBody = await response.json();
                    detail = errBody.detail || errBody.error || JSON.stringify(errBody);
                } catch (_) {}
                throw new Error(`${response.status}: ${detail || response.statusText}`);
            }
            const job = await response.json();
            this.startScanSweep();
            const hud = document.getElementById('scan-hud');
            if (hud) hud.classList.remove('hidden');
            this.pollJobStatus(job.id);
        } catch (err) {
            this.isPolling = false;
            this.stopScanSweep();
            const hud = document.getElementById('scan-hud');
            if (hud) hud.classList.add('hidden');
            console.error("Failed to start job:", err);
            if (window.showToast) window.showToast('Error starting scan: ' + err.message, 'error');
            else alert(`Error starting scan: ${err.message}`);
        } finally {
            if (scanBtn) { scanBtn.textContent = 'Scan AOI'; scanBtn.disabled = false; }
        }
    }

    pollJobStatus(jobId) {
        const POLL_MS = 3000;
        const MAX_POLLS = 200; // 10 minutes at 3s
        let pollCount = 0;
        let elapsed = 0;

        const statusLabels = {
            queued:         'Queued…',
            validating:     'Validating…',
            exporting:      'Fetching imagery…',
            preprocessing:  'Processing…',
            inferring:      'Processing…',
            postprocessing: 'Processing…',
            storing:        'Saving…',
        };

        const statusEl = document.getElementById('scan-status-text');
        const scanBtn  = document.getElementById('scan-aoi-btn');

        const setStatus = (label, elapsedSec) => {
            if (statusEl) { statusEl.textContent = label; statusEl.classList.remove('hidden'); }
            const hudText    = document.getElementById('scan-hud-text');
            const hudElapsed = document.getElementById('scan-hud-elapsed');
            const hudEl      = document.getElementById('scan-hud');
            if (hudText)    hudText.textContent    = label;
            if (hudElapsed) hudElapsed.textContent = elapsedSec != null ? elapsedSec + 's' : '';
            if (hudEl)      hudEl.classList.remove('hidden');
        };

        const clearStatus = () => {
            if (statusEl) { statusEl.textContent = ''; statusEl.classList.add('hidden'); }
            const hudEl = document.getElementById('scan-hud');
            if (hudEl) hudEl.classList.add('hidden');
            this.stopScanSweep();
        };

        setStatus('Starting…', 0);

        const interval = setInterval(async () => {
            pollCount++;
            elapsed = Math.round(pollCount * POLL_MS / 1000);

            if (pollCount > MAX_POLLS) {
                clearInterval(interval);
                clearStatus();
                alert("Scan timed out after 10 minutes.");
                return;
            }

            try {
                const res = await fetch(`/api/jobs/${jobId}/?t=${Date.now()}`);
                if (!res.ok) throw new Error(`Server responded with ${res.status}`);
                const data = await res.json();

                if (data.status === 'completed') {
                    clearInterval(interval);
                    clearStatus();
                    this.handleJobSuccess(data);
                    return;
                }

                if (data.status === 'failed') {
                    clearInterval(interval);
                    clearStatus();
                    this.handleJobFailure(data);
                    return;
                }

                const label = statusLabels[data.status] || data.status;
                setStatus(label, elapsed);

            } catch (e) {
                clearInterval(interval);
                clearStatus();
                this.isPolling = false;
                alert("Error checking scan status: " + e.message);
            }
        }, POLL_MS);
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
        
        // Notify user of scan result
        const fc = data.detection_data;
        const newCount = (fc && fc.features) ? fc.features.length : 0;
        const illegal = data.illegal_count || 0;
        if (window.showToast) {
            if (newCount > 0) {
                window.showToast(
                    `Scan complete — ${newCount} site${newCount !== 1 ? 's' : ''} detected (${illegal} illegal)`,
                    illegal > 0 ? 'error' : 'info'
                );
            } else {
                window.showToast('Scan complete — No mining sites detected in this area', 'success');
            }
        } else {
            const byLine = data.created_by ? `\nTriggered by: ${data.created_by}` : '';
            alert(newCount > 0
                ? `Scan complete: ${newCount} site(s) detected (${illegal} illegal).${byLine}`
                : `Scan complete: No mining sites detected in this area.${byLine}`);
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
        
        if (window.showToast) {
            window.showToast('Scan failed: ' + (data.error || data.failure_reason || 'Unknown error'), 'error');
        } else {
            alert("Scan failed: " + (data.error || data.failure_reason || 'Unknown error occurred'));
        }
    }

    // ── Pulse animation for detection glow layer ─────────────────────────
    startPulseAnimation() {
        if (this._pulseAnimFrame) cancelAnimationFrame(this._pulseAnimFrame);
        let phase = 0;
        const animate = () => {
            phase += 0.02;
            const t = 0.5 + 0.5 * Math.sin(phase);
            if (this.map.getLayer('detections-glow')) {
                this.map.setPaintProperty('detections-glow', 'line-opacity', 0.1 + 0.3 * t);
            }
            this._pulseAnimFrame = requestAnimationFrame(animate);
        };
        this._pulseAnimFrame = requestAnimationFrame(animate);
    }

    // ── Radar sweep canvas animation ──────────────────────────────────────
    startScanSweep(overrideBbox = null) {
        const canvas = document.getElementById('scan-sweep-canvas');
        if (!canvas) return;
        canvas.style.display = 'block';
        const ctx = canvas.getContext('2d');

        let bbox = overrideBbox;
        if (!bbox) {
            const drawData = this.draw ? this.draw.getAll() : null;
            if (!drawData || !drawData.features.length) { this.stopScanSweep(); return; }
            bbox = turf.bbox(drawData.features[0]);
        }
        let progress = 0;
        let lastTime = null;
        const SWEEP_MS = 2400;
        this._sweeping = true;

        const sweep = (ts) => {
            if (!this._sweeping) return;
            if (!lastTime) lastTime = ts;
            const dt = ts - lastTime; lastTime = ts;
            progress = (progress + dt / SWEEP_MS) % 1;

            const mapEl = document.getElementById('live-map');
            if (!mapEl) return;
            canvas.width  = mapEl.offsetWidth;
            canvas.height = mapEl.offsetHeight;
            ctx.clearRect(0, 0, canvas.width, canvas.height);

            const tl = this.map.project([bbox[0], bbox[3]]);
            const br = this.map.project([bbox[2], bbox[1]]);
            const x1 = Math.max(0, tl.x);
            const x2 = Math.min(canvas.width, br.x);
            const y1 = Math.max(0, tl.y);
            const y2 = Math.min(canvas.height, br.y);
            if (x2 <= x1 || y2 <= y1) { this._sweepAnimFrame = requestAnimationFrame(sweep); return; }

            const sweepY = y1 + (y2 - y1) * progress;

            // Trailing gradient above the line
            const grad = ctx.createLinearGradient(0, sweepY - 32, 0, sweepY);
            grad.addColorStop(0, 'rgba(74,222,128,0)');
            grad.addColorStop(1, 'rgba(74,222,128,0.16)');
            ctx.fillStyle = grad;
            ctx.fillRect(x1, sweepY - 32, x2 - x1, 32);

            // Scan line
            ctx.save();
            ctx.shadowColor = 'rgba(74,222,128,0.9)';
            ctx.shadowBlur  = 8;
            ctx.strokeStyle = 'rgba(74,222,128,0.85)';
            ctx.lineWidth   = 1.5;
            ctx.beginPath();
            ctx.moveTo(x1, sweepY);
            ctx.lineTo(x2, sweepY);
            ctx.stroke();
            ctx.restore();

            this._sweepAnimFrame = requestAnimationFrame(sweep);
        };
        this._sweepAnimFrame = requestAnimationFrame(sweep);
    }

    stopScanSweep() {
        this._sweeping = false;
        if (this._sweepAnimFrame) { cancelAnimationFrame(this._sweepAnimFrame); this._sweepAnimFrame = null; }
        const canvas = document.getElementById('scan-sweep-canvas');
        if (canvas) {
            canvas.style.display = 'none';
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }
    }

    // ── Batch job tracking ────────────────────────────────────────────────

    trackBatchJobs(jobs) {
        // jobs = [{id, name, geometry}]
        jobs.forEach(j => this._batchJobs.set(j.id, {
            name: j.name, geometry: j.geometry,
            status: 'queued', elapsed: 0, pollCount: 0
        }));
        this._updateBatchAOIsOnMap();
        this._updateBatchPanel();
        this._startBatchPolling();
        // Focus first job immediately
        if (jobs.length > 0) this._focusBatchJob(jobs[0].id);
    }

    _updateBatchAOIsOnMap() {
        const src = this.map.getSource('batch-aois');
        if (!src) return;
        const features = [];
        this._batchJobs.forEach((job, jobId) => {
            features.push({
                type: 'Feature',
                properties: { jobId, name: job.name, status: job.status, focused: jobId === this._focusedJobId },
                geometry: job.geometry
            });
        });
        src.setData({ type: 'FeatureCollection', features });
    }

    _focusBatchJob(jobId) {
        this._focusedJobId = jobId;
        const job = this._batchJobs.get(jobId);
        if (!job) return;

        // Zoom to AOI
        const bbox = turf.bbox({ type: 'Feature', geometry: job.geometry });
        this.map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 80, duration: 800 });

        // Update sweep animation for this AOI
        this.stopScanSweep();
        const inProgress = !['completed', 'failed'].includes(job.status);
        if (inProgress) {
            this.startScanSweep(bbox);
            const hud = document.getElementById('scan-hud');
            if (hud) hud.classList.remove('hidden');
        } else {
            const hud = document.getElementById('scan-hud');
            if (hud) hud.classList.add('hidden');
        }

        // Highlight focused card
        document.querySelectorAll('.batch-job-card').forEach(el => {
            el.style.background = el.dataset.jobId === jobId
                ? 'rgba(255,255,255,0.08)' : 'transparent';
        });
        this._updateBatchAOIsOnMap();
    }

    _startBatchPolling() {
        if (this._batchPollInterval) return;
        const POLL_MS = 3000;
        const statusLabels = {
            queued: 'Queued', validating: 'Validating',
            exporting: 'Fetching imagery', preprocessing: 'Processing',
            inferring: 'Running model', postprocessing: 'Extracting sites',
            storing: 'Saving', completed: 'Complete', failed: 'Failed'
        };

        this._batchPollInterval = setInterval(async () => {
            const active = [...this._batchJobs.entries()]
                .filter(([, j]) => !['completed', 'failed'].includes(j.status));

            if (active.length === 0) {
                clearInterval(this._batchPollInterval);
                this._batchPollInterval = null;
                // Fade panel out after 8s if all done
                setTimeout(() => {
                    const panel = document.getElementById('batch-scan-panel');
                    if (panel) panel.classList.add('hidden');
                    this._batchJobs.clear();
                    this._updateBatchAOIsOnMap();
                }, 8000);
                return;
            }

            for (const [jobId, job] of active) {
                job.pollCount++;
                job.elapsed = Math.round(job.pollCount * POLL_MS / 1000);
                try {
                    const res = await fetch(`/api/jobs/${jobId}/?t=${Date.now()}`);
                    if (!res.ok) continue;
                    const data = await res.json();
                    job.status = data.status;
                    if (data.status === 'completed') this._onBatchJobComplete(jobId, data);
                    else if (data.status === 'failed')  this._onBatchJobFailed(jobId, data);
                } catch (_) {}
            }

            this._updateBatchAOIsOnMap();
            this._updateBatchPanel();

            // Keep HUD in sync with focused job
            const focused = this._batchJobs.get(this._focusedJobId);
            if (focused && !['completed', 'failed'].includes(focused.status)) {
                const hudEl      = document.getElementById('scan-hud');
                const hudText    = document.getElementById('scan-hud-text');
                const hudElapsed = document.getElementById('scan-hud-elapsed');
                if (hudEl)      hudEl.classList.remove('hidden');
                if (hudText)    hudText.textContent    = statusLabels[focused.status] || focused.status;
                if (hudElapsed) hudElapsed.textContent = focused.elapsed + 's';
            }
        }, POLL_MS);
    }

    _onBatchJobComplete(jobId, data) {
        // If focused job completed, hide sweep and show toast
        if (jobId === this._focusedJobId) {
            this.stopScanSweep();
            const hud = document.getElementById('scan-hud');
            if (hud) hud.classList.add('hidden');
            // Auto-focus next in-progress job
            const next = [...this._batchJobs.entries()]
                .find(([id, j]) => id !== jobId && !['completed', 'failed'].includes(j.status));
            if (next) this._focusBatchJob(next[0]);
        }
        const n = data.total_detections || 0;
        const ill = data.illegal_count || 0;
        if (window.showToast) {
            window.showToast(n > 0
                ? `${data.scene_id || 'Scan'}: ${n} site${n !== 1 ? 's' : ''} (${ill} illegal)`
                : `${data.scene_id || 'Scan'}: No mining detected`, n > 0 ? 'error' : 'success');
        }
        // Refresh detections on map
        fetch('/api/sites/', { credentials: 'include' })
            .then(r => r.ok ? r.json() : null)
            .then(d => {
                if (d && this.map.getSource('detections')) {
                    this.map.getSource('detections').setData(d);
                    if (typeof this._onDetectionsUpdated === 'function')
                        this._onDetectionsUpdated(d.features || [], { source: 'scan' });
                }
            }).catch(() => {});
    }

    _onBatchJobFailed(jobId, data) {
        if (jobId === this._focusedJobId) {
            this.stopScanSweep();
            const hud = document.getElementById('scan-hud');
            if (hud) hud.classList.add('hidden');
            const next = [...this._batchJobs.entries()]
                .find(([id, j]) => id !== jobId && !['completed', 'failed'].includes(j.status));
            if (next) this._focusBatchJob(next[0]);
        }
        if (window.showToast)
            window.showToast('Scan failed: ' + (data.failure_reason || 'Unknown error'), 'error');
    }

    _updateBatchPanel() {
        const panel = document.getElementById('batch-scan-panel');
        const list  = document.getElementById('batch-job-list');
        if (!panel || !list) return;

        if (this._batchJobs.size === 0) { panel.classList.add('hidden'); return; }
        panel.classList.remove('hidden');

        const statusLabels = {
            queued: 'Queued', validating: 'Validating',
            exporting: 'Fetching imagery', preprocessing: 'Processing',
            inferring: 'Running model', postprocessing: 'Extracting sites',
            storing: 'Saving', completed: 'Complete', failed: 'Failed'
        };
        const statusColor = s => s === 'completed' ? '#4ade80' : s === 'failed' ? '#ef4444' : '#fbbf24';

        list.innerHTML = [...this._batchJobs.entries()].map(([jobId, job]) => `
            <div class="batch-job-card" data-job-id="${jobId}"
                 onclick="window.mapHandler._focusBatchJob('${jobId}')"
                 style="display:flex;align-items:center;gap:8px;padding:7px 8px;border-radius:8px;cursor:pointer;margin-bottom:4px;
                        background:${jobId === this._focusedJobId ? 'rgba(255,255,255,0.08)' : 'transparent'};
                        transition:background 0.2s;">
                <div style="width:8px;height:8px;border-radius:50%;flex-shrink:0;background:${statusColor(job.status)};
                            ${!['completed','failed'].includes(job.status) ? 'animation:pulseGreenDot 1.6s ease infinite;' : ''}"></div>
                <div style="flex:1;min-width:0;">
                    <div style="font-size:12px;font-weight:500;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
                         title="${job.name}">${job.name}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.45);">
                        ${statusLabels[job.status] || job.status}${job.elapsed > 0 ? ' · ' + job.elapsed + 's' : ''}
                    </div>
                </div>
            </div>`).join('');
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
        if (name === 'csrftoken') {
            const meta = document.querySelector('meta[name="csrf-token"]');
            if (meta) return meta.getAttribute('content') || '';
        }
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