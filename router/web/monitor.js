const $ = id => document.getElementById(id);
let activeTab = 'power';
const views = ['power','voltage','juwai'];
let voltageChart = null, voltagePoints = [], voltageTimer = null, voltageController = null, voltageVersion = 0;
function stopVoltage() {
  voltageVersion++;
  clearTimeout(voltageTimer);voltageTimer=null;
  voltageController?.abort();voltageController=null;
}
function makeVoltageChart() {
  if(voltageChart)return;
  voltageChart = new Chart($('voltageChart'),{
    type:'line',data:{datasets:[{label:'Voltage',data:[],parsing:false,borderColor:'#38bdf8',borderWidth:2,pointRadius:2,pointHitRadius:12,spanGaps:false,tension:0}]},
    options:{responsive:true,maintainAspectRatio:false,animation:false,normalized:true,
      scales:{x:{type:'linear',grid:{color:'#1e293b'},ticks:{color:'#94a3b8',maxTicksLimit:5,callback:v=>new Date(v).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}},
        y:{grid:{color:'#1e293b'},ticks:{color:'#94a3b8',callback:v=>v.toLocaleString()+' V'},suggestedMin:230,suggestedMax:240}},
      plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>new Date(items[0].parsed.x).toLocaleTimeString(),label:ctx=>ctx.parsed.y.toFixed(2)+' V'}}}}
  });
}
async function readVoltage(version) {
  if(version!==voltageVersion || activeTab!=='voltage' || document.hidden)return;
  voltageController=new AbortController();
  try {
    const data=await getJSON('/api/voltage',voltageController.signal);
    if(version!==voltageVersion || activeTab!=='voltage' || document.hidden)return;
    if(!Number.isFinite(data.ts)||!Number.isFinite(data.volts))throw new Error('Invalid voltage');
    const previous=voltagePoints[voltagePoints.length-1];
    if(!previous || data.ts>previous.x){
      if(previous && data.ts-previous.x>12000)voltagePoints.push({x:previous.x+3000,y:null});
      voltagePoints.push({x:data.ts,y:data.volts});
    }
    const end=Date.now();
    voltagePoints=voltagePoints.filter(p=>p.x>=end-900000).slice(-600);
    voltageChart.data.datasets[0].data=voltagePoints;
    voltageChart.options.scales.x.min=end-900000;voltageChart.options.scales.x.max=end;
    voltageChart.update('none');
    $('voltageValue').innerText=data.volts.toFixed(2)+' V';$('voltageValue').style.opacity='1';
    $('voltageStatus').innerText='Updated '+new Date(data.ts).toLocaleTimeString();
  } catch(e) {
    if(version===voltageVersion && e.name!=='AbortError'){
      $('voltageStatus').innerText='Voltage unavailable · retrying while this tab is open';
      $('voltageValue').style.opacity='.55';
    }
  } finally {
    if(version===voltageVersion && activeTab==='voltage' && !document.hidden){
      voltageController=null;voltageTimer=setTimeout(()=>readVoltage(version),3000);
    }
  }
}
function selectTab(name) {
  if(activeTab===name)return;
  activeTab=name;stopVoltage();stopJuwai();
  if(name!=='power'){generation++;controller?.abort();}
  for(const view of views){
    const current=view===name;
    $(view+'Tab').setAttribute('aria-selected',String(current));$(view+'Tab').tabIndex=current?0:-1;
    $(view+'Panel').hidden=!current;
  }
  if(name==='voltage'){
    generation++;controller?.abort();
    makeVoltageChart();voltageChart.resize();
    $('voltageStatus').innerText='Requesting voltage…';readVoltage(voltageVersion);
  } else if(name==='juwai') {
    makeJuwaiChart();juwaiChart.resize();readJuwai(juwaiVersion);
  } else {
    chart.resize();donut.resize();lastHistory=0;
    loadHistory().catch(e=>{if(e.name!=='AbortError')$('lastReading').innerText='Connection lost';});
  }
}
for(const view of views){
  $(view+'Tab').addEventListener('click',()=>selectTab(view));
  $(view+'Tab').addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();
    const i=views.indexOf(view);
    const next=event.key==='Home'?views[0]:event.key==='End'?views[views.length-1]:views[(i+(event.key==='ArrowRight'?1:views.length-1))%views.length];
    selectTab(next);$(next+'Tab').focus();
  });
}

