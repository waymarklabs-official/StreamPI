// Hide the mouse cursor while the remote is in use; a real mouse brings it back.
(() => {
  const ID = 'remote-hide-cursor';
  let hidden = false;

  function hide() {
    if (hidden) return;
    hidden = true;
    let s = document.getElementById(ID);
    if (!s) {
      s = document.createElement('style');
      s.id = ID;
      s.textContent = '*, *::before, *::after { cursor: none !important; }';
    }
    (document.head || document.documentElement).appendChild(s);
  }

  function show() {
    if (!hidden) return;
    hidden = false;
    const s = document.getElementById(ID);
    if (s) s.remove();
  }

  const parked = e => e.clientX >= innerWidth - 3 && e.clientY >= innerHeight - 3;

  window.addEventListener('mousemove', e => {
    if (!e.isTrusted) return;
    if (parked(e)) hide();     // the remote just tucked the cursor away
    else show();               // someone moved a real mouse
  }, true);

  window.addEventListener('keydown', hide, true);
  hide();                      // start hidden: this is a TV
})();
