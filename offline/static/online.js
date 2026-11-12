(()=>{
 'use strict';
 const root=document.getElementById('online-media');if(!root)return;
 const grid=document.getElementById('media-grid'),drop=document.getElementById('media-drop'),input=document.getElementById('media-input'),errorBox=document.getElementById('media-error');
 const max=Number(root.dataset.max),maxBytes=Number(root.dataset.maxMb)*1048576;
 let images=JSON.parse(document.getElementById('online-images').textContent);
 const csrf=()=>decodeURIComponent(document.cookie.split('; ').find(v=>v.startsWith('csrftoken='))?.slice(10)||document.querySelector('[name=csrfmiddlewaretoken]')?.value||'');
 const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
 const say=text=>{errorBox.textContent=text||'';};
 const json=(url,body)=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify(body)}).then(async r=>{const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||'The server could not save this.');return d;});
 const updateUrl=id=>root.dataset.update.replace(/[0-9a-f-]{36}\/$/,'')+id+'/';
 const deleteUrl=id=>root.dataset.delete.replace(/[0-9a-f-]{36}\/delete\/$/,'')+id+'/delete/';

 function render(){
  grid.replaceChildren();
  images.forEach((img,index)=>{
   const tile=make('li',undefined,'media-tile');tile.draggable=true;tile.dataset.id=img.id;
   const pic=make('img');pic.src=img.url;pic.alt=img.alt||'';pic.loading='lazy';tile.append(pic);
   if(index===0)tile.append(make('span','Cover','cover-badge'));
   const alt=make('input',undefined,'alt');alt.type='text';alt.value=img.alt||'';alt.maxLength=200;alt.placeholder='Describe the photo (for accessibility)';alt.setAttribute('aria-label','Photo description');
   alt.addEventListener('change',()=>{img.alt=alt.value;json(updateUrl(img.id),{alt:alt.value}).catch(e=>say(e.message));});
   const actions=make('div',undefined,'tile-actions');
   if(index>0){const cover=make('button','Make cover');cover.type='button';cover.addEventListener('click',()=>{images.splice(0,0,images.splice(index,1)[0]);save();});actions.append(cover);}else actions.append(make('span'));
   const del=make('button','Remove');del.type='button';del.addEventListener('click',()=>remove(img));actions.append(del);
   tile.append(actions,alt);
   tile.addEventListener('dragstart',e=>{tile.classList.add('dragging');e.dataTransfer.setData('text/plain',img.id);e.dataTransfer.effectAllowed='move';});
   tile.addEventListener('dragend',()=>tile.classList.remove('dragging'));
   tile.addEventListener('dragover',e=>{if(e.dataTransfer.types.includes('Files'))return;e.preventDefault();tile.classList.add('drop-target');});
   tile.addEventListener('dragleave',()=>tile.classList.remove('drop-target'));
   tile.addEventListener('drop',e=>{if(e.dataTransfer.types.includes('Files'))return;e.preventDefault();tile.classList.remove('drop-target');
    const from=images.findIndex(i=>i.id===e.dataTransfer.getData('text/plain')),to=index;if(from<0||from===to)return;images.splice(to,0,images.splice(from,1)[0]);save();});
   grid.append(tile);
  });
  drop.hidden=images.length>=max;
 }
 function save(){render();json(root.dataset.order,{order:images.map(i=>i.id)}).then(()=>say('')).catch(e=>say(e.message));}
 function remove(img){
  if(!window.confirm('Remove this photo from the product?'))return;
  json(deleteUrl(img.id),{}).then(d=>{images=images.filter(i=>i.id!==img.id);render();say(d.unpublished?'That was the last photo, so the product was taken off the website. Add a photo and publish it again.':'');}).catch(e=>say(e.message));
 }

 /* Resize big photos in the browser first: faster upload, smaller storage, same look on the website. */
 async function prepare(file){
  if(!/^image\/(jpeg|png|webp)$/.test(file.type))throw new Error(file.name+': only JPG, PNG or WebP photos can be added.');
  let bitmap;try{bitmap=await createImageBitmap(file);}catch{throw new Error(file.name+' could not be read as a photo.');}
  const longest=Math.max(bitmap.width,bitmap.height),scale=Math.min(1,1600/longest);
  if(scale===1&&file.size<=900*1024)return file;
  const canvas=document.createElement('canvas');canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);
  canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
  const blob=await new Promise(done=>canvas.toBlob(done,'image/webp',0.86));
  if(!blob||blob.type!=='image/webp')throw new Error(file.name+' could not be resized.');
  return blob;
 }
 function send(blob,name,tile,variant){
  return new Promise((done,fail)=>{
   const form=new FormData();form.append('file',blob,name);if(variant)form.append('variant',variant);const xhr=new XMLHttpRequest();xhr.open('POST',root.dataset.upload);xhr.setRequestHeader('X-CSRFToken',csrf());
   xhr.upload.onprogress=e=>{if(tile&&e.lengthComputable)tile.querySelector('.bar i').style.width=Math.round(e.loaded*100/e.total)+'%';};
   xhr.onload=()=>{let d={};try{d=JSON.parse(xhr.responseText);}catch{}xhr.status===200&&d.id?done(d):fail(new Error(d.error||'Upload failed.'));};
   xhr.onerror=()=>fail(new Error('Could not reach the server. Check the connection and try again.'));xhr.send(form);
  });
 }
 let busy=false;
 async function add(files){
  if(busy)return;busy=true;say('');
  try{
   for(const file of files){
    if(images.length>=max){say('A product can have at most '+max+' photos.');break;}
    const tile=make('li',undefined,'media-tile uploading'),bar=make('div',undefined,'bar');bar.append(make('i'));tile.append(bar,make('p',file.name,'muted'));grid.append(tile);
    try{const blob=await prepare(file);if(blob.size>maxBytes)throw new Error(file.name+' is still too large after resizing.');images.push(await send(blob,file.name.replace(/\.[^.]+$/,'')+(blob===file?'':'.webp'),tile));}
    catch(problem){say(problem.message);}
    tile.remove();render();
   }
   if(images.length===1)say('This page is now live on the website.');
  }finally{busy=false;render();}
 }
 const reload=()=>{if(window.Turbo)window.Turbo.visit(location.href,{action:'replace'});else location.reload();};
 /* a photo for one colour/storage only */
 document.querySelectorAll('[data-variant-photo]').forEach(box=>box.addEventListener('change',async()=>{
  const files=[...box.files];box.value='';say('');
  for(const file of files){try{const blob=await prepare(file);if(blob.size>maxBytes)throw new Error(file.name+' is still too large after resizing.');await send(blob,file.name.replace(/\.[^.]+$/,'')+(blob===file?'':'.webp'),null,box.dataset.variantPhoto);}catch(problem){say(problem.message);return;}}
  reload();}));
 document.querySelectorAll('[data-remove-photo]').forEach(button=>button.addEventListener('click',()=>{
  if(!window.confirm('Remove this photo?'))return;json(deleteUrl(button.dataset.removePhoto),{}).then(reload).catch(e=>say(e.message));}));
 const addRow=document.getElementById('add-spec-row');
 if(addRow)addRow.addEventListener('click',()=>{const box=document.getElementById('custom-specs');const n=box.querySelectorAll('.sh-spec-row').length+1;
  const row=document.createElement('div');row.className='sh-spec-row';
  const a=document.createElement('input');a.name='other_label_'+n;a.placeholder='Name';a.maxLength=80;const b=document.createElement('input');b.name='other_value_'+n;b.placeholder='Value';b.maxLength=300;row.append(a,b);box.append(row);a.focus();});
 drop.addEventListener('click',()=>input.click());
 drop.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();input.click();}});
 input.addEventListener('change',()=>{add([...input.files]);input.value='';});
 for(const type of ['dragenter','dragover'])drop.addEventListener(type,e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();drop.classList.add('over');}});
 for(const type of ['dragleave','drop'])drop.addEventListener(type,()=>drop.classList.remove('over'));
 drop.addEventListener('drop',e=>{e.preventDefault();add([...e.dataTransfer.files]);});
 render();
})();
