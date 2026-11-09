/* Bulk purchase builder: rows of phone variants, continuous IMEI scanning, live totals. Server re-validates everything. */
(function(){
  'use strict';
  var form=document.querySelector('[data-bulk]');if(!form)return;
  var products=JSON.parse(document.getElementById('bulk-products').textContent);
  var conditions=JSON.parse(document.getElementById('bulk-conditions').textContent),types=JSON.parse(document.getElementById('bulk-types').textContent);
  var body=form.querySelector('[data-rows]'),hidden=form.elements.rows_json,scan=form.querySelector('[data-scan]'),status=form.querySelector('[data-scan-status]'),totalEl=form.querySelector('[data-total]'),countEl=form.querySelector('[data-count]');
  var rows=[],active=0;
  function luhn(n){if(!/^\d{15}$/.test(n))return false;var s=0;for(var i=0;i<15;i++){var d=+n[i];if(i%2){d*=2;if(d>9)d-=9}s+=d}return s%10===0}
  function blank(src){return {brand:src?src.brand:'',model:src?src.model:'',storage:src?src.storage:'',colour:src?src.colour:'',notes:src?src.notes:'',product:src?src.product:'',quantity:src?src.quantity:1,condition:src?src.condition:'Good',device_type:src?src.device_type:'Used',grade:src?src.grade:'',pta_status:src?src.pta_status:'PTA',cost:src?src.cost:'',cost_mode:src?src.cost_mode:'each',asking_price:src?src.asking_price:'',imeis:''}}
  function opts(list,sel,labelOf){return list.map(function(v){var val=typeof v==='string'?v:v.id,label=labelOf?labelOf(v):v;return '<option value="'+val+'"'+(val===sel?' selected':'')+'>'+label.replace(/</g,'&lt;')+'</option>'}).join('')}
  function lines(r){return (r.imeis||'').split(/\n/).map(function(x){return x.trim()}).filter(Boolean)}
  function numbers(line){return line.replace(/,/g,' ').split(/\s+/).filter(Boolean)}
  function render(){
    body.innerHTML='';
    rows.forEach(function(r,i){
      var tr=document.createElement('tr');tr.dataset.i=i;if(i===active)tr.classList.add('active-row');
      var cat=window.PhoneCatalog||{models:function(){return []},storage:[]},esc=function(v){return String(v||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')};
      tr.innerHTML='<td class="phone-cell"><input data-f="brand" list="phone-brands" value="'+esc(r.brand)+'" placeholder="Brand (choose or type)" autocomplete="off" aria-label="Brand">'+
        '<input data-f="model" list="models-'+i+'" value="'+esc(r.model)+'" placeholder="Model (choose or type)" autocomplete="off" aria-label="Model"><datalist id="models-'+i+'">'+cat.models(r.brand).map(function(m){return '<option value="'+esc(m)+'">'}).join('')+'</datalist>'+
        '<select data-f="storage" aria-label="Storage"><option value="">Storage</option>'+opts(cat.storage,r.storage)+'</select>'+
        '<input data-f="colour" list="phone-colours" value="'+esc(r.colour)+'" placeholder="Colour" autocomplete="off" aria-label="Colour">'+
        '<input data-f="notes" value="'+esc(r.notes)+'" maxlength="400" placeholder="Notes: battery changed, display replaced…" autocomplete="off" aria-label="Notes about this phone"></td>'+
        '<td><input data-f="quantity" type="number" min="1" max="100" value="'+r.quantity+'" aria-label="Quantity"></td>'+
        '<td><select data-f="condition" aria-label="Condition">'+opts(conditions,r.condition)+'</select><select data-f="device_type" aria-label="Phone type">'+opts(types,r.device_type)+'</select></td>'+
        '<td><input data-f="grade" type="number" min="1" max="10" placeholder="1-10" value="'+(r.grade||'')+'" aria-label="Grade out of 10"><select data-f="pta_status" aria-label="PTA"><option'+(r.pta_status==='PTA'?' selected':'')+'>PTA</option><option'+(r.pta_status==='Non-PTA'?' selected':'')+'>Non-PTA</option></select></td>'+
        '<td><input data-f="cost" type="number" min="0" step="0.01" value="'+(r.cost||'')+'" aria-label="Buying price"><select data-f="cost_mode" aria-label="Price is"><option value="each"'+(r.cost_mode==='each'?' selected':'')+'>each</option><option value="total"'+(r.cost_mode==='total'?' selected':'')+'>line total</option></select></td>'+
        '<td><textarea data-f="imeis" rows="3" placeholder="One IMEI per line. Dual-SIM: both on one line." aria-label="IMEIs">'+(r.imeis||'').replace(/</g,'&lt;')+'</textarea><small data-imei-note></small></td>'+
        '<td><button type="button" data-dup title="Copy this line">Copy</button> <button type="button" data-del title="Remove this line" aria-label="Remove line">&#10005;</button></td>';
      body.appendChild(tr);
    });
    summarise();
  }
  function summarise(){
    var total=0,phones=0,all={},dup=false;
    rows.forEach(function(r,i){
      var q=+r.quantity||0,c=+r.cost||0;phones+=q;total+=r.cost_mode==='total'?c:c*q;
      var ls=lines(r),bad=[];ls.forEach(function(l){numbers(l).forEach(function(n){if(!luhn(n))bad.push(n);if(all[n]){dup=true;bad.push(n)}all[n]=1})});
      var note=body.children[i]&&body.children[i].querySelector('[data-imei-note]');
      if(note){note.textContent=ls.length+' of '+q+' phones scanned'+(bad.length?' · check: '+bad.slice(0,2).join(', '):'');note.style.color=bad.length||(ls.length&&ls.length!==q)?'var(--warn)':'var(--ink-3)'}
    });
    totalEl.textContent='PKR '+total.toLocaleString('en-PK',{minimumFractionDigits:2,maximumFractionDigits:2});countEl.textContent=phones+' phone'+(phones===1?'':'s')+' on '+rows.length+' line'+(rows.length===1?'':'s');
    hidden.value=JSON.stringify(rows);return !dup;
  }
  body.addEventListener('input',function(e){var tr=e.target.closest('tr'),f=e.target.dataset.f;if(!tr||!f)return;rows[+tr.dataset.i][f]=e.target.value;if(f==='brand'){var dl=tr.querySelector('datalist'),cat=window.PhoneCatalog;if(dl&&cat){dl.innerHTML=cat.models(e.target.value).map(function(m){return '<option value="'+String(m).replace(/"/g,'&quot;')+'">'}).join('')}}summarise()});
  body.addEventListener('focusin',function(e){var tr=e.target.closest('tr');if(tr){active=+tr.dataset.i;Array.prototype.forEach.call(body.children,function(x,i){x.classList.toggle('active-row',i===active)})}});
  body.addEventListener('click',function(e){var tr=e.target.closest('tr');if(!tr)return;var i=+tr.dataset.i;
    if(e.target.closest('[data-del]')){rows.splice(i,1);if(!rows.length)rows.push(blank());active=Math.min(active,rows.length-1);render()}
    else if(e.target.closest('[data-dup]')){rows.splice(i+1,0,blank(rows[i]));active=i+1;render()}});
  form.querySelector('[data-add]').addEventListener('click',function(){rows.push(blank(rows[rows.length-1]));active=rows.length-1;render()});
  /* scanner: every Enter sends one IMEI to the active line; when that line is full it moves to the next incomplete one */
  scan.addEventListener('keydown',function(e){
    if(e.key!=='Enter')return;e.preventDefault();var n=scan.value.replace(/\D/g,'');scan.value='';
    if(!n)return;if(!luhn(n)){status.textContent='“'+n+'” is not a valid IMEI. Scan again.';status.className='error';return}
    var seen=rows.some(function(r){return lines(r).some(function(l){return numbers(l).indexOf(n)>-1})});if(seen){status.textContent='Already scanned: '+n;status.className='error';return}
    var r=rows[active];if(!r||lines(r).length>=(+r.quantity||0)){var next=rows.findIndex(function(x){return lines(x).length<(+x.quantity||0)});if(next>-1){active=next;r=rows[next]}}
    r.imeis=(r.imeis?r.imeis.replace(/\n+$/,'')+'\n':'')+n;status.textContent='Added '+n+' to line '+(active+1)+' ('+lines(r).length+' of '+(+r.quantity||0)+')';status.className='muted';
    render();scan.focus();
  });
  form.addEventListener('submit',function(e){
    var ok=summarise();var empty=rows.some(function(r){return !(r.product||(r.brand&&r.model&&r.storage&&r.colour))||!(+r.quantity)||!(+r.cost)});
    if(!ok){e.preventDefault();status.textContent='Fix the duplicate IMEIs before saving.';status.className='error';return}
    if(empty){e.preventDefault();status.textContent='Every line needs a brand, model, storage, colour, quantity and buying price.';status.className='error'}
  },true);
  try{var saved=JSON.parse(hidden.value||'[]');rows=Array.isArray(saved)&&saved.length?saved.map(function(r){return Object.assign(blank(),r)}):[blank()]}catch(e){rows=[blank()]}
  render();
})();
