// Remote D-pad: arrow keys jump between anything clickable, Enter clicks it.
// With a big video on screen and nothing highlighted:
//   Enter = play/pause, Left/Right = skip 10s, Up/Down = start highlighting.
(() => {
  const SEL = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]',
    '[role=menuitemradio]', '[role=menuitemcheckbox]', '[role=option]',
    '[role=checkbox]', '[role=radio]', '[role=switch]', '[role=gridcell]',
    '[tabindex]:not([tabindex="-1"])', '[onclick]'
  ].join(',');
  const DIRS = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right' };

  let current = null, lastRect = null, box = null;
  const handled = new Set();

  // ---------- helpers ----------
  const center = r => ({ x: (r.left + r.right) / 2, y: (r.top + r.bottom) / 2 });
  const onScreen = r => r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
  const parentOf = n => n.assignedSlot ||
    (n.parentNode instanceof ShadowRoot ? n.parentNode.host : n.parentElement);

  function contains(a, b) {
    for (let n = b; n; n = n.parentNode || n.host) if (n === a) return true;
    return false;
  }

  function allElements(root = document, out = []) {
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    for (let n = tw.nextNode(); n; n = tw.nextNode()) {
      out.push(n);
      if (n.shadowRoot) allElements(n.shadowRoot, out);
    }
    return out;
  }

  function deepActive() {
    let a = document.activeElement;
    while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
    return a;
  }

  function isTyping() {
    const a = deepActive();
    if (!a) return false;
    if (a.isContentEditable || a.tagName === 'TEXTAREA') return true;
    if (a.tagName === 'INPUT') {
      return !['button', 'checkbox', 'radio', 'submit', 'reset',
               'range', 'color', 'file', 'image'].includes(a.type);
    }
    return false;
  }

  // ---------- find clickable things ----------
  function collect() {
    const vw = innerWidth, vh = innerHeight, maxArea = vw * vh * 0.6;
    const cache = new Map();
    const style = el => {
      let s = cache.get(el);
      if (!s) { s = getComputedStyle(el); cache.set(el, s); }
      return s;
    };
    const found = [];
    for (const el of allElements()) {
      if (el === box) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 6 || r.height < 6 || r.width * r.height > maxArea) continue;
      if (r.bottom < -2 * vh || r.top > 3 * vh || r.right < -2 * vw || r.left > 3 * vw) continue;
      const s = style(el);
      if (s.visibility === 'hidden' || s.opacity === '0' || s.pointerEvents === 'none') continue;
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
      let ok = el.matches(SEL);
      if (!ok && s.cursor === 'pointer') {
        // cursor is inherited: only count the outermost element that turns it on
        const p = parentOf(el);
        ok = !p || style(p).cursor !== 'pointer';
      }
      if (ok) found.push({ el, r, area: r.width * r.height });
    }
    // drop wrappers that are essentially the same box as a bigger one already kept
    found.sort((a, b) => b.area - a.area);
    const kept = [];
    for (const c of found) {
      const dup = kept.some(k => {
        const ix = Math.max(0, Math.min(k.r.right, c.r.right) - Math.max(k.r.left, c.r.left));
        const iy = Math.max(0, Math.min(k.r.bottom, c.r.bottom) - Math.max(k.r.top, c.r.top));
        return ix * iy > 0.9 * c.area && c.area > 0.6 * k.area;
      });
      if (!dup) kept.push(c);
    }
    return kept;
  }

  // ---------- choosing the next item ----------
  function score(a, b, dir) {
    const ac = center(a), bc = center(b);
    let main, gap, off;
    if (dir === 'left' || dir === 'right') {
      if (dir === 'right' ? bc.x <= ac.x + 2 : bc.x >= ac.x - 2) return Infinity;
      main = dir === 'right' ? b.left - a.right : a.left - b.right;
      gap = Math.max(0, Math.max(a.top, b.top) - Math.min(a.bottom, b.bottom));
      off = Math.abs(bc.y - ac.y);
    } else {
      if (dir === 'down' ? bc.y <= ac.y + 2 : bc.y >= ac.y - 2) return Infinity;
      main = dir === 'down' ? b.top - a.bottom : a.top - b.bottom;
      gap = Math.max(0, Math.max(a.left, b.left) - Math.min(a.right, b.right));
      off = Math.abs(bc.x - ac.x);
    }
    return Math.max(main, 0) + gap * 4 + off * 0.3;
  }

  function move(dir) {
    const cands = collect();
    if (!cands.length) return;
    const from = current && current.isConnected ? current.getBoundingClientRect() : null;
    if (!from || !from.width) {
      // nothing highlighted yet: start near the last spot, or the middle of the screen
      const ref = lastRect || { left: innerWidth / 2, right: innerWidth / 2,
                                top: innerHeight / 2, bottom: innerHeight / 2 };
      const rc = center(ref);
      let best = null, bd = Infinity;
      for (const c of cands) {
        const cc = center(c.r);
        const d = Math.hypot(cc.x - rc.x, cc.y - rc.y) + (onScreen(c.r) ? 0 : 1e6);
        if (d < bd) { bd = d; best = c; }
      }
      setCurrent(best.el);
      return;
    }
    let best = null, bs = Infinity;
    for (const c of cands) {
      if (c.el === current || contains(c.el, current)) continue;
      const s = score(from, c.r, dir);
      if (s < bs) { bs = s; best = c; }
    }
    if (best) setCurrent(best.el);
  }

  function setCurrent(el) {
    current = el;
    try { el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' }); } catch (_) {}
    if (el.tabIndex >= 0) { try { el.focus({ preventScroll: true }); } catch (_) {} }
    ensureBox();
  }

  // ---------- highlight outline ----------
  function ensureBox() {
    if (box) return;
    box = document.createElement('div');
    box.style.cssText =
      'position:fixed;pointer-events:none;z-index:2147483647;box-sizing:content-box;' +
      'border:4px solid #3ea6ff;border-radius:10px;' +
      'box-shadow:0 0 0 3px rgba(0,0,0,.6),0 0 18px #3ea6ff;' +
      'transition:all .12s ease-out;display:none;';
    requestAnimationFrame(tick);
  }

  function tick() {
    const fs = document.fullscreenElement;
    const host = fs && fs.tagName !== 'VIDEO' ? fs : document.documentElement;
    if (box.parentNode !== host) host.appendChild(box);
    if (current && current.isConnected) {
      const r = current.getBoundingClientRect();
      if (r.width && r.height) {
        lastRect = r;
        Object.assign(box.style, {
          display: 'block',
          left: (r.left - 6) + 'px', top: (r.top - 6) + 'px',
          width: (r.width + 4) + 'px', height: (r.height + 4) + 'px'
        });
      } else {
        box.style.display = 'none';
      }
    } else {
      current = null;
      box.style.display = 'none';
    }
    requestAnimationFrame(tick);
  }

  // ---------- clicking ----------
  function deepFromPoint(x, y) {
    let el = document.elementFromPoint(x, y);
    while (el && el.shadowRoot) {
      const inner = el.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === el) break;
      el = inner;
    }
    return el;
  }

  function activate(el) {
    const r = el.getBoundingClientRect();
    const x = Math.min(Math.max(r.left + r.width / 2, 1), innerWidth - 1);
    const y = Math.min(Math.max(r.top + r.height / 2, 1), innerHeight - 1);
    let t = deepFromPoint(x, y);
    if (!t || !contains(el, t)) t = el;
    const base = { bubbles: true, cancelable: true, composed: true,
                   clientX: x, clientY: y, button: 0 };
    const ptr = { ...base, pointerId: 1, pointerType: 'mouse', isPrimary: true };
    t.dispatchEvent(new PointerEvent('pointerdown', { ...ptr, buttons: 1 }));
    t.dispatchEvent(new MouseEvent('mousedown', { ...base, buttons: 1 }));
    if (el.tabIndex >= 0) { try { el.focus({ preventScroll: true }); } catch (_) {} }
    t.dispatchEvent(new PointerEvent('pointerup', { ...ptr, buttons: 0 }));
    t.dispatchEvent(new MouseEvent('mouseup', { ...base, buttons: 0 }));
    t.dispatchEvent(new MouseEvent('click', { ...base, buttons: 0, detail: 1 }));
  }

  // ---------- video player mode ----------
  function bigVideo() {
    let best = null, bestA = 0;
    for (const el of allElements()) {
      if (el.tagName !== 'VIDEO') continue;
      const r = el.getBoundingClientRect();
      const a = r.width * r.height;
      if (a > bestA) { best = el; bestA = a; }
    }
    return bestA >= innerWidth * innerHeight * 0.6 ? best : null;
  }

  // ---------- keys ----------
  function consume(e) {
    e.preventDefault();
    e.stopImmediatePropagation();
    handled.add(e.code);
  }

  window.addEventListener('keydown', e => {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;  // Alt+Left = page back
    if (e.key === 'Escape') { current = null; return; }             // page still gets Escape
    const dir = DIRS[e.key];
    if (!dir && e.key !== 'Enter') return;
    if (isTyping() && e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;

    if (!current) {
      const v = bigVideo();
      if (v && e.key === 'Enter') {
        consume(e);
        if (v.paused) v.play().catch(() => {}); else v.pause();
        return;
      }
      if (v && (dir === 'left' || dir === 'right')) {
        consume(e);
        try { v.currentTime += dir === 'right' ? 10 : -10; } catch (_) {}
        return;
      }
      if (e.key === 'Enter') return;   // nothing highlighted: let the page handle Enter
    }

    consume(e);
    if (e.key === 'Enter') activate(current);
    else move(dir);
  }, true);

  window.addEventListener('keyup', e => {
    if (handled.delete(e.code)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);
})();
