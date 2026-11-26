// 지도: 브라우저 위치 권한으로 현재 좌표를 얻고, Flask 경로 API의 JSON을 받아 도보 경로를 지도에 그린다. 위치 및 API 실패는 안내한다.
// Leaflet + OpenStreetMap. 외부 등록 화면을 열면 GPS 요청. 기존 좌표는 유지한다.
function gps(){return new Promise((resolve,reject)=>{if(!navigator.geolocation)return reject(new Error('현재 위치 기능을 사용할 수 없습니다.'));navigator.geolocation.getCurrentPosition(p=>resolve(p.coords),e=>reject(new Error(e.code===1?'위치 권한을 허용하거나 좌표를 직접 입력해주세요.':'현재 위치를 가져오지 못했습니다. 좌표를 직접 입력할 수도 있습니다.')),{enableHighAccuracy:true,timeout:10000,maximumAge:30000})})}
document.addEventListener('DOMContentLoaded',()=>{
 const canvas=document.querySelector('[data-map]');if(!canvas)return;
 const box=canvas.closest('.location-fields'),status=document.querySelector('[data-map-status]');let map,marker,userMarker,timer,loaded=false;
 let target={latitude:Number(canvas.dataset.lat)||37.8074,longitude:Number(canvas.dataset.lng)||127.0712};
 const tell=message=>{if(status)status.textContent=message};
 function draw(){
  if(!window.L){tell('지도 프로그램을 불러오지 못했습니다. 좌표와 위치 설명으로 계속 진행할 수 있습니다.');return}
  if(canvas.clientWidth===0)return;
  if(!map){canvas.classList.add('leaflet-map');map=L.map(canvas).setView([target.latitude,target.longitude],17);marker=L.marker([target.latitude,target.longitude]).addTo(map);
   const tiles=L.tileLayer(window.mapTileUrl||'https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(map);
   tiles.on('tileerror',()=>tell('지도 일부를 불러오지 못했습니다. 좌표와 위치 설명은 유지됩니다.'));
   tiles.on('tileload',()=>{loaded=true;clearTimeout(timer)});
   timer=setTimeout(()=>{if(!loaded)tell('지도 응답이 지연되고 있습니다. 좌표와 위치 설명으로 계속 진행할 수 있습니다.')},9000);
   if(box)map.on('click',event=>select(event.latlng.lat,event.latlng.lng));
  }else {map.invalidateSize();map.setView([target.latitude,target.longitude],map.getZoom());marker.setLatLng([target.latitude,target.longitude])}
 }
 function select(lat,lng){target={latitude:lat,longitude:lng};if(box){box.querySelector('[name=latitude]').value=lat;box.querySelector('[name=longitude]').value=lng}draw();tell('선택한 좌표: '+lat.toFixed(6)+', '+lng.toFixed(6)+' · 위치 설명을 입력해주세요.')}
 let autoRequested=false;
 async function autoLocate(){if(!box||autoRequested)return;const lat=box.querySelector('[name=latitude]'),lng=box.querySelector('[name=longitude]');if(lat.value&&lng.value)return;autoRequested=true;tell('현재 위치를 확인하고 있습니다…');try{const p=await gps();if(!lat.value&&!lng.value)select(p.latitude,p.longitude)}catch(e){tell(e.message)}}
 window.addEventListener('map-visible',()=>{requestAnimationFrame(draw);autoLocate()});
 if(box){const lat=box.querySelector('[name=latitude]'),lng=box.querySelector('[name=longitude]');if(lat.value&&lng.value)target={latitude:Number(lat.value),longitude:Number(lng.value)};
  box.querySelector('[data-gps]').addEventListener('click',async()=>{tell('현재 위치를 확인하고 있습니다…');try{const p=await gps();select(p.latitude,p.longitude)}catch(e){tell(e.message)}});
  [lat,lng].forEach(el=>el.addEventListener('change',()=>{if(lat.value&&lng.value&&Number.isFinite(Number(lat.value))&&Number.isFinite(Number(lng.value)))select(Number(lat.value),Number(lng.value))}));
 }draw();if(box&&box.querySelector('[name=storage_type]:checked')?.value==='external')autoLocate();
 const nav=document.querySelector('[data-navigation]');
 let routeLayer,lastRequest=0,lastRoutePosition=null,latestPosition=null,watchId=null,trackingTimer=null,trackingStopped=false,trackingGeneration=0;
 // GPS가 바뀌면 마커를 이동하고, 20m 이상 이동했을 때 최소 15초 간격으로 경로를 재조회한다.
 function updateUserMarker(p){if(!map)return;if(userMarker)userMarker.setLatLng([p.latitude,p.longitude]);else userMarker=L.circleMarker([p.latitude,p.longitude],{radius:8,color:'#fff',weight:2,fillColor:'#2563eb',fillOpacity:1}).addTo(map).bindTooltip('내 위치')}
 function movedMeters(a,b){const rad=Math.PI/180,dlat=(b.latitude-a.latitude)*rad,dlng=(b.longitude-a.longitude)*rad;const h=Math.sin(dlat/2)**2+Math.cos(a.latitude*rad)*Math.cos(b.latitude*rad)*Math.sin(dlng/2)**2;return 6371000*2*Math.asin(Math.sqrt(Math.min(1,h)))}
 function refreshRoute(){if(trackingStopped||document.hidden||nav.disabled||!latestPosition||Date.now()-lastRequest<15000)return;if(!lastRoutePosition||movedMeters(lastRoutePosition,latestPosition)>=20)navigate(latestPosition)}
 async function navigate(position=null){
  if(nav.disabled)return;
  nav.disabled=true;
  const generation=trackingGeneration;
  const section=document.getElementById('navigation-links');
  tell('현재 위치를 확인하고 있습니다…');
  try{
   const p=position||await gps();if(trackingStopped||generation!==trackingGeneration)return;draw();if(!map)throw new Error('지도를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.');
   latestPosition={latitude:p.latitude,longitude:p.longitude};
   updateUserMarker(latestPosition);
   marker.bindTooltip('물품 위치');map.fitBounds([[p.latitude,p.longitude],[target.latitude,target.longitude]],{padding:[35,35],maxZoom:17});
   tell('도보 이동 경로를 불러오고 있습니다…');
   if(Date.now()-lastRequest<1100)throw new Error('잠시 후 다시 눌러주세요.');
   lastRequest=Date.now();
   const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),25000);
   let result;
   try{
    const url=nav.dataset.routeUrl+'?'+new URLSearchParams({latitude:p.latitude,longitude:p.longitude});
    const response=await fetch(url,{signal:controller.signal,credentials:'same-origin'});
    result=await response.json();if(!response.ok)throw new Error(result.error||('길찾기 요청 실패: HTTP '+response.status));
   }finally{clearTimeout(timeout)}
   const route=result.routes?.[0];
   if(result.code!=='Ok'||!route||route.geometry?.type!=='LineString'||!Array.isArray(route.geometry.coordinates)||route.geometry.coordinates.length<2||!Number.isFinite(route.distance)||!Number.isFinite(route.duration))throw new Error('route');
   if(trackingStopped||generation!==trackingGeneration)return;
   routeLayer?.remove();section.replaceChildren();lastRoutePosition={latitude:p.latitude,longitude:p.longitude};
   routeLayer=L.geoJSON(route.geometry,{style:{color:'#2563eb',weight:5,opacity:0.85}}).addTo(map);
   const bounds=routeLayer.getBounds();bounds.extend([p.latitude,p.longitude]);bounds.extend([target.latitude,target.longitude]);map.fitBounds(bounds,{padding:[35,35],maxZoom:17});
   const summary=document.createElement('p');summary.className='text-sm font-semibold text-slate-700';summary.textContent='도보 '+(route.distance>=1000?(route.distance/1000).toFixed(1)+'km':Math.round(route.distance)+'m')+' · 약 '+Math.max(1,Math.ceil(route.duration/60))+'분';section.append(summary);
   const credit=document.createElement('p');credit.className='text-xs text-slate-500';credit.append('경로 제공: FOSSGIS / OSRM · ');const fix=document.createElement('a');fix.href='https://www.openstreetmap.org/fixthemap';fix.target='_blank';fix.rel='noopener noreferrer';fix.textContent='지도 오류 신고';credit.append(fix);section.append(credit);section.hidden=false;
   tell('파란 선이 도보 경로입니다. 이동하면 내 위치와 경로가 자동으로 갱신됩니다.');
  }catch(e){if(trackingStopped||generation!==trackingGeneration)return;tell(e.name==='AbortError'?'길찾기 요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.':e.message==='route'||e instanceof TypeError?'길찾기 응답을 확인하지 못했습니다. 서버 실행 상태와 인터넷 연결을 확인해주세요.':e.message)}
  finally{nav.disabled=false}
 }
 if(nav){
  nav.addEventListener('click',()=>navigate());navigate();
  function startTracking(){
   if(!navigator.geolocation||watchId!==null)return;
   trackingStopped=false;
   const generation=trackingGeneration;
   watchId=navigator.geolocation.watchPosition(p=>{
    if(trackingStopped||generation!==trackingGeneration)return;
    latestPosition={latitude:p.coords.latitude,longitude:p.coords.longitude};
    updateUserMarker(latestPosition);refreshRoute();
   },e=>{if(!trackingStopped&&generation===trackingGeneration)tell(e.code===1?'위치 권한이 거부되어 자동 추적할 수 없습니다. 위치 권한을 허용해주세요.':'현재 위치 추적이 지연되고 있습니다. 기존 경로는 유지됩니다.')},{enableHighAccuracy:true,timeout:10000,maximumAge:0});
   trackingTimer=setInterval(refreshRoute,15000);
  }
  window.addEventListener('pagehide',()=>{
   trackingStopped=true;trackingGeneration++;latestPosition=null;
   if(watchId!==null)navigator.geolocation.clearWatch(watchId);
   clearInterval(trackingTimer);watchId=null;trackingTimer=null;
  });
  window.addEventListener('pageshow',startTracking);
  startTracking();
 }
});