let juwaiChart=null, juwaiTimer=null, juwaiController=null, juwaiVersion=0;
let juwaiHistory={}, juwaiHistoryKey='', juwaiHistoryTime=0;
const juwaiFields={power_w:{label:'Power',unit:'W',column:4},voltage_v:{label:'Voltage',unit:'V',column:2},current_a:{label:'Current',unit:'A',column:3}};
function stopJuwai(){juwaiVersion++;clearTimeout(juwaiTimer);juwaiTimer=null;juwaiController?.abort();juwaiController=null;}
function makeJuwaiChart(){
  if(juwaiChart)return;
  juwaiChart=new Chart($('juwaiChart'),{type:'line',data:{datasets:['Shed','Garage'].map((name,i)=>({label:name,data:[],parsing:false,borderColor:['#38bdf8','#34d399'][i],backgroundColor:['#38bdf8','#34d399'][i],pointRadius:3,pointHitRadius:10,borderWidth:2,spanGaps:false,tension:0}))},options:{responsive:true,maintainAspectRatio:false,animation:false,normalized:true,scales:{x:{type:'linear',ticks:{color:'#94a3b8',maxTicksLimit:5,callback:v=>new Date(v).toLocaleString(undefined,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})},grid:{color:'#1e293b'}},y:{ticks:{color:'#94a3b8'},grid:{color:'#1e293b'}}},plugins:{legend:{labels:{color:'#94a3b8'}},tooltip:{callbacks:{title:items=>new Date(items[0].parsed.x).toLocaleString(),label:c=>c.dataset.label+': '+c.parsed.y.toLocaleString()+' '+juwaiFields[$('juwaiMetric').value].unit}}}}});
}
function drawJuwai(){
  if(!juwaiChart)return;
  const field=juwaiFields[$('juwaiMetric').value];let count=0;
  for(const [i,id] of ['shed','garage'].entries()){
    const series=juwaiHistory[id],points=[];
    for(const row of series?.points||[]){
      const prev=points[points.length-1];
      if(prev && row[0]-prev.x>Math.max(1800000,series.step_ms*2.1))points.push({x:prev.x+series.step_ms,y:null});
      points.push({x:row[0],y:Number.isFinite(row[field.column])?row[field.column]:null});count++;
    }
    juwaiChart.data.datasets[i].data=points;
    if(series){juwaiChart.options.scales.x.min=series.start;juwaiChart.options.scales.x.max=series.end;}
  }
  juwaiChart.options.scales.y.title={display:true,text:field.label+' ('+field.unit+')',color:'#94a3b8'};
  juwaiChart.update('none');
  $('juwaiHistoryNote').textContent=count?'Stored sample averages. Missing intervals are left as gaps. History starts with the first stored sample.':'No stored samples in this period.';
}
function renderJuwai(data){
  for(const device of data.devices){
    if(!['shed','garage'].includes(device.id))continue;
    const id=device.id,number=(v,unit,digits)=>Number.isFinite(v)?v.toLocaleString(undefined,{maximumFractionDigits:digits})+' '+unit:'— '+unit;
    $(id+'Power').textContent=number(device.values.power_w,'W',2);
    $(id+'Voltage').textContent=number(device.values.voltage_v,'V',2);
    $(id+'Current').textContent=number(device.values.current_a,'A',3);
    $(id+'Card').dataset.stale=String(device.stale);
    $(id+'Age').textContent=device.observed_at?(device.stale?'Stale sample · ':'Stored ')+new Date(device.observed_at).toLocaleString():'No stored reading';
  }
  $('juwaiStatus').textContent=data.collection_mode==='automatic'?'Showing stored readings. Automatic collection is configured; check each sample’s update time.':'Verified samples · automatic collection is not enabled.';
}
async function readJuwai(version){
  if(version!==juwaiVersion||activeTab!=='juwai'||document.hidden)return;
  // One in-flight cycle; changing range cancels and invalidates the old cycle.
  const aborter=new AbortController();juwaiController=aborter;
  const window=$('juwaiWindow').value;
  try{
    const tasks=[getJSON('/api/tuya/latest',aborter.signal).then(data=>{if(version===juwaiVersion&&activeTab==='juwai'&&!document.hidden)renderJuwai(data);})];
    if(juwaiHistoryKey!==window||Date.now()-juwaiHistoryTime>=300000){
      tasks.push(Promise.all(['shed','garage'].map(id=>getJSON('/api/tuya/history?device='+id+'&window='+window,aborter.signal).then(data=>[id,data]))).then(rows=>{
        if(version!==juwaiVersion||activeTab!=='juwai'||document.hidden)return;
        juwaiHistory=Object.fromEntries(rows);juwaiHistoryKey=window;juwaiHistoryTime=Date.now();drawJuwai();
      }));
    }
    const results=await Promise.allSettled(tasks);
    if(version!==juwaiVersion||activeTab!=='juwai'||document.hidden)return;
    if(results[0].status==='rejected'){
      $('juwaiStatus').textContent='Stored readings unavailable. Previous values may be out of date.';
      for(const id of ['shed','garage']){$(id+'Card').dataset.stale='true';$(id+'Age').textContent='Update unavailable · previous sample';}
    }
    if(results[1]?.status==='rejected')$('juwaiHistoryNote').textContent='Stored history unavailable. Previous chart may be out of date.';
  }finally{
    if(version===juwaiVersion&&activeTab==='juwai'&&!document.hidden){juwaiController=null;juwaiTimer=setTimeout(()=>readJuwai(version),30000);}
  }
}
$('juwaiMetric').addEventListener('change',drawJuwai);
$('juwaiWindow').addEventListener('change',()=>{stopJuwai();juwaiHistoryKey='';juwaiHistory={};drawJuwai();readJuwai(juwaiVersion);});

