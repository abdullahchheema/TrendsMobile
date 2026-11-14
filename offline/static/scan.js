// Camera scanning for IMEI fields. A small "Scan" button appears next to every IMEI box (POS search, purchases, returns, transfers, lookups).
// It opens the phone or laptop camera, reads the barcode on the box or the phone's "About" screen, checks it is a valid IMEI and fills the box.
// The browser's own BarcodeDetector is used when it exists (most Android phones); otherwise the bundled ZXing reader does the work.
// Typing and hardware scanners keep working exactly as before. Camera access needs the site to be opened over https.
(function () {
  'use strict';
  var script = document.currentScript;
  var ZXING_URL = script && script.dataset.zxing;
  var FORMATS = ['code_128', 'code_39', 'ean_13', 'itf', 'qr_code', 'data_matrix'];

  function luhn(number) {
    var sum = 0, alt = false;
    for (var i = number.length - 1; i >= 0; i--) {
      var n = number.charCodeAt(i) - 48;
      if (alt) { n *= 2; if (n > 9) n -= 9; }
      sum += n; alt = !alt;
    }
    return sum % 10 === 0;
  }

  // Finds a valid 15-digit IMEI inside whatever the barcode held ("IMEI: 35...", "35.../01", a serial plus IMEI…).
  function findImei(text) {
    var runs = String(text || '').replace(/[^0-9]+/g, ' ').trim().split(' ');
    for (var i = 0; i < runs.length; i++) {
      var run = runs[i];
      if (run.length === 15 && luhn(run)) return run;
      if (run.length > 15) for (var j = 0; j + 15 <= run.length; j++) { var part = run.substr(j, 15); if (luhn(part)) return part; }
    }
    return '';
  }

  function isImeiField(el) {
    if (!(el instanceof HTMLElement) || !/^(INPUT|TEXTAREA)$/.test(el.tagName)) return false;
    if (el.type === 'hidden' || el.type === 'checkbox' || el.type === 'radio' || el.dataset.noScan !== undefined) return false;
    var label = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    var hint = [el.name, el.getAttribute('data-f'), el.placeholder, el.getAttribute('aria-label'), label && label.textContent].join(' ');
    return /imei/i.test(hint) && !el.closest('dialog[data-turbo-permanent]');
  }

  var dialog, video, status, stream, timer, reader, active;

  function build() {
    if (dialog) return;
    dialog = document.createElement('dialog');
    dialog.className = 'scan-dialog';
    dialog.setAttribute('aria-label', 'Scan an IMEI');
    dialog.innerHTML = '<h2>Scan the IMEI</h2><video playsinline muted></video><p role="status"></p><div class="scan-actions"><button type="button" data-scan-cancel>Cancel</button></div>';
    document.body.appendChild(dialog);
    video = dialog.querySelector('video');
    status = dialog.querySelector('p');
    dialog.querySelector('[data-scan-cancel]').addEventListener('click', stop);
    dialog.addEventListener('close', release);
    dialog.addEventListener('cancel', release);
  }

  function release() {
    if (timer) { clearInterval(timer); timer = null; }
    if (reader) { try { reader.reset(); } catch (e) { /* already stopped */ } reader = null; }
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    if (video) video.srcObject = null;
  }

  function stop() { release(); if (dialog && dialog.open) dialog.close(); }

  function deliver(code) {
    var field = active;
    stop();
    if (!field) return;
    if (field.tagName === 'TEXTAREA') field.value = (field.value.trim() ? field.value.replace(/\s+$/, '') + '\n' : '') + code;
    else field.value = code;
    field.dispatchEvent(new Event('input', { bubbles: true }));
    field.dispatchEvent(new Event('change', { bubbles: true }));
    if (field.tagName === 'INPUT') {
      field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));   // the POS and purchase screens move on after a scan
      var form = field.form;
      if (form && (form.getAttribute('method') || 'get').toLowerCase() === 'get' && field.type === 'search' && form.requestSubmit) form.requestSubmit();
    }
    field.focus();
  }

  function handle(text) {
    var imei = findImei(text);
    if (imei) { deliver(imei); return true; }
    status.textContent = 'That barcode is not an IMEI. Point the camera at the IMEI barcode.';
    return false;
  }

  function loadZxing() {
    return new Promise(function (resolve, reject) {
      if (window.ZXing) return resolve(window.ZXing);
      if (!ZXING_URL) return reject(new Error('missing'));
      var tag = document.createElement('script');
      tag.src = ZXING_URL; tag.onload = function () { resolve(window.ZXing); }; tag.onerror = function () { reject(new Error('load')); };
      document.head.appendChild(tag);
    });
  }

  async function start(field) {
    build();
    active = field;
    status.textContent = 'Starting the camera…';
    if (!dialog.open) dialog.showModal();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      status.textContent = 'This browser cannot use the camera here. Use a barcode scanner or type the IMEI.';
      return;
    }
    try {
      if ('BarcodeDetector' in window) {
        var supported = await window.BarcodeDetector.getSupportedFormats();
        var formats = FORMATS.filter(function (f) { return supported.indexOf(f) >= 0; });
        if (formats.length) {
          var detector = new window.BarcodeDetector({ formats: formats });
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
          video.srcObject = stream; await video.play();
          status.textContent = 'Point the camera at the IMEI barcode.';
          timer = setInterval(async function () {
            if (video.readyState < 2) return;
            try { var found = await detector.detect(video); for (var i = 0; i < found.length; i++) if (handle(found[i].rawValue)) return; } catch (e) { /* frame not ready */ }
          }, 250);
          return;
        }
      }
      var ZX = await loadZxing();
      var hints = new Map();
      hints.set(ZX.DecodeHintType.POSSIBLE_FORMATS, [ZX.BarcodeFormat.CODE_128, ZX.BarcodeFormat.CODE_39, ZX.BarcodeFormat.EAN_13, ZX.BarcodeFormat.ITF, ZX.BarcodeFormat.QR_CODE, ZX.BarcodeFormat.DATA_MATRIX]);
      hints.set(ZX.DecodeHintType.TRY_HARDER, true);
      reader = new ZX.BrowserMultiFormatReader(hints, 300);
      status.textContent = 'Point the camera at the IMEI barcode.';
      await reader.decodeFromConstraints({ video: { facingMode: { ideal: 'environment' } }, audio: false }, video, function (result) { if (result) handle(result.getText()); });
    } catch (error) {
      release();
      status.textContent = error && error.name === 'NotAllowedError'
        ? 'Camera permission was refused. Allow the camera for this site, or type the IMEI.'
        : 'The camera could not start. Use a barcode scanner or type the IMEI.';
    }
  }

  function decorate(root) {
    (root.querySelectorAll ? root.querySelectorAll('input, textarea') : []).forEach(function (el) {
      if (el.dataset.scanReady || !isImeiField(el)) return;
      el.dataset.scanReady = '1';
      var button = document.createElement('button');
      button.type = 'button'; button.className = 'scan-btn'; button.textContent = 'Scan';
      button.setAttribute('aria-label', 'Scan the IMEI with the camera');
      button.addEventListener('click', function () { start(el); });
      el.insertAdjacentElement('afterend', button);
    });
  }

  function run() { decorate(document); }
  document.addEventListener('DOMContentLoaded', run);
  document.addEventListener('turbo:load', run);
  new MutationObserver(function (records) {                // phone cards are added by the purchase screen while you work
    records.forEach(function (r) { r.addedNodes.forEach(function (n) { if (n.nodeType === 1) { if (n.matches && n.matches('input,textarea') && isImeiField(n)) decorate(n.parentNode); else decorate(n); } }); });
  }).observe(document.documentElement, { childList: true, subtree: true });
  window.CovisonScan = { findImei: findImei, luhn: luhn };     // used by the tests
})();
