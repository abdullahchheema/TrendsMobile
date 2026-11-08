/* Sidebar: mark the current page, open its section, collapse the rest (progressive enhancement). */
(function(){
  var nav=document.getElementById('main-navigation');if(!nav)return;
  var here=location.pathname.replace(/\/+$/,'')||'/';var best=null;
  nav.querySelectorAll('a[href]').forEach(function(a){
    if(a.classList.contains('brand')||a.classList.contains('primary-nav'))return;
    var p=a.pathname.replace(/\/+$/,'')||'/';
    if(p===here||(p!=='/'&&here.indexOf(p+'/')===0&&(!best||p.length>best.pathname.length)))best=a;
  });
  if(best){best.setAttribute('aria-current','page');}
  var keep=best&&best.closest('details');
  nav.querySelectorAll('details').forEach(function(d){d.open=(d===keep);});
  if(best){var d=best.closest('details');if(d)d.open=true;}
  if(!keep){var first=nav.querySelector('details');}
  try{var s=sessionStorage.getItem('cov-nav-scroll');if(s)nav.scrollTop=+s;}catch(e){}
  nav.addEventListener('click',function(){try{sessionStorage.setItem('cov-nav-scroll',nav.scrollTop);}catch(e){}});
})();
