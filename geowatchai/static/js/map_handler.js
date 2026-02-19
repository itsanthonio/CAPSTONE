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
            style: 'https://demotiles.maplibre.org/style.json',
            center: this.options.center || [-1.6244, 6.6885],
            zoom: this.options.zoom || 10
        });

        this.map.on('load', () => {
            this.setupDrawingTools();
            this.setupMapLayers();
            this.setupClickHandlers();
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
        if (typeof MapboxDraw === 'undefined') return;
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
        const fullLayerId = layerId === 'detections' ? 'detections-layer' : layerId;
        if (this.map.getLayer(fullLayerId)) {
            const visibility = this.map.getLayoutProperty(fullLayerId, 'visibility');
            this.map.setLayoutProperty(
                fullLayerId, 'visibility', visibility === 'none' ? 'visible' : 'none'
            );
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
        let failCount = 0;
        const interval = setInterval(async () => {
            try {
                const res = await fetch(`/api/jobs/${jobId}/`);
                if (!res.ok) { failCount++; if (failCount > 5) clearInterval(interval); return; }
                const job = await res.json();
                console.log("Job status:", job.status);

                if (job.status === 'completed') {
                    clearInterval(interval);
                    // Fetch the results for this job and render them
                    try {
                        const rRes = await fetch(`/api/results/?job_id=${jobId}`, { credentials: 'include' });
                        const rData = await rRes.json();
                        // Handle both paginated {results:[...]} and plain array
                        const results = Array.isArray(rData) ? rData : (rData.results || []);
                        const allFeatures = [];
                        results.forEach(r => {
                            if (r.geojson && r.geojson.features) {
                                r.geojson.features.forEach(f => allFeatures.push(f));
                            }
                        });
                        if (allFeatures.length > 0) {
                            this.map.getSource('detections').setData({
                                type: 'FeatureCollection',
                                features: allFeatures
                            });
                            // Fit map to results
                            try {
                                const bounds = new maplibregl.LngLatBounds();
                                allFeatures.forEach(f => {
                                    if (f.geometry && f.geometry.coordinates) {
                                        const coords = f.geometry.type === 'Polygon'
                                            ? f.geometry.coordinates[0]
                                            : f.geometry.coordinates.flat(2);
                                        coords.forEach(c => bounds.extend(c));
                                    }
                                });
                                if (!bounds.isEmpty()) {
                                    this.map.fitBounds(bounds, { padding: 40, maxZoom: 13 });
                                }
                            } catch(e) { console.warn('Could not fit bounds:', e); }
                        }
                        console.log(`Loaded ${allFeatures.length} detection features`);
                    } catch(e) {
                        console.error('Failed to fetch results after job completion:', e);
                    }
                } else if (job.status === 'failed') {
                    clearInterval(interval);
                    console.error('Job failed:', job.failure_reason || 'Unknown reason');
                }
                // Otherwise still running — keep polling
            } catch (e) {
                console.error('Poll error:', e);
                failCount++;
                if (failCount > 5) clearInterval(interval);
            }
        }, 3000);
    }

    updateConfidenceFilter(val) {
        if (this.map.getLayer('detections-layer')) {
            this.map.setFilter('detections-layer', ['>=', ['get', 'confidence'], parseFloat(val)]);
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
}