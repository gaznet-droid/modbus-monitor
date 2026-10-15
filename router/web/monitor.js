const $ = id => document.getElementById(id);
const ranges = {"15m":900,"30m":1800,"1h":3600,"2h":7200,"4h":14400,"1d":86400,"7d":604800,"30d":2592000,"90d":7776000,"365d":31536000};
let selected = localStorage.getItem('modbus-window') || '4h';
if (!ranges[selected]) selected = '4h';
$('timeWindow').value = selected;
let points = [], step = 3000, cursor = 0, lastHistory = 0, generation = 0;
let controller = null;
const chart = new Chart($('lineChart'), {
  type:'line', data:{datasets:[{label:'Net power',data:[],parsing:false,
    borderColor:'#38bdf8',backgroundColor:'rgba(56,189,248,.08)',
    borderWidth:2,pointRadius:0,pointHitRadius:12,fill:{target:'origin',above:'rgba(56,189,248,.08)',below:'rgba(52,211,153,.08)'},
    spanGaps:false,tension:0,
    segment:{borderColor:ctx => ctx.p1.parsed.y < 0 ? '#34d399' : '#38bdf8'}}]},
  options:{responsive:true,maintainAspectRatio:false,animation:false,normalized:true,
    scales:{x:{type:'linear',grid:{color:'#1e293b'},ticks:{color:'#94a3b8',maxTicksLimit:6,
      callback:value => new Date(value).toLocaleString(undefined,ranges[selected]<=14400 ? {hour:'2-digit',minute:'2-digit'} : ranges[selected]<=86400 ? {weekday:'short',hour:'2-digit',minute:'2-digit'} : {day:'numeric',month:'short'})}},
    y:{grid:{color:c=>c.tick.value===0?'#f59e0b':'#1e293b',lineWidth:c=>c.tick.value===0?2:1},ticks:{color:'#94a3b8',callback:v=>v.toLocaleString()+' W'}}},
    plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>new Date(items[0].parsed.x).toLocaleString(),
      label:ctx=>{const p=ctx.raw;return [`${p.y<0?'Exporting':'Importing'}: ${Math.abs(p.y).toLocaleString()} W`, ...(p.low!==p.high?[`Range: ${p.low.toLocaleString()} to ${p.high.toLocaleString()} W`]:[])];}}}}}
});
const donut = new Chart($('donutChart'),{type:'doughnut',data:{labels:['Imported','Exported'],datasets:[{data:[0,0],backgroundColor:['#38bdf8','#34d399'],borderColor:'#141c2e',borderWidth:3}]},options:{responsive:true,maintainAspectRatio:false,animation:false,cutout:'70%',plugins:{legend:{display:false}}}});
function energy(value) {return value>=1000?(value/1000).toLocaleString(undefined,{maximumFractionDigits:2})+' kWh':value.toLocaleString(undefined,{maximumFractionDigits:2})+' Wh';}
function updateLive(data) {
  const latest=data.latest;
  if(!latest){$('modeBadge').innerText='Waiting for meter';$('lastSeen').innerText='No readings recorded';return;}
  const val=latest.value;
  $('currentVal').innerText=(val>0?'+':'')+val.toLocaleString(undefined,{maximumFractionDigits:2})+' W';
  $('currentVal').style.color=val<0?'#34d399':'#38bdf8';
  $('modeBadge').className='mode-badge '+(val<0?'export-badge':'import-badge');
  $('modeBadge').innerText=data.stale?'Reading unavailable':val<0?'Exporting':'Importing';
  $('modeBadge').style.opacity=data.stale?'.55':'1';
  $('lastSeen').innerText='Last reading '+new Date(latest.ts).toLocaleTimeString();
  $('totalImportVal').innerText=energy(latest.import_wh);
  $('totalExportVal').innerText=energy(latest.export_wh);
  donut.data.datasets[0].data=[latest.import_wh,latest.export_wh];donut.update('none');
  cursor=latest.ts;
}
function draw(end=Date.now()) {
  const start=end-ranges[selected]*1000;
  points=points.filter(p=>p.x>=start);
  const expanded=[];
  for(const p of points){const prev=expanded[expanded.length-1];if(prev&&p.x-prev.x>Math.max(12000,step*2.1))expanded.push({x:prev.x+step,y:null});expanded.push(p);}
  chart.data.datasets[0].data=expanded;
  chart.options.scales.x.min=start;chart.options.scales.x.max=end;chart.update('none');
}
async function getJSON(url,signal){const response=await fetch(url,{signal,cache:'no-store'});if(!response.ok)throw new Error('Readings unavailable');return response.json();}
async function loadHistory(){
  const version=++generation;
  if(controller)controller.abort();controller=new AbortController();
  const data=await getJSON('/api/history?window='+selected,controller.signal);
  if(version!==generation)return;
  step=data.step_ms;points=data.points.map(p=>({x:p[0],y:p[1],low:p[2],high:p[3],n:p[4]}));
  updateLive(data.live);draw(data.end);lastHistory=Date.now();
  $('historyNote').innerText=(step<=3000?'Individual readings':`Average power in ${step<60000?Math.round(step/1000)+' second':step<3600000?Math.round(step/60000)+' minute':Math.round(step/3600000)+' hour'} groups; hover to see the range`)+'. History builds from the start of local recording. Missing readings are left as gaps. Energy totals cover recorded intervals only.';
}
$('timeWindow').addEventListener('change',async()=>{selected=$('timeWindow').value;localStorage.setItem('modbus-window',selected);try{await loadHistory();}catch(e){if(e.name!=='AbortError')$('modeBadge').innerText='Connection lost';}});
async function tick(){
  try {
    if(!document.hidden){
      if(!lastHistory || Date.now()-lastHistory>60000){await loadHistory();}
      else {
        const version=generation;
        const data=await getJSON('/api/live?since='+cursor);
        if(version===generation){
          updateLive(data);
          if(data.reload)await loadHistory();
          else if(ranges[selected]<=14400){
            for(const p of data.points){
              const x=Math.floor(p[0]/step)*step,tail=points[points.length-1];
              if(tail&&tail.x===x){tail.y=(tail.y*tail.n+p[1])/(tail.n+1);tail.n++;tail.low=Math.min(tail.low,p[1]);tail.high=Math.max(tail.high,p[1]);}
              else points.push({x,y:p[1],low:p[1],high:p[1],n:1});
            }
            draw(data.server_time);
          }
        }
      }
    }
  } catch(e){if(e.name!=='AbortError'){$('modeBadge').innerText='Connection lost';$('modeBadge').style.opacity='.55';}}
  finally{setTimeout(tick,3000);}
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)lastHistory=0;});
tick();
