/* Read-only cached view. Responsive charts reuse the same fetched history.
   Web (>=601px): a list of devices with one persistent graph on the right, the
   1000 watt inverter preselected. Mobile (<=600px): tap a card to open its graph
   underneath, one at a time. */
(() => {
  const el=id=>document.getElementById(id), mobile=matchMedia('(max-width:600px)');
  const DEVICE_IDS=['inverter-1000','garage-inverter','inverter-600','charger-inverter','fridge'];
  const INTERVAL_S=300; // eWeLink readings arrive every 5 minutes
  const SHORT=['15m','live','2h','4h','8h'];
  const PERIOD_WORD={'15m':'last 15 minutes',live:'past hour','2h':'past 2 h','4h':'past 4 h','8h':'past 8 h',today:'today','24h':'past 24 h',previous24h:'previous 24 h',yesterday:'yesterday','7d':'past week','30d':'past month'};
  let timer=null,controller=null,version=0,historyAt=0,historyKey='';
  const available=()=>!document.hidden&&!el('ewelinkPanel').hidden;
  const format=(v,unit)=>Number.isFinite(v)?v.toLocaleString(undefined,{maximumFractionDigits:2})+' '+unit:'Not reported';
  const date=(ts,options={})=>new Date(ts).toLocaleString('en-GB',{timeZone:'Europe/London',...options});
  const layout=()=>el('ewelinkChartSection').closest('.ewelink-layout')||document.querySelector('.ewelink-layout');
  window.stopEwelink=()=>{version++;clearTimeout(timer);timer=null;controller?.abort();controller=null;};
  // One plug's latest figures into a card: the Inverters tab's cards, and the System tab's detail header (window.ewelinkDrawCard).
  function drawCard(card,device){
    const stale=!device.ts||device.stale||!device.online;
    card.dataset.stale=String(stale);
    // Icon status: green+pulse when on, amber when off, grey when offline/unknown.
    card.dataset.devstate=!device.ts?'unknown':stale?'offline':device.switch==='on'?'on':device.switch==='off'?'off':'unknown';
    const state=!device.ts?'Not connected':device.stale?'Old reading':device.online?'Online':'Offline';
    card.querySelector('[data-state]').textContent=' · '+state;
    for(const [field,key,unit] of [['power','power_w','W'],['voltage','voltage_v','V'],['current','current_a','A']])card.querySelector('[data-'+field+']').textContent=format(device[key],unit);
    card.querySelector('[data-switch]').textContent=device.switch==='on'?'On':device.switch==='off'?'Off':'Not reported';
  }
  function draw(data){
    for(const device of data.devices){const card=el('ew-'+device.id);if(card)drawCard(card,device);}
  }
  window.ewelinkDrawCard=drawCard;
  // Energy for the selected period: the server's figure (the plug's own day counter where it sends one,
  // otherwise an estimate from power readings); the older five-minute sum only if the server gives none.
  function energyWh(result){
    if(result?.energy&&'wh' in result.energy)return Number.isFinite(result.energy.wh)?result.energy.wh:null;
    let sum=0,any=false;
    for(const b of (result.bins||[]))if(b.samples&&Number.isFinite(b.mean_w)){sum+=b.mean_w*b.samples;any=true;}
    return any?sum*INTERVAL_S/3600:null;
  }
  const fmtEnergy=wh=>wh==null?'No readings':wh<1000?Math.round(wh).toLocaleString()+' Wh':(wh/1000).toLocaleString(undefined,{maximumFractionDigits:2})+' kWh';
  let cumulativeKey='',cumulativeAt=0;
  async function refreshCumulative(v){
    const period=el('ewelinkPeriod').value,word=PERIOD_WORD[period]||'this period';
    document.querySelectorAll('.ewelink-cumulative-label').forEach(x=>x.textContent='Used '+word);
    await Promise.all(DEVICE_IDS.map(async id=>{
      try{
        const r=await get('/api/ewelink/history?device='+encodeURIComponent(id)+'&window='+period);
        if(v!==version||!available())return;
        showEnergy(el('ew-'+id),r,word);
      }catch(error){/* keep the previous figure if a read fails */}
    }));
  }
  // Energy used for the period, with the estimate wording, into a card's [data-energy] and its label.
  function showEnergy(card,r,word){
    const cell=card?.querySelector('[data-energy]'),label=card?.querySelector('.ewelink-cumulative-label');
    if(cell)cell.textContent=fmtEnergy(energyWh(r));
    const method=r?.energy?.method;
    if(label)label.textContent='Used '+word+(method==='estimate'?' (estimate)':method==='mixed'?' (part estimate)':'');
    if(cell)cell.title=method==='counter'?'From the plug\'s own energy counter':method?'Estimated from power readings, which the cloud updates only now and then, so it can read low':'';
  }
  // The System tab's detail uses the same wording: r is the plug's /api/ewelink/history reply for period.
  window.ewelinkEnergy=(card,r,period)=>showEnergy(card,r,PERIOD_WORD[period]||'this period');
  function binLabel(b,unit,full=false){
    if(unit==='hour')return date(b.start,full?{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}:{hour:'2-digit',minute:'2-digit'});
    return (unit==='week'?'Week of ':'')+date(b.start,{day:'numeric',month:'short'});
  }
  // One power-history graph. e names its elements (chart, details, drill, back, bins, heading, note); available() says whether it is showing.
  // The Inverters tab has one; the System tab's detail makes another through window.ewelinkGraph.
  function graph(e,available){
    const $e=k=>el(e[k]),g={history:null,week:null,shownBins:[],selectedBin:0,chart:null};
    g.showBin=index=>{
      if(!g.shownBins.length)return;
      g.selectedBin=Math.max(0,Math.min(g.shownBins.length-1,Number(index)||0));
      const b=g.shownBins[g.selectedBin],unit=g.week?'day':g.history.bucket_unit;
      $e('details').textContent=binLabel(b,unit,true)+' · '+(b.samples?format(b.mean_w,'W')+' average · '+b.samples+' readings'+(b.partial?' · Partial data':''):'No recorded readings');
      $e('drill').hidden=g.history.bucket_unit!=='week'||!!g.week;
      g.highlight();
    };
    // The tapped bar turns white and the rest stay full colour, so the selection shows on the chart.
    g.highlight=()=>{
      if(!g.chart||g.chart.config.type!=='bar')return;
      g.chart.data.datasets[0].backgroundColor=g.shownBins.map((_,i)=>i===g.selectedBin?'#f8fafc':'#38bdf8');
      g.chart.update('none');
    };
    const coverage={id:'ewelinkCoverage',afterDatasetsDraw(c){
      if(c.config.type!=='bar')return;
      const {ctx,chartArea:a}=c,scale=c.scales.x;
      const width=Math.min(40,(a.right-a.left)/Math.max(1,g.shownBins.length)*.7);
      g.shownBins.forEach((b,i)=>{
        if(b.samples)return;
        const x=scale.getPixelForValue(i)-width/2;
        ctx.save();ctx.fillStyle='rgba(148,163,184,.05)';ctx.fillRect(x,a.top,width,a.bottom-a.top);
        ctx.beginPath();ctx.rect(x,a.top,width,a.bottom-a.top);ctx.clip();ctx.strokeStyle='rgba(148,163,184,.25)';ctx.lineWidth=1;
        for(let y=a.top-width;y<a.bottom+width;y+=12){ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x+width,y+width);ctx.stroke();}ctx.restore();
      });
    }};
    g.draw=()=>{
      const history=g.history;
      if(!history||!available())return;
      const bars=mobile.matches&&!SHORT.includes(history.window);
      $e('bins').hidden=!bars;$e('back').hidden=!bars||!g.week;
      let data,labels=[],unit=g.week?'day':history.bucket_unit;
      if(bars){
        g.shownBins=(g.week?history.daily_bins:history.bins).filter(b=>!g.week||b.start>=g.week.start&&b.end<=g.week.end);
        labels=g.shownBins.map(b=>binLabel(b,unit));data=g.shownBins.map(b=>b.mean_w);
        g.showBin(g.selectedBin);
      }else{
        data=[];let previous=null;
        for(const [ts,value] of history.points){
          if(previous!==null&&ts-previous>Math.max(900000,history.bucket_ms*1.5))data.push({x:previous+1,y:null});
          data.push({x:ts,y:value});previous=ts;
        }
      }
      $e('heading').textContent=bars?'Average power by '+unit:'Power over time';
      if(g.chart)g.chart.destroy();
      const chart=g.chart=new Chart($e('chart'),{type:bars?'bar':'line',plugins:[coverage],
        data:{labels,datasets:[{label:bars?'Average power':'Power',data,parsing:bars?undefined:false,borderColor:'#38bdf8',backgroundColor:'#38bdf8',borderWidth:bars?0:2,borderRadius:bars?3:0,pointRadius:history.points.length===1?3:0,pointHitRadius:12,spanGaps:false,minBarLength:2,barPercentage:.8,categoryPercentage:.85}]},
        options:{responsive:true,maintainAspectRatio:false,animation:false,
          onClick:event=>{if(bars)g.showBin(Math.round(chart.scales.x.getValueForPixel(event.x)));},
          // Touch screens report taps as touch events, not clicks, so select the bar under the finger here too.
          onHover:event=>{if(bars&&event.native?.type?.startsWith('touch'))g.showBin(Math.round(chart.scales.x.getValueForPixel(event.x)));},
          scales:{x:{type:bars?'category':'linear',grid:{display:!bars,color:'#222f49'},ticks:{color:'#94a3b8',maxTicksLimit:mobile.matches?5:8,maxRotation:0,callback:bars?undefined:v=>date(v,SHORT.includes(history.window)||['today','24h','previous24h','yesterday'].includes(history.window)?{hour:'2-digit',minute:'2-digit'}:{day:'numeric',month:'short'})}},y:{beginAtZero:bars,grid:{color:'#222f49'},ticks:{color:'#94a3b8',callback:v=>v+' W'}}},
          plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>bars?binLabel(g.shownBins[items[0].dataIndex],unit,true):date(items[0].parsed.x),label:item=>format(item.parsed.y,'W')+(bars?' average':'')}}}}});
      g.highlight();
      const count=history.points.length;
      $e('note').textContent=bars?'Bars show average power in W. Hatched slots have no readings. Tap a bar for its details.':'Stored power readings · gaps indicate missing readings.';
      if(!count)$e('note').textContent='No recorded power readings in this period.';
    };
    // A new reply: fresh means a different device or period, so start again on the latest bar.
    g.set=(result,fresh)=>{if(fresh){g.week=null;g.selectedBin=Math.max(0,(result.bins?.length||1)-1);}g.history=result;g.draw();};
    g.clear=(note)=>{g.history=null;g.week=null;if(g.chart){g.chart.destroy();g.chart=null;}$e('bins').hidden=true;$e('back').hidden=true;$e('note').textContent=note;};
    g.fail=()=>g.clear('History is not available yet.');
    $e('drill').addEventListener('click',()=>{g.week=g.shownBins[g.selectedBin];g.selectedBin=0;g.draw();});
    $e('back').addEventListener('click',()=>{g.week=null;g.selectedBin=0;g.draw();});
    return g;
  }
  window.ewelinkGraph=graph;
  const main=graph({chart:'ewelinkChart',details:'ewelinkDetails',drill:'ewelinkDrill',back:'ewelinkBack',bins:'ewelinkBinControls',heading:'ewelinkChartHeading',note:'ewelinkHistoryNote'},available);
  async function get(url,signal){const r=await fetch(url,{signal,cache:'no-store'});if(!r.ok)throw Error('Unavailable');return r.json();}
  async function read(v){
    if(v!==version||!available())return;
    controller=new AbortController();const aborter=controller,timeout=setTimeout(()=>aborter.abort(),10000);
    try{
      const data=await get('/api/ewelink/latest',aborter.signal);
      if(v!==version||!available())return;draw(data);
      // Cumulative shows on every card, graph open or not. Five history reads, so at most every 30 seconds (or when the period changes); the latest readings above refresh every 3 seconds.
      const cumKey=el('ewelinkPeriod').value;if(cumKey!==cumulativeKey||Date.now()-cumulativeAt>=30000){cumulativeKey=cumKey;cumulativeAt=Date.now();refreshCumulative(v);}
      if(el('ewelinkChartSection').hidden)return; // History only while a device graph is shown.
      const device=el('ewelinkDevice').value,period=el('ewelinkPeriod').value,key=device+period;
      if(historyKey!==key||Date.now()-historyAt>300000){
        try{
          const result=await get('/api/ewelink/history?device='+encodeURIComponent(device)+'&window='+period,aborter.signal);
          if(v!==version||!available())return;
          main.set(result,historyKey!==key);historyKey=key;historyAt=Date.now();
        }catch(error){if(v!==version||!available())return;main.fail();}
      }else main.draw();
    }catch(error){if(v===version&&available())document.querySelectorAll('.ewelink-card').forEach(c=>c.dataset.stale='true');}
    finally{clearTimeout(timeout);if(v===version&&available()){controller=null;timer=setTimeout(()=>read(v),3000);}}
  }
  window.startEwelink=()=>{window.stopEwelink();read(version);};
  // Web: pick a device; its graph fills the right-hand pane. One device highlighted at a time.
  function webSelect(card){
    document.querySelectorAll('.ewelink-card').forEach(c=>{c.setAttribute('aria-selected',String(c===card));c.setAttribute('aria-expanded','false');});
    const section=el('ewelinkChartSection');
    if(section.parentElement!==layout())layout().appendChild(section);
    section.hidden=false;
    el('ewelinkDevice').value=card.id.slice(3);historyAt=0;main.week=null;window.startEwelink();
  }
  // Mobile: tap a card to open its graph underneath; tap again (or another card) to close it.
  function toggle(card){
    const section=el('ewelinkChartSection'),wasOpen=card.getAttribute('aria-expanded')==='true';
    document.querySelectorAll('.ewelink-card').forEach(c=>{c.setAttribute('aria-expanded','false');c.setAttribute('aria-selected','false');});
    if(main.chart){main.chart.destroy();main.chart=null;}
    if(wasOpen){section.hidden=true;return;}
    card.setAttribute('aria-expanded','true');card.after(section);section.hidden=false;
    el('ewelinkDevice').value=card.id.slice(3);historyAt=0;main.week=null;window.startEwelink();
  }
  // Put the page into web (list + persistent graph) or mobile (collapsed) shape.
  function initMode(){
    const section=el('ewelinkChartSection');
    document.querySelectorAll('.ewelink-card').forEach(c=>c.setAttribute('aria-expanded','false'));
    if(section.parentElement!==layout())layout().appendChild(section);
    if(mobile.matches){
      document.querySelectorAll('.ewelink-card').forEach(c=>c.setAttribute('aria-selected','false'));
      section.hidden=true;if(main.chart){main.chart.destroy();main.chart=null;}
    }else{
      let sel=document.querySelector('.ewelink-card[aria-selected="true"]')||el('ew-inverter-1000')||document.querySelector('.ewelink-card');
      document.querySelectorAll('.ewelink-card').forEach(c=>c.setAttribute('aria-selected',String(c===sel)));
      if(sel)el('ewelinkDevice').value=sel.id.slice(3);
      section.hidden=false;
    }
  }
  document.querySelectorAll('.ewelink-card').forEach(card=>{
    card.addEventListener('click',()=>mobile.matches?toggle(card):webSelect(card));
    card.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();mobile.matches?toggle(card):webSelect(card);}});
  });
  for(const id of ['ewelinkDevice','ewelinkPeriod'])el(id).addEventListener('change',()=>{historyAt=0;main.week=null;window.startEwelink();});
  mobile.addEventListener('change',()=>{initMode();if(available())window.startEwelink();});
  document.addEventListener('visibilitychange',()=>{window.stopEwelink();if(available())window.startEwelink();});
  window.addEventListener('pagehide',window.stopEwelink);
  window.addEventListener('pageshow',event=>{if(event.persisted&&available())window.startEwelink();});
  initMode();
  // The page may already have opened #ewelink before this deferred script loaded.
  if(location.hash==='#ewelink'){if(activeTab==='ewelink')window.startEwelink();else selectTab('ewelink');}
})();
