/**
 * Map location search — local Ghana dataset first, Photon (OSM) fallback.
 *
 *   initMapSearch(containerId, flyToFn)
 *   - containerId : string ID of the map's root element
 *   - flyToFn     : function(lng, lat) that pans the map
 */
function initMapSearch(containerId, flyToFn) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const wrapper = document.createElement('div');
    wrapper.style.cssText = [
        'position:absolute',
        'top:12px',
        'left:12px',
        'z-index:1100',
        'width:260px',
        'pointer-events:auto',
    ].join(';');

    wrapper.innerHTML = `
        <div style="position:relative;">
            <div style="position:relative;">
                <svg style="position:absolute;left:10px;top:50%;transform:translateY(-50%);width:15px;height:15px;pointer-events:none;color:var(--text-tertiary);"
                     fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <circle cx="11" cy="11" r="8"/>
                    <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35"/>
                </svg>
                <input type="text" autocomplete="off" placeholder="Search location in Ghana…"
                       style="width:100%;padding:8px 10px 8px 33px;border-radius:8px;font-size:13px;
                              outline:none;background:var(--surface-2);border:1px solid var(--border-default);
                              color:var(--text-primary);box-shadow:0 2px 8px rgba(0,0,0,0.35);">
            </div>
            <div class="ms-results"
                 style="display:none;position:absolute;top:calc(100% + 4px);left:0;right:0;
                        border-radius:8px;overflow:hidden;background:var(--surface-2);
                        border:1px solid var(--border-default);box-shadow:0 4px 16px rgba(0,0,0,0.45);
                        max-height:240px;overflow-y:auto;"></div>
        </div>
    `;

    container.appendChild(wrapper);

    const input   = wrapper.querySelector('input');
    const results = wrapper.querySelector('.ms-results');
    let photonTimer;

    /* ── Local search against GHANA_PLACES ── */
    function localSearch(q) {
        const places = window.GHANA_PLACES;
        if (!places || !places.length) return [];
        const lower = q.toLowerCase();
        const exact = [], prefix = [], contains = [];
        for (const p of places) {
            const n = p.name.toLowerCase();
            if (n === lower)              exact.push(p);
            else if (n.startsWith(lower)) prefix.push(p);
            else if (n.includes(lower))   contains.push(p);
        }
        return [...exact, ...prefix, ...contains].slice(0, 5);
    }

    function renderLocalResults(matches, q) {
        results.innerHTML = matches.map(p => `
            <div data-lat="${p.lat}" data-lon="${p.lon}" data-name="${p.name}"
                 style="padding:9px 12px;cursor:pointer;border-bottom:1px solid var(--border-subtle);"
                 onmouseover="this.style.background='var(--surface-3)'"
                 onmouseout="this.style.background=''">
                <div style="font-size:13px;font-weight:500;color:var(--text-primary);">${p.name}</div>
                <div style="font-size:11px;color:var(--text-tertiary);">${p.region}, Ghana</div>
            </div>`).join('');
        results.style.display = 'block';
        attachClickHandlers();
    }

    function attachClickHandlers() {
        results.querySelectorAll('[data-lat]').forEach(item => {
            item.addEventListener('click', function () {
                flyToFn(parseFloat(this.dataset.lon), parseFloat(this.dataset.lat));
                input.value = this.dataset.name;
                results.style.display = 'none';
            });
        });
    }

    /* ── Keyboard ── */
    input.addEventListener('input', function () {
        clearTimeout(photonTimer);
        const q = this.value.trim();
        if (q.length < 2) { results.style.display = 'none'; return; }

        // Instant local results
        const local = localSearch(q);
        if (local.length) {
            renderLocalResults(local, q);
            // Still fetch Photon in background if fewer than 3 local hits
            if (local.length < 3) {
                photonTimer = setTimeout(() => geocodePhoton(q), 600);
            }
        } else {
            // No local results — show spinner and go straight to Photon
            results.innerHTML = '<div style="padding:10px 12px;font-size:13px;color:var(--text-tertiary);">Searching…</div>';
            results.style.display = 'block';
            photonTimer = setTimeout(() => geocodePhoton(q), 420);
        }
    });

    input.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { results.style.display = 'none'; this.blur(); }
        if (e.key === 'Enter')  { const first = results.querySelector('[data-lat]'); if (first) first.click(); }
    });

    /* Close on outside click */
    document.addEventListener('click', function (e) {
        if (!wrapper.contains(e.target)) results.style.display = 'none';
    });

    /* ── Photon fallback (autocomplete-friendly, OSM-based) ── */
    async function geocodePhoton(q) {
        try {
            const url = 'https://photon.komoot.io/api/?q=' + encodeURIComponent(q)
                        + '&limit=5&lang=en&lat=7.9&lon=-1.0&bbox=-3.5,4.5,1.4,11.3';
            const res  = await fetch(url);
            const data = await res.json();
            const features = (data.features || []);

            if (!features.length) {
                // Keep existing local results if we have any, else show no-results
                if (!results.querySelector('[data-lat]')) {
                    results.innerHTML = '<div style="padding:10px 12px;font-size:13px;color:var(--text-tertiary);">No results found</div>';
                }
                return;
            }

            results.innerHTML = features.map(f => {
                const p      = f.properties;
                const name   = p.name || p.city || p.town || p.village || q;
                const detail = [p.county, p.state].filter(Boolean).join(', ');
                const [lon, lat] = f.geometry.coordinates;
                return `<div data-lat="${lat}" data-lon="${lon}" data-name="${name}"
                             style="padding:9px 12px;cursor:pointer;border-bottom:1px solid var(--border-subtle);"
                             onmouseover="this.style.background='var(--surface-3)'"
                             onmouseout="this.style.background=''">
                            <div style="font-size:13px;font-weight:500;color:var(--text-primary);">${name}</div>
                            <div style="font-size:11px;color:var(--text-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${detail}</div>
                        </div>`;
            }).join('');
            results.style.display = 'block';
            attachClickHandlers();

        } catch (err) {
            if (!results.querySelector('[data-lat]')) {
                results.innerHTML = '<div style="padding:10px 12px;font-size:13px;color:var(--status-critical);">Search unavailable</div>';
            }
            console.error('Map search error:', err);
        }
    }
}
