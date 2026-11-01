(()=>{
 'use strict';
 const button=document.getElementById('add-journal-row');if(!button)return;
 button.addEventListener('click',()=>{
  const total=document.getElementById('id_rows-TOTAL_FORMS'),index=Number(total.value);
  if(index>=30){button.disabled=true;return;}
  const fragment=document.getElementById('journal-empty-row').content.cloneNode(true);
  for(const element of fragment.querySelectorAll('[name],[id],[for]'))for(const attr of ['name','id','for'])if(element.hasAttribute(attr))element.setAttribute(attr,element.getAttribute(attr).replaceAll('__prefix__',String(index)));
  document.querySelector('#journal-rows tbody').append(fragment);total.value=String(index+1);if(index+1>=30)button.disabled=true;
 });
})();
