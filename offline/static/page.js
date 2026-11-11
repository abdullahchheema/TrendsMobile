/* Page lifecycle helper. Pages are now swapped in place by Turbo (no full reload), so scripts must not assume
   "page load" happens once. Scripts that run once in <head> use ready(); scripts inside a page use leave()
   to undo anything they attached to document/window. Works the same when Turbo is absent (receipt, login). */
(function(){
  'use strict';
  if(window.covison&&window.covison.page)return;
  if(window.Turbo){
    if(window.Turbo.config&&window.Turbo.config.drive)window.Turbo.config.drive.progressBarDelay=120;
    else if(window.Turbo.setProgressBarDelay)window.Turbo.setProgressBarDelay(120);
  }
  var leaving=[],starts=[];
  function run(list){list.slice().forEach(function(fn){try{fn()}catch(e){if(window.console)console.error(e)}})}
  var page={
    /* Run now and again after every in-place page swap. */
    ready:function(fn){starts.push(fn);run([fn])},
    /* Run once, just before this page's content is swapped out. */
    leave:function(fn){leaving.push(fn)},
    /* Open a page the way a link click would (in place when possible). */
    visit:function(url){if(window.Turbo&&window.Turbo.visit)window.Turbo.visit(url);else window.location.href=url},
    /* Ask before leaving when unsaved work is in flight; return true to allow. */
    guard:null
  };
  window.covison=window.covison||{};window.covison.page=page;
  document.addEventListener('turbo:before-visit',function(event){
    if(typeof page.guard==='function'&&page.guard()===false)event.preventDefault();
  });
  document.addEventListener('turbo:before-render',function(){var list=leaving;leaving=[];page.guard=null;run(list)});
  document.addEventListener('turbo:render',function(){
    run(starts);
    /* Keyboard and screen-reader users: land on the new content, like a normal page load would. */
    var main=document.getElementById('content');
    if(main&&(!document.activeElement||document.activeElement===document.body))main.focus({preventScroll:true});
  });
  /* A save that never reached the server must not look like it finished. */
  document.addEventListener('turbo:fetch-request-error',function(event){
    var form=event.target&&event.target.closest&&event.target.closest('form');
    if(form&&typeof window.covisonToast==='function')window.covisonToast('Could not reach the server. Check the connection, then check the list before saving again.','error');
  });
  /* Back button on every page (falls back to the dashboard when there is nothing to go back to). */
  document.addEventListener('click',function(event){
    var button=event.target.closest&&event.target.closest('[data-go-back]');if(!button)return;
    event.preventDefault();
    if(window.history.length>1)window.history.back();else if(window.Turbo)window.Turbo.visit('/desk/mobile-shop');else window.location.href='/desk/mobile-shop';
  });
  /* Scrolling the page with the wheel must never change a number being typed in. */
  document.addEventListener('wheel',function(event){
    var el=document.activeElement;if(el&&el.tagName==='INPUT'&&el.type==='number'&&el===event.target)el.blur();
  },{passive:true});
  document.addEventListener('turbo:submit-end',function(event){
    var form=event.target;if(!form||!form.querySelectorAll)return;
    form.querySelectorAll('button.is-loading').forEach(function(b){b.classList.remove('is-loading');b.removeAttribute('aria-busy')});
  });
})();
