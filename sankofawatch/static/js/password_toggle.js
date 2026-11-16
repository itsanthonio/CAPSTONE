/**
 * Adds a "show / hide password" eye button to every password field on the page.
 * Self-contained (inline SVG icons), so it works on pages without the icon font.
 */
(function () {
    var EYE = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    var EYE_OFF = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

    function enhance(input) {
        if (input.dataset.pwToggle) return;
        input.dataset.pwToggle = '1';

        var wrap = document.createElement('div');
        wrap.style.cssText = 'position:relative;display:block;width:100%;';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        input.style.paddingRight = '38px';

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('aria-label', 'Show password');
        btn.setAttribute('aria-pressed', 'false');
        btn.tabIndex = 0;
        btn.innerHTML = EYE;
        btn.style.cssText = 'position:absolute;right:6px;top:50%;transform:translateY(-50%);' +
            'background:none;border:0;padding:4px;cursor:pointer;line-height:0;color:#9b8f7a;border-radius:4px;';
        btn.addEventListener('mouseenter', function () { btn.style.color = '#f6be39'; });
        btn.addEventListener('mouseleave', function () { btn.style.color = '#9b8f7a'; });
        btn.addEventListener('focus', function () { btn.style.outline = '2px solid #f6be39'; });
        btn.addEventListener('blur', function () { btn.style.outline = 'none'; });
        btn.addEventListener('click', function () {
            var show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            btn.innerHTML = show ? EYE_OFF : EYE;
            btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
            btn.setAttribute('aria-pressed', show ? 'true' : 'false');
            input.focus();
        });
        wrap.appendChild(btn);
    }

    function scan() { document.querySelectorAll('input[type="password"]').forEach(enhance); }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan);
    else scan();
    // Fields added later (modals, dynamic forms)
    new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
})();
