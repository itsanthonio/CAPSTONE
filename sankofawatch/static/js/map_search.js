/**
 * Map location search — proxies through /api/geocode/ (local GhanaPlace DB → Google → Nominatim).
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
        'top:14px',
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
    let timer;
    const _cache = new Map();  // session-level query cache

    function showResults(places) {
        results.innerHTML = '';
        if (!places.length) {
            results.innerHTML = '<div style="padding:10px 12px;font-size:13px;color:var(--text-tertiary);">No results found</div>';
            results.style.display = 'block';
            return;
        }
        places.forEach(p => {
            const parts = p.display_name.split(',');
            const item = document.createElement('div');
            item.dataset.lat  = p.lat;
            item.dataset.lon  = p.lon;
            item.dataset.name = parts[0].trim();
            item.style.cssText = 'padding:9px 12px;cursor:pointer;border-bottom:1px solid var(--border-subtle);';
            item.onmouseover = () => item.style.background = 'var(--surface-3)';
            item.onmouseout  = () => item.style.background = '';
            item.innerHTML = `
                <div style="font-size:13px;font-weight:500;color:var(--text-primary);">${parts[0]}</div>
                ${parts.length > 1 ? `<div style="font-size:11px;color:var(--text-tertiary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${parts.slice(1).join(',').trim()}</div>` : ''}
            `;
            item.addEventListener('click', function () {
                flyToFn(parseFloat(this.dataset.lon), parseFloat(this.dataset.lat));
                input.value = this.dataset.name;
                results.style.display = 'none';
            });
            results.appendChild(item);
        });
        results.style.display = 'block';
    }

    function search(q) {
        const key = q.toLowerCase();
        if (_cache.has(key)) { showResults(_cache.get(key)); return; }
        results.innerHTML = '<div style="padding:10px 12px;font-size:13px;color:var(--text-tertiary);">Searching…</div>';
        results.style.display = 'block';
        fetch('/api/geocode/?q=' + encodeURIComponent(q), { credentials: 'include' })
            .then(r => r.json())
            .then(data => { _cache.set(key, data.results || []); showResults(data.results || []); })
            .catch(() => { results.style.display = 'none'; });
    }

    input.addEventListener('input', function () {
        clearTimeout(timer);
        const q = this.value.trim();
        if (q.length < 2) { results.style.display = 'none'; return; }
        // Show cached result instantly, otherwise wait 200ms before hitting the server
        if (_cache.has(q.toLowerCase())) { search(q); return; }
        timer = setTimeout(() => search(q), 200);
    });

    input.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { results.style.display = 'none'; this.blur(); }
        if (e.key === 'Enter') {
            const first = results.querySelector('[data-lat]');
            if (first) first.click();
        }
    });

    document.addEventListener('click', function (e) {
        if (!wrapper.contains(e.target)) results.style.display = 'none';
    });
}
