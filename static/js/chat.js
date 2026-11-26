// 3초 간격으로 당사자 메시지만 조회. 입력 중인 메시지를 보존한다.
document.addEventListener('DOMContentLoaded',()=>{
 const box=document.getElementById('chat-messages');if(!box)return;box.scrollTop=box.scrollHeight;
 const input=document.querySelector('.chat-composer input[name=body]'),key='chat-draft-'+box.dataset.user+'-'+box.dataset.chatId;
 if(input){input.value=sessionStorage.getItem(key)||'';input.addEventListener('input',()=>sessionStorage.setItem(key,input.value));input.form.addEventListener('submit',()=>sessionStorage.removeItem(key))}
 setInterval(async()=>{if(document.hidden)return;try{const response=await fetch('/api/chats/'+box.dataset.chatId+'/messages');if(!response.ok)return;const data=await response.json();const last=data.messages.length?data.messages[data.messages.length-1].id:0;if(last!==Number(box.dataset.lastId)||data.shared!==(box.dataset.shared==='1')||data.completed!==(box.dataset.completed==='1')){if(input)sessionStorage.setItem(key,input.value);location.reload()}}catch(e){}},3000);
});
