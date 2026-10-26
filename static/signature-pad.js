/* The signature box on a fiche de police (CESEDA R. 814-1), on the guest's
   online check-in and on the desk's screen. Draws with a finger or a mouse
   and posts the drawing as a PNG in the hidden field beside it; without this
   script the box stays hidden and the typed name is the signature. */
(function(){
  if (window.__gSig) return; window.__gSig = true;
  function setup(box){
    var c = box.querySelector('[data-sig-canvas]'), out = box.querySelector('[data-sig-value]');
    var help = box.querySelector('[data-sig-help]'), typedHelp = box.querySelector('[data-sig-typed-help]');
    if (!c || !c.getContext) return;
    c.hidden = false; help.hidden = false;
    if (typedHelp) typedHelp.textContent = 'No box to sign in? Type your full name, exactly as above.';
    var g = c.getContext('2d'), down = false, drawn = false;
    g.lineWidth = 3; g.lineCap = 'round'; g.lineJoin = 'round'; g.strokeStyle = '#13294b';
    function at(e){ var r = c.getBoundingClientRect();
      return [(e.clientX - r.left) * c.width / r.width, (e.clientY - r.top) * c.height / r.height]; }
    c.addEventListener('pointerdown', function(e){ down = true; c.setPointerCapture(e.pointerId);
      var p = at(e); g.beginPath(); g.moveTo(p[0], p[1]); e.preventDefault(); });
    c.addEventListener('pointermove', function(e){ if (!down) return;
      var p = at(e); g.lineTo(p[0], p[1]); g.stroke(); drawn = true; e.preventDefault(); });
    function end(){ if (!down) return; down = false; if (drawn) out.value = c.toDataURL('image/png'); }
    c.addEventListener('pointerup', end); c.addEventListener('pointercancel', end);
    box.querySelector('[data-sig-clear]').addEventListener('click', function(){
      g.clearRect(0, 0, c.width, c.height); drawn = false; out.value = ''; });
  }
  function init(){ document.querySelectorAll('[data-sigpad]').forEach(setup); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
