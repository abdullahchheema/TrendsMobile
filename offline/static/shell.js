/* Sidebar: mark the current page, open its section, collapse the rest (progressive enhancement).
   Runs on first load and again whenever a page is swapped in place. */
(function(){
  function mark(){
    var nav=document.getElementById('main-navigation');if(!nav)return;
    var here=location.pathname.replace(/\/+$/,'')||'/';var best=null;
    nav.querySelectorAll('a[href]').forEach(function(a){
      if(a.classList.contains('brand')||a.classList.contains('primary-nav'))return;
      var p=a.pathname.replace(/\/+$/,'')||'/';
      if(p===here||(p!=='/'&&here.indexOf(p+'/')===0&&(!best||p.length>best.pathname.length)))best=a;
    });
    /* The server already marked the exact link (path + filter); only fall back when it marked none. */
    if(best&&!nav.querySelector('a[aria-current="page"]'))best.setAttribute('aria-current','page');
    var current=nav.querySelector('a[aria-current="page"]')||best;
    var keep=current&&current.closest('details');
    nav.querySelectorAll('details').forEach(function(d){d.open=(d===keep);});
    try{var s=sessionStorage.getItem('cov-nav-scroll');if(s)nav.scrollTop=+s;}catch(e){}
  }
  (window.covison&&window.covison.page?window.covison.page.ready:function(fn){fn()})(mark);
  document.addEventListener('click',function(e){
    var nav=document.getElementById('main-navigation');
    if(nav&&nav.contains(e.target)){try{sessionStorage.setItem('cov-nav-scroll',nav.scrollTop);}catch(x){}}
  });
})();
