/* Cached eWeLink view. Browser traffic never initiates vendor calls. */
(() => {
  const el=id=>document.getElementById(id);
  let timer=null,controller=null,version=0,chart=null,historyAt=0,historyKey='';
  const available=()=>!document.hidden&&!el('ewelinkPanel').hidden;
  const format=(v,unit)=>Number.isFinite(v)?v.toLocaleString(undefined,{maximumFractionDigits:2})+' '+unit:'Not reported';
  window.stopEwelink=()=>{version++;clearTimeout(timer);timer=null;controller?.abort();controller=null;};
  function draw(data){
    el('ewelinkStatus').textContent=data.status;
    for(const device of data.devices){
      const card=el('ew-'+device.id);if(!card)continue;
      const state=!device.ts?'Not connected':device.stale?'Reading is old':device.online?'Online':'Offline';
      card.dataset.stale=String(!device.ts||device.stale||!device.online);
      card.querySelector('[data-state]').textContent=state;
      card.querySelector('[data-power]').textContent=format(device.power_w,'W');
      card.querySelector('[data-voltage]').textContent=format(device.voltage_v,'V');
      card.querySelector('[data-current]').textContent=format(device.current_a,'A');
      card.querySelector('[data-switch]').textContent=device.switch==='on'?'On':device.switch==='off'?'Off':'Not reported';
      card.querySelector('time').textContent=device.ts?'Checked '+new Date(device.ts).toLocaleString(undefined,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'Waiting for account connection';
      if(device.ts)card.querySelector('time').dateTime=new Date(device.ts).toISOString();
    }
  }
  function drawHistory(data){
    const points=[];let previous=null;
    for(const [ts,value] of data.points){
      if(previous!==null&&ts-previous>Math.max(900000,data.bucket_ms*1.5))points.push({x:previous+1,y:null});
      points.push({x:ts,y:value});previous=ts;
    }
    if(!chart){
      chart=new Chart(el('ewelinkChart'),{type:'line',data:{datasets:[{label:'Power',data:[],parsing:false,borderColor:'#38bdf8',borderWidth:2,pointRadius:0,pointHitRadius:12,spanGaps:false}]},
        options:{responsive:true,maintainAspectRatio:false,animation:false,
          scales:{x:{type:'linear',grid:{color:'#222f49'},ticks:{color:'#94a3b8',maxTicksLimit:5,callback:v=>new Date(v).toLocaleString(undefined,data.window==='24h'?{hour:'2-digit',minute:'2-digit'}:{day:'numeric',month:'short'})}},y:{grid:{color:'#222f49'},ticks:{color:'#94a3b8',callback:v=>v+' W'}}},
          plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>new Date(items[0].parsed.x).toLocaleString(),label:item=>format(item.parsed.y,'W')}}}}});
    }
    chart.data.datasets[0].data=points;chart.resize();chart.update('none');
    el('ewelinkHistoryNote').textContent=points.length?'Stored power readings · gaps indicate missing readings.':'History will appear after this device reports power.';
  }
  async function get(url,signal){const response=await fetch(url,{signal,cache:'no-store'});if(!response.ok)throw Error('Unavailable');return response.json();}
  async function read(v){
    if(v!==version||!available())return;
    controller=new AbortController();const aborter=controller;
    const timeout=setTimeout(()=>aborter.abort(),10000);
    try{
      const data=await get('/api/ewelink/latest',aborter.signal);
      if(v!==version||!available())return;draw(data);
      const device=el('ewelinkDevice').value,period=el('ewelinkPeriod').value,key=device+period;
      if(historyKey!==key||Date.now()-historyAt>300000){
        try{
          const history=await get('/api/ewelink/history?device='+encodeURIComponent(device)+'&window='+period,aborter.signal);
          if(v!==version||!available())return;
          historyKey=key;historyAt=Date.now();drawHistory(history);
        }catch(error){
          if(v!==version||!available())return;
          if(chart){chart.data.datasets[0].data=[];chart.update('none');}
          el('ewelinkHistoryNote').textContent='History is not available yet.';
        }
      }
    }catch(error){
      if(v===version&&available()){
        el('ewelinkStatus').textContent='Readings unavailable · reconnecting';
        document.querySelectorAll('.ewelink-card').forEach(card=>card.dataset.stale='true');
      }
    }finally{
      clearTimeout(timeout);
      if(v===version&&available()){controller=null;timer=setTimeout(()=>read(v),30000);}
    }
  }
  window.startEwelink=()=>{window.stopEwelink();read(version);};
  for(const id of ['ewelinkDevice','ewelinkPeriod'])el(id).addEventListener('change',()=>{historyAt=0;window.startEwelink();});
  document.addEventListener('visibilitychange',()=>{window.stopEwelink();if(available())window.startEwelink();});
  window.addEventListener('pagehide',window.stopEwelink);
  window.addEventListener('pageshow',event=>{if(event.persisted&&available())window.startEwelink();});
  if(location.hash==='#ewelink')selectTab('ewelink');
})();
