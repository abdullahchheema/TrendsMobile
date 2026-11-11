/* Shared UI behaviour: toasts, confirm dialogs, global search palette, shortcuts, submit feedback. No dependencies. */
(function(){
  'use strict';
  var $=function(s,r){return (r||document).querySelector(s)},$$=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))};

  /* ---- toasts: server messages are rendered into .toast-region; add dismiss + auto-hide ---- */
  var page=(window.covison&&window.covison.page)||{ready:function(fn){fn()},visit:function(u){location.href=u}};
  function region(){return $('.toast-region')}
  function dismiss(t){t.classList.add('is-leaving');setTimeout(function(){t.remove()},220)}
  function wire(t){
    var close=t.querySelector('button');if(close)close.addEventListener('click',function(){dismiss(t)});
    if(!t.classList.contains('error'))setTimeout(function(){if(t.isConnected)dismiss(t)},6000);
  }
  page.ready(function(){$$('.toast',region()).forEach(wire)});
  window.covisonToast=function(message,kind){
    var box=region();if(!box)return;var t=document.createElement('div');t.className='toast '+(kind||'');t.setAttribute('role',kind==='error'?'alert':'status');
    var p=document.createElement('p');p.textContent=message;var b=document.createElement('button');b.type='button';b.setAttribute('aria-label','Dismiss');b.textContent='✕';
    t.appendChild(p);t.appendChild(b);box.appendChild(t);wire(t);
  };

  /* ---- confirm dialog: <button data-confirm="Cancel this invoice?"> or <form data-confirm="..."> ---- */
  var confirmDialog=$('#confirm-dialog'),pending=null;
  if(confirmDialog&&typeof confirmDialog.showModal==='function'){
    document.addEventListener('click',function(e){
      var el=e.target.closest('[data-confirm]');if(!el||el.tagName==='FORM')return;
      if(el.dataset.confirmed)return;e.preventDefault();ask(el.dataset.confirm,function(){el.dataset.confirmed='1';el.click();delete el.dataset.confirmed});
    });
    document.addEventListener('submit',function(e){
      var f=e.target;if(!f.matches('form[data-confirm]')||f.dataset.confirmed)return;e.preventDefault();
      ask(f.dataset.confirm,function(){f.dataset.confirmed='1';f.requestSubmit?f.requestSubmit():f.submit()});
    },true);
    $('[data-confirm-ok]',confirmDialog).addEventListener('click',function(){confirmDialog.close('ok')});
    $('[data-confirm-cancel]',confirmDialog).addEventListener('click',function(){confirmDialog.close('cancel')});
    confirmDialog.addEventListener('close',function(){var cb=pending;pending=null;if(cb&&confirmDialog.returnValue==='ok')cb()});
  }
  function ask(message,cb){pending=cb;$('[data-confirm-message]',confirmDialog).textContent=message;confirmDialog.returnValue='';confirmDialog.showModal();$('[data-confirm-cancel]',confirmDialog).focus()}

  /* ---- submit feedback: spinner on the pressed button, block double submits ---- */
  document.addEventListener('submit',function(e){
    var f=e.target;if(e.defaultPrevented||f.dataset.noLoading!==undefined||f.method.toLowerCase()!=='post'||!f.checkValidity())return;
    var b=e.submitter||f.querySelector('button[type=submit],button:not([type])');if(!b||b.classList.contains('is-loading'))return;
    setTimeout(function(){b.classList.add('is-loading');b.setAttribute('aria-busy','true')},0);
    setTimeout(function(){b.classList.remove('is-loading');b.removeAttribute('aria-busy')},15000);
  });

  /* ---- global search palette ---- */
  var palette=$('#palette');
  if(palette&&typeof palette.showModal==='function'){
    var input=$('.palette-input',palette),list=$('.palette-list',palette),empty=$('.palette-empty',palette),url=palette.dataset.url,timer=null,items=[],active=0,seq=0;
    var pages=[];
    function loadPages(){pages=$$('#main-navigation a[href]').filter(function(a){return !a.classList.contains('brand')}).map(function(a){return {kind:'Page',label:a.textContent.trim(),href:a.getAttribute('href')}})}
    function render(){
      list.innerHTML='';items.forEach(function(it,i){
        var li=document.createElement('li');li.setAttribute('role','option');li.setAttribute('aria-selected',i===active?'true':'false');
        li.innerHTML='<span class="kind"></span><span class="label"></span><span class="hint"></span>';
        li.children[0].textContent=it.kind;li.children[1].textContent=it.label;li.children[2].textContent=it.hint||'';
        li.addEventListener('click',function(){go(it)});li.addEventListener('mousemove',function(){active=i;mark()});list.appendChild(li);
      });
      empty.hidden=items.length>0;
    }
    function mark(){$$('li',list).forEach(function(li,i){li.setAttribute('aria-selected',i===active?'true':'false')})}
    function go(it){if(!it)return;if(palette.open)palette.close();page.visit(it.href)}
    function search(q){
      var local=pages.filter(function(p){return !q||p.label.toLowerCase().indexOf(q.toLowerCase())>-1}).slice(0,q?4:8);
      if(q.length<2){items=local;active=0;render();return}
      var my=++seq;fetch(url+'?q='+encodeURIComponent(q),{headers:{'Accept':'application/json'},credentials:'same-origin'}).then(function(r){return r.ok?r.json():{results:[]}}).then(function(d){
        if(my!==seq)return;items=(d.results||[]).concat(local);active=0;render();
      }).catch(function(){items=local;render()});
    }
    function open(){loadPages();palette.showModal();input.value='';search('');input.focus()}
    input.addEventListener('input',function(){clearTimeout(timer);timer=setTimeout(function(){search(input.value.trim())},160)});
    input.addEventListener('keydown',function(e){
      if(e.key==='ArrowDown'){e.preventDefault();active=Math.min(active+1,items.length-1);mark()}
      else if(e.key==='ArrowUp'){e.preventDefault();active=Math.max(active-1,0);mark()}
      else if(e.key==='Enter'){e.preventDefault();go(items[active])}
    });
    palette.addEventListener('click',function(e){if(e.target===palette)palette.close()});
    document.addEventListener('click',function(e){if(e.target.closest('[data-open-search]'))open()});
    document.addEventListener('keydown',function(e){
      var typing=/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)||document.activeElement.isContentEditable;
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();palette.open?palette.close():open();return}
      if(typing||e.ctrlKey||e.metaKey||e.altKey)return;
      if(e.key==='/'){e.preventDefault();open()}
      else if(e.key==='n'){var s=$('#main-navigation a[href*="/entry/sale/"]');if(s)page.visit(s.href)}
      else if(e.key==='b'){var b=$('#main-navigation a[href*="/entry/purchase/"]');if(b)page.visit(b.href)}
    });
  }
})();

/* Sale detail: tick every returnable phone for a full return */
document.addEventListener('click',function(e){var b=e.target.closest('[data-select-returns]');if(!b)return;var f=b.closest('form');if(!f)return;f.querySelectorAll('input[type=checkbox][name=devices]').forEach(function(c){c.checked=true});f.querySelectorAll('input[name^=return_qty_]').forEach(function(i){i.value=i.max||i.value})});