const ranges = {"15m":900,"30m":1800,"1h":3600,"2h":7200,"4h":14400,"8h":28800,"1d":86400,"24h":86400,"7d":604800,"30d":2592000,"90d":7776000,"365d":31536000};
let selected = '15m';
let customBounds = null;
try { customBounds = JSON.parse(localStorage.getItem('modbus-custom') || 'null'); } catch (_) {}
if (!(selected in ranges) && !(selected === 'custom' && customBounds && customBounds.start < customBounds.end && customBounds.end <= Date.now() && customBounds.end - customBounds.start <= 365*86400000)) selected = '15m';
const localInput = timestamp => {const d = new Date(timestamp);return new Date(timestamp-d.getTimezoneOffset()*60000).toISOString().slice(0,16);};
$('dateFrom').value = localInput(customBounds?.start || Date.now()-86400000);
$('dateTo').value = localInput(customBounds?.end || Date.now());
$('dateFrom').max = $('dateTo').max = localInput(Date.now());
$('timeWindow').value = selected;
$('customRange').hidden = selected !== 'custom';
let points = [], step = 3000, cursor = 0, lastHistory = 0, generation = 0;
let displayStart = 0, displayEnd = 0, controller = null, calendarBounds = null;
const spanSeconds = () => selected === 'custom' ? (customBounds.end-customBounds.start)/1000 : selected === '1d' && calendarBounds ? (calendarBounds.end-calendarBounds.start)/1000 : ranges[selected];
const chart = new Chart($('lineChart'), {
  type:'line', data:{datasets:[{label:'Net power',data:[],parsing:false,
    borderColor:'#38bdf8',backgroundColor:'rgba(56,189,248,.08)',
    borderWidth:2,pointRadius:0,pointHitRadius:12,fill:{target:'origin',above:'rgba(56,189,248,.08)',below:'rgba(52,211,153,.08)'},
    spanGaps:false,tension:0,
    segment:{borderColor:ctx => ctx.p1.parsed.y < 0 ? '#34d399' : '#38bdf8'}}]},
  options:{responsive:true,maintainAspectRatio:false,animation:false,normalized:true,
    scales:{x:{type:'linear',grid:{color:'#1e293b'},ticks:{color:'#94a3b8',maxTicksLimit:6,
      callback:value => new Date(value).toLocaleString(undefined,spanSeconds()<=14400 ? {hour:'2-digit',minute:'2-digit'} : spanSeconds()<=90000 ? {weekday:'short',hour:'2-digit',minute:'2-digit',...(selected==='1d'?{timeZone:'Europe/London'}:{})} : {day:'numeric',month:'short'})}},
    y:{grid:{color:c=>c.tick.value===0?'#f59e0b':'#1e293b',lineWidth:c=>c.tick.value===0?2:1},ticks:{color:'#94a3b8',callback:v=>v.toLocaleString()+' W'}}},
    plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>new Date(items[0].parsed.x).toLocaleString(),
      label:ctx=>{const p=ctx.raw;return [`${p.y<0?'Exporting':'Importing'}: ${Math.abs(p.y).toLocaleString()} W`, ...(p.low!==p.high?[`Range: ${p.low.toLocaleString()} to ${p.high.toLocaleString()} W`]:[])];}}}}}
});
const donut = new Chart($('donutChart'),{type:'doughnut',data:{labels:['Imported','Exported'],datasets:[{data:[0,0],backgroundColor:['#38bdf8','#34d399'],borderColor:'#141c2e',borderWidth:3}]},options:{responsive:true,maintainAspectRatio:false,animation:false,cutout:'70%',plugins:{legend:{display:false}}}});
function energy(value) {return value>=1000?(value/1000).toLocaleString(undefined,{maximumFractionDigits:2})+' kWh':value.toLocaleString(undefined,{maximumFractionDigits:2})+' Wh';}
function updateLive(data) {
  const meter=data.meter;
  $('meterKwh').innerText=meter ? meter.kwh.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2,useGrouping:false})+' kWh' : '— kWh';
  $('meterUpdated').innerText=meter ? (meter.stale?'Reading unavailable · last update ':'Updated ')+new Date(meter.ts).toLocaleTimeString() : 'Waiting for meter reading';
  $('meterKwh').style.opacity=meter?.stale?'.55':'1';
  const latest=data.latest;
  if(!latest){$('lastReading').innerText='Waiting for meter';return;}
  const val=latest.value;
  $('currentVal').innerText=(val>0?'+':'')+val.toLocaleString(undefined,{maximumFractionDigits:2})+' W';
  $('currentVal').style.color=val<0?'#34d399':'#38bdf8';
  $('currentVal').style.opacity=data.stale?'.55':'1';
  $('lastReading').innerText=(data.stale?'Reading unavailable · last update ':'Last reading ')+new Date(latest.ts).toLocaleTimeString();
  cursor=latest.ts;
}
function updateEnergy(summary) {
  $('totalImportVal').innerText=energy(summary.import_wh);
  $('totalExportVal').innerText=energy(summary.export_wh);
  donut.data.datasets[0].data=[summary.import_wh,summary.export_wh];donut.update('none');
  const fmt = timestamp => new Date(timestamp).toLocaleString(undefined,{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',...(selected==='1d'?{timeZone:'Europe/London'}:{})});
  $('selectedPeriod').innerText=fmt(displayStart)+' – '+fmt(displayEnd);
  $('energyNote').innerText=summary.coverage_s>0 ? 'Energy recorded during the selected period.' : 'No recorded energy intervals in this period.';
  if(selected==='1d') $('energyNote').innerText+=' Today, midnight to midnight (UK time). Totals build through the day and reset at midnight.';
  else if(selected!=='custom') $('energyNote').innerText+=' Rolling period: totals can decrease as older readings leave the window.';
  if(summary.resolution_s>1) $('energyNote').innerText+=' Older boundaries use complete minute groups.';
}
function draw(end=Date.now()) {
  displayEnd=selected==='custom'?customBounds.end:selected==='1d'?calendarBounds.end:end;
  displayStart=selected==='custom'?customBounds.start:selected==='1d'?calendarBounds.start:displayEnd-ranges[selected]*1000;
  points=points.filter(p=>p.x>=displayStart-step && p.x<=displayEnd);
  const expanded=[];
  for(const p of points){const prev=expanded[expanded.length-1];if(prev&&p.x-prev.x>Math.max(12000,step*2.1))expanded.push({x:prev.x+step,y:null});expanded.push(p);}
  chart.data.datasets[0].data=expanded;
  chart.options.scales.x.min=displayStart;chart.options.scales.x.max=displayEnd;chart.update('none');
}
async function getJSON(url,signal){const response=await fetch(url,{signal,cache:'no-store'});if(!response.ok)throw new Error('Readings unavailable');return response.json();}
async function loadHistory(){
  const version=++generation;
  if(controller)controller.abort();controller=new AbortController();
  const query=selected==='custom'?'start='+customBounds.start+'&end='+customBounds.end:'window='+selected;
  const data=await getJSON('/api/history?'+query,controller.signal);
  if(version!==generation)return;
  if(selected==='1d') calendarBounds={start:data.start,end:data.end};
  step=data.step_ms;points=data.points.map(p=>({x:p[0],y:p[1],low:p[2],high:p[3],n:p[4]}));
  updateLive(data.live);draw(data.end);updateEnergy(data.energy);lastHistory=Date.now();
  $('historyNote').innerText=(points.length ? step<=3000?'Individual readings':`Average power in ${step<60000?Math.round(step/1000)+' second':step<3600000?Math.round(step/60000)+' minute':Math.round(step/3600000)+' hour'} groups; hover to see the range` : 'No readings in this period')+'. History builds from the start of local recording. Missing readings are left as gaps. Energy totals cover recorded intervals only.';
}
$('timeWindow').addEventListener('change',async()=>{
  const choice=$('timeWindow').value;
  $('customRange').hidden=choice!=='custom';$('rangeError').innerText='';
  $('dateFrom').max=$('dateTo').max=localInput(Date.now());
  if(choice==='custom') return;
  selected=choice;localStorage.setItem('modbus-window',selected);
  try{await loadHistory();}catch(e){if(e.name!=='AbortError')$('lastReading').innerText='Connection lost';}
});
$('customRange').addEventListener('submit',async event=>{
  event.preventDefault();
  const start=new Date($('dateFrom').value).getTime(),end=new Date($('dateTo').value).getTime();
  let error='';
  if(!Number.isFinite(start)||!Number.isFinite(end)) error='Choose a From and To date and time.';
  else if(start>=end) error='To must be later than From.';
  else if(end>Date.now()) error='Choose dates up to the current time.';
  else if(end-start>365*86400000) error='Choose a period of up to 365 days.';
  $('rangeError').innerText=error;if(error)return;
  selected='custom';customBounds={start,end};
  localStorage.setItem('modbus-window',selected);localStorage.setItem('modbus-custom',JSON.stringify(customBounds));
  $('applyRange').disabled=true;
  try{await loadHistory();}catch(e){if(e.name!=='AbortError')$('rangeError').innerText='Could not load this period. Please try again.';}
  finally{$('applyRange').disabled=false;}
});
async function tick(){
  try {
    if(!document.hidden && activeTab==='power'){
      if(!lastHistory || selected!=='custom' && Date.now()-lastHistory>60000){await loadHistory();}
      else {
        const version=generation;
        const data=await getJSON('/api/live?since='+cursor+(selected==='custom'?'':'&window='+selected));
        if(version===generation && activeTab==='power'){
          updateLive(data);
          if(selected!=='custom'){
            if(data.reload || selected==='1d' && data.period.start!==calendarBounds?.start)await loadHistory();
            else {
              if(ranges[selected]<=14400){
                for(const p of data.points){
                  const x=Math.floor(p[0]/step)*step,tail=points[points.length-1];
                  if(tail&&tail.x===x){tail.y=(tail.y*tail.n+p[1])/(tail.n+1);tail.n++;tail.low=Math.min(tail.low,p[1]);tail.high=Math.max(tail.high,p[1]);}
                  else points.push({x,y:p[1],low:p[1],high:p[1],n:1});
                }
              }
              if(selected==='1d')calendarBounds=data.period;
              draw(data.server_time);if(data.energy)updateEnergy(data.energy);
            }
          }
        }
      }
    }
  } catch(e){if(e.name!=='AbortError'){$('lastReading').innerText='Connection lost';$('currentVal').style.opacity='.55';}}
  finally{setTimeout(tick,3000);}
}
document.addEventListener('visibilitychange',()=>{
  stopVoltage();stopJuwai();
  if(!document.hidden){
    if(activeTab==='voltage')readVoltage(voltageVersion);
    else if(activeTab==='juwai')readJuwai(juwaiVersion);
    else if(selected!=='custom')lastHistory=0;
  } else if(activeTab==='voltage') {
    $('voltageStatus').innerText='Paused while this page is hidden';$('voltageValue').style.opacity='.55';
  }
});
window.addEventListener('pagehide',()=>{stopVoltage();stopJuwai();});
window.addEventListener('pageshow',event=>{if(event.persisted&&!document.hidden){if(activeTab==='voltage')readVoltage(voltageVersion);else if(activeTab==='juwai')readJuwai(juwaiVersion);}});
tick();
