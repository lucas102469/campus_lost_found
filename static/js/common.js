// 화면 공통 동작: 알림 수 갱신, 아이콘, 확인창, 사진 넘기기와 헤더 알림 팝업을 처리한다.
// 공통 아이콘, 삭제 확인, 인앱 알림 수 갱신. 텍스트는 HTML 삽입하지 않는다.
document.addEventListener('DOMContentLoaded',()=>{
 if(window.lucide)lucide.createIcons();
 document.querySelectorAll('form[data-confirm]').forEach(form=>form.addEventListener('submit',e=>{if(!confirm(form.dataset.confirm))e.preventDefault()}));
 const badge=document.getElementById('notification-count');
 if(badge)setInterval(async()=>{if(document.hidden)return;try{const r=await fetch('/api/notifications/count');if(!r.ok)return;const data=await r.json();badge.textContent=data.count;badge.hidden=!data.count}catch(e){}},5000);
});

// 목록 카드 안의 버튼 클릭도 상세 이동 없이 사진만 넘긴다.
document.addEventListener('click',event=>{const button=event.target.closest('[data-gallery-step]');if(!button)return;event.preventDefault();event.stopPropagation();const gallery=button.closest('[data-gallery]'),slides=Array.from(gallery.querySelectorAll('.gallery-slide'));if(!slides.length)return;const next=((Number(gallery.dataset.index)||0)+Number(button.dataset.galleryStep)+slides.length)%slides.length;slides.forEach((slide,index)=>slide.hidden=index!==next);gallery.dataset.index=next;});

document.addEventListener('DOMContentLoaded',()=>{
 const toggle=document.getElementById('notification-toggle'),popup=document.getElementById('notification-popup'),menu=document.getElementById('notification-menu'),preview=document.getElementById('notification-preview');if(!toggle)return;
 function close(){popup.hidden=true;toggle.setAttribute('aria-expanded','false')}
 toggle.addEventListener('click',async()=>{
  if(!popup.hidden){close();return}popup.hidden=false;toggle.setAttribute('aria-expanded','true');preview.textContent='알림을 불러오고 있습니다…';
  try{const response=await fetch('/api/notifications/recent');if(!response.ok)throw new Error();const data=await response.json();preview.replaceChildren();
   if(!data.notifications.length){const empty=document.createElement('p');empty.className='p-4 text-sm text-slate-400';empty.textContent='새로운 알림이 없습니다.';preview.append(empty)}
   for(const n of data.notifications){const form=document.createElement('form');form.method='post';form.action='/notifications/'+n.id+'/open';const token=document.createElement('input');token.type='hidden';token.name='csrf_token';token.value=document.querySelector('meta[name=csrf-token]').content;const link=document.createElement('button');link.type='submit';link.className='block w-full text-left p-4 border-b text-sm hover:bg-slate-50';const body=document.createElement('p');body.textContent=n.body;if(!n.is_read)body.className='font-semibold';const date=document.createElement('small');date.textContent=n.created_at;date.className='text-slate-400';link.append(body,date);form.append(token,link);preview.append(form)}
  }catch(e){preview.textContent='알림을 불러오지 못했습니다. 전체 알림 내역을 확인해주세요.'}
 });document.addEventListener('click',e=>{if(!menu.contains(e.target))close()});document.addEventListener('keydown',e=>{if(e.key==='Escape')close()});
});
