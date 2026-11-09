/* Brand > model suggestions for the buy screens. Brand and model stay free text: suggestions only help, they never limit. */
(function(){
  'use strict';
  var el=document.getElementById('phone-catalog');if(!el)return;
  var cat=JSON.parse(el.textContent);
  window.PhoneCatalog={
    models:function(brand){return cat.models[String(brand||'').trim().toLowerCase().replace(/\s+/g,' ')]||[]},
    brands:cat.brands,colours:cat.colours,storage:cat.storage
  };
  var form=document.querySelector('[data-easy-entry]');if(!form||!form.elements.brand)return;
  var list=document.getElementById('phone-models');
  function fill(){list.innerHTML='';window.PhoneCatalog.models(form.elements.brand.value).forEach(function(m){var o=document.createElement('option');o.value=m;list.appendChild(o)})}
  form.elements.brand.addEventListener('input',fill);fill();
})();
