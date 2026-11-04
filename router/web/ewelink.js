/* Read-only cached view. Responsive charts reuse the same fetched history. */
(() => {
  const el=id=>document.getElementById(id), mobile=matchMedia('(max-width:600px)');
  let timer=null,controller=null,version=0,chart=null,historyAt=0,historyKey='',history=null,week=null,shownBins=[],selectedBin=0;
  const available=()=>!document.hidden&&!el('ewelinkPanel').hidden;
  const format=(v,unit)=>Number.isFinite(v)?v.toLocaleString(undefined,{maximumFractionDigits:2})+' '+unit:'Not reported';
  const date=(ts,options={})=>new Date(ts).toLocaleString('en-GB',{timeZone:'Europe/London',...options});
  window.stopEwelink=()=>{version++;clearTimeout(timer);timer=null;controller?.abort();controller=null;};
  function draw(data){
    const newest=Math.max(0,...data.devices.map(d=>d.ts||0));
    el('ewelinkStatus').textContent=data.status+(newest?' · Checked '+date(newest,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'');
    for(const device of data.devices){
      const card=el('ew-'+device.id);if(!card)continue;
      card.dataset.stale=String(!device.ts||device.stale||!device.online);
      const state=!device.ts?'Not connected':device.stale?'Old reading':device.online?'Online':'Offline';
      card.querySelector('[data-state]').textContent=' · '+state;card.querySelector('.ewelink-latest .status-dot').title=state;
      for(const [field,key,unit] of [['power','power_w','W'],['voltage','voltage_v','V'],['current','current_a','A']])card.querySelector('[data-'+field+']').textContent=format(device[key],unit);
      card.querySelector('[data-switch]').textContent=device.switch==='on'?'On':device.switch==='off'?'Off':'Not reported';
    }
  }
  function binLabel(b,unit,full=false){
    if(unit==='hour')return date(b.start,full?{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}:{hour:'2-digit',minute:'2-digit'});
    return (unit==='week'?'Week of ':'')+date(b.start,{day:'numeric',month:'short'});
  }
  function showBin(index){
    if(!shownBins.length)return;
    selectedBin=Math.max(0,Math.min(shownBins.length-1,Number(index)||0));
    const b=shownBins[selectedBin],unit=week?'day':history.bucket_unit;
    el('ewelinkBucket').value=String(selectedBin);
    el('ewelinkDetails').textContent=binLabel(b,unit,true)+' · '+(b.samples?format(b.mean_w,'W')+' average · '+b.samples+' readings'+(b.partial?' · Partial data':''):'No recorded readings');
    el('ewelinkDrill').hidden=history.bucket_unit!=='week'||!!week;
    highlightBin();
  }
  // The chosen bar stays bright and the rest dim, so picking from the list or tapping a bar shows on the chart.
  function highlightBin(){
    if(!chart||chart.config.type!=='bar')return;
    chart.data.datasets[0].backgroundColor=shownBins.map((_,i)=>i===selectedBin?'#38bdf8':'rgba(56,189,248,.35)');
    chart.update('none');
  }
  const coverage={id:'ewelinkCoverage',afterDatasetsDraw(c){
    if(c.config.type!=='bar')return;
    const {ctx,chartArea:a}=c,scale=c.scales.x;
    const width=Math.min(40,(a.right-a.left)/Math.max(1,shownBins.length)*.7);
    shownBins.forEach((b,i)=>{
      if(b.samples)return;
      const x=scale.getPixelForValue(i)-width/2;
      ctx.save();ctx.fillStyle='rgba(148,163,184,.05)';ctx.fillRect(x,a.top,width,a.bottom-a.top);
      ctx.beginPath();ctx.rect(x,a.top,width,a.bottom-a.top);ctx.clip();ctx.strokeStyle='rgba(148,163,184,.25)';ctx.lineWidth=1;
      for(let y=a.top-width;y<a.bottom+width;y+=12){ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x+width,y+width);ctx.stroke();}ctx.restore();
    });
  }};
  function drawHistory(){
    if(!history||!available())return;
    const bars=mobile.matches&&history.window!=='live';
    el('ewelinkBinControls').hidden=!bars;el('ewelinkBack').hidden=!bars||!week;
    let data,labels=[],unit=week?'day':history.bucket_unit;
    if(bars){
      shownBins=(week?history.daily_bins:history.bins).filter(b=>!week||b.start>=week.start&&b.end<=week.end);
      labels=shownBins.map(b=>binLabel(b,unit));data=shownBins.map(b=>b.mean_w);
      el('ewelinkBucket').replaceChildren(...shownBins.map((b,i)=>{const o=document.createElement('option');o.value=String(i);o.textContent=binLabel(b,unit,true);return o;}));
      showBin(selectedBin);
    }else{
      data=[];let previous=null;
      for(const [ts,value] of history.points){
        if(previous!==null&&ts-previous>Math.max(900000,history.bucket_ms*1.5))data.push({x:previous+1,y:null});
        data.push({x:ts,y:value});previous=ts;
      }
    }
    el('ewelinkChartHeading').textContent=bars?'Average power by '+unit:'Power over time';
    if(chart)chart.destroy();
    chart=new Chart(el('ewelinkChart'),{type:bars?'bar':'line',plugins:[coverage],
      data:{labels,datasets:[{label:bars?'Average power':'Power',data,parsing:bars?undefined:false,borderColor:'#38bdf8',backgroundColor:'#38bdf8',borderWidth:bars?0:2,borderRadius:bars?3:0,pointRadius:history.points.length===1?3:0,pointHitRadius:12,spanGaps:false,minBarLength:2,barPercentage:.8,categoryPercentage:.85}]},
      options:{responsive:true,maintainAspectRatio:false,animation:false,
        onClick:event=>{if(bars)showBin(Math.round(chart.scales.x.getValueForPixel(event.x)));},
        scales:{x:{type:bars?'category':'linear',grid:{display:!bars,color:'#222f49'},ticks:{color:'#94a3b8',maxTicksLimit:mobile.matches?5:8,maxRotation:0,callback:bars?undefined:v=>date(v,history.window==='live'||['today','24h','previous24h','yesterday'].includes(history.window)?{hour:'2-digit',minute:'2-digit'}:{day:'numeric',month:'short'})}},y:{beginAtZero:bars,grid:{color:'#222f49'},ticks:{color:'#94a3b8',callback:v=>v+' W'}}},
        plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>bars?binLabel(shownBins[items[0].dataIndex],unit,true):date(items[0].parsed.x),label:item=>format(item.parsed.y,'W')+(bars?' average':'')}}}}});
    highlightBin();
    const count=history.points.length;
    el('ewelinkHistoryNote').textContent=bars?'Bars show average power in W. Hatched slots have no readings; select a bar for detail.':'Stored power readings · gaps indicate missing readings.';
    if(!count)el('ewelinkHistoryNote').textContent='No recorded power readings in this period.';
  }
  async function get(url,signal){const r=await fetch(url,{signal,cache:'no-store'});if(!r.ok)throw Error('Unavailable');return r.json();}
  async function read(v){
    if(v!==version||!available())return;
    controller=new AbortController();const aborter=controller,timeout=setTimeout(()=>aborter.abort(),10000);
    try{
      const data=await get('/api/ewelink/latest',aborter.signal);
      if(v!==version||!available())return;draw(data);
      if(el('ewelinkChartSection').hidden)return; // History only while a device graph is open.
      const device=el('ewelinkDevice').value,period=el('ewelinkPeriod').value,key=device+period;
      if(historyKey!==key||Date.now()-historyAt>300000){
        try{
          const result=await get('/api/ewelink/history?device='+encodeURIComponent(device)+'&window='+period,aborter.signal);
          if(v!==version||!available())return;
          if(historyKey!==key){week=null;selectedBin=Math.max(0,(result.bins?.length||1)-1);}
          history=result;historyKey=key;historyAt=Date.now();drawHistory();
        }catch(error){if(v!==version||!available())return;history=null;if(chart){chart.destroy();chart=null;}el('ewelinkBinControls').hidden=true;el('ewelinkBack').hidden=true;el('ewelinkHistoryNote').textContent='History is not available yet.';}
      }else drawHistory();
    }catch(error){if(v===version&&available()){el('ewelinkStatus').textContent='Readings unavailable · reconnecting';document.querySelectorAll('.ewelink-card').forEach(c=>c.dataset.stale='true');}}
    finally{clearTimeout(timeout);if(v===version&&available()){controller=null;timer=setTimeout(()=>read(v),30000);}}
  }
  window.startEwelink=()=>{window.stopEwelink();read(version);};
  // Tap a card to open its graph underneath; tap again (or another card) to close it.
  function toggle(card){
    const section=el('ewelinkChartSection'),wasOpen=card.getAttribute('aria-expanded')==='true';
    document.querySelectorAll('.ewelink-card').forEach(c=>c.setAttribute('aria-expanded','false'));
    if(chart){chart.destroy();chart=null;}
    if(wasOpen){section.hidden=true;return;}
    card.setAttribute('aria-expanded','true');card.after(section);section.hidden=false;
    el('ewelinkDevice').value=card.id.slice(3);historyAt=0;week=null;window.startEwelink();
  }
  document.querySelectorAll('.ewelink-card').forEach(card=>{
    card.addEventListener('click',()=>toggle(card));
    card.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();toggle(card);}});
  });
  for(const id of ['ewelinkDevice','ewelinkPeriod'])el(id).addEventListener('change',()=>{historyAt=0;week=null;window.startEwelink();});
  el('ewelinkBucket').addEventListener('change',event=>showBin(event.target.value));
  el('ewelinkDrill').addEventListener('click',()=>{week=shownBins[selectedBin];selectedBin=0;drawHistory();});
  el('ewelinkBack').addEventListener('click',()=>{week=null;selectedBin=0;drawHistory();});
  mobile.addEventListener('change',()=>{if(available())drawHistory();});
  document.addEventListener('visibilitychange',()=>{window.stopEwelink();if(available())window.startEwelink();});
  window.addEventListener('pagehide',window.stopEwelink);
  window.addEventListener('pageshow',event=>{if(event.persisted&&available())window.startEwelink();});
  // The page may already have opened #ewelink before this deferred script loaded.
  if(location.hash==='#ewelink'){if(activeTab==='ewelink')window.startEwelink();else selectTab('ewelink');}
})();
