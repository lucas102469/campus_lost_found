// 등록·수정 폼: 건물별 층 선택과 실내·외 입력 전환, 여러 사진의 미리보기·파일명·삭제를 처리한다. 최종 검증은 서버에서도 한다.
// 선택한 건물의 층만 보여준다. 건물 변경 시 다른 폼 입력을 지우지 않는다.
document.addEventListener('DOMContentLoaded',()=>{
 const box=document.querySelector('.location-fields');
 if(box){
  const buildings=JSON.parse(box.dataset.buildings),building=box.querySelector('[name=building]'),floor=box.querySelector('[name=floor]');
  function updateFloor(){const selected=floor.value||floor.dataset.value;floor.replaceChildren(...buildings[building.value].map(f=>{const opt=document.createElement('option');opt.value=f;opt.textContent=f;return opt}));floor.value=buildings[building.value].includes(selected)?selected:'1층';floor.dataset.value=''}
  updateFloor();building.addEventListener('change',updateFloor);
  function toggle(){const external=box.querySelector('[name=storage_type]:checked').value==='external';box.querySelector('[data-internal]').hidden=external;box.querySelector('[data-external]').hidden=!external;box.querySelector('[name=room]').required=!external;box.querySelector('[name=latitude]').required=external;box.querySelector('[name=longitude]').required=external;box.querySelector('[name=location_note]').required=external;if(external)window.dispatchEvent(new Event('map-visible'))}
  box.querySelectorAll('[name=storage_type]').forEach(r=>r.addEventListener('change',toggle));toggle();
 }
 const input=document.querySelector('[data-photo-input]');if(input){
 let selected=[];const preview=document.querySelector('[data-photo-preview]'),names=document.querySelector('[data-photo-names]');let existing=JSON.parse(names.dataset.existing||'[]');
 const signature=file=>file.name+'|'+file.size+'|'+file.lastModified;
 function sync(){const transfer=new DataTransfer();selected.forEach(file=>transfer.items.add(file));input.files=transfer.files}
 function render(){
 (preview._urls||[]).forEach(url=>URL.revokeObjectURL(url));preview._urls=selected.map(file=>URL.createObjectURL(file));preview.replaceChildren();preview.dataset.index='0';
 const images=[...existing.map(file=>({url:file.url,name:file.name})),...selected.map((file,index)=>({url:preview._urls[index],name:file.name}))];
 images.forEach((file,index)=>{const img=document.createElement('img');img.className='gallery-slide';img.src=file.url;img.alt=file.name;img.hidden=index!==0;preview.append(img)});
 if(!images.length){const text=document.createElement('b');text.textContent='사진 업로드';preview.append(text)}
 if(images.length>1)for(const [step,label,cls] of [[-1,'이전 사진','gallery-prev'],[1,'다음 사진','gallery-next']]){const button=document.createElement('button');button.type='button';button.className='gallery-arrow '+cls;button.dataset.galleryStep=step;button.setAttribute('aria-label',label);button.textContent=step===-1?'<':'>';preview.append(button)}
 names.replaceChildren();
 function row(name,remove,filename){const li=document.createElement('li'),text=document.createElement('span'),button=document.createElement('button');text.textContent=name;button.type='button';button.className='photo-remove';button.textContent='×';button.setAttribute('aria-label',name+' 삭제');button.addEventListener('click',remove);li.append(text);li.append(button);if(filename){const hidden=document.createElement('input');hidden.type='hidden';hidden.name='keep_photo';hidden.value=filename;li.append(hidden)}names.append(li)}
 existing.forEach((file,index)=>row(file.name,()=>{existing.splice(index,1);render()},file.filename));
 selected.forEach((file,index)=>row(file.name,()=>{selected.splice(index,1);sync();render()}));
 }
 preview.addEventListener('click',event=>{if(!event.target.closest('button'))input.click()});
 input.addEventListener('change',()=>{
 const merged=[...selected];for(const file of Array.from(input.files))if(!merged.some(existing=>signature(existing)===signature(file)))merged.push(file);
 if(merged.reduce((sum,file)=>sum+file.size,0)>5*1024*1024||merged.some(file=>!/\.(jpe?g|png)$/i.test(file.name)||(file.type&&!['image/jpeg','image/png'].includes(file.type)))){alert('JPG, JPEG, PNG 사진만 가능하며 전체 합계는 5MB 이하여야 합니다.');sync();return}
 selected=merged;sync();render();
 });render();
 }
});
