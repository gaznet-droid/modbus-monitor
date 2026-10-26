// Pure JS unit harness: no real browser, network, device or DOM mutations.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map(),listeners={},requests=[],charts=[];let hidden=false;
const periods={today:'Today',live:'Live', '24h':'24h','7d':'Week','30d':'Month'};
function element(id){if(!elements.has(id))elements.set(id,{value:id==='ewelinkPeriod'?'today':id==='ewelinkDevice'?'fridge':'',hidden:false,dataset:{},handlers:{},textContent:'',addEventListener(t,f){this.handlers[t]=f;},replaceChildren(...children){this.children=children;},get selectedOptions(){return [{textContent:id==='ewelinkPeriod'?periods[this.value]:'Fridge'}]}});return elements.get(id);}
const media={matches:false,addEventListener(t,f){this.handler=f;}};
const now=Date.now(),bin=(i,v)=>({start:now+i*3600000,end:now+(i+1)*3600000,mean_w:v,samples:v===null?0:3,partial:v===null});
class Chart{constructor(canvas,config){this.config=config;this.data=config.data;this.scales={x:{getValueForPixel:x=>x}};charts.push(this);}destroy(){this.destroyed=true;}}
const sandbox={Chart,console,Date,Intl,Number,Math,String,encodeURIComponent,AbortController,matchMedia:()=>media,location:{hash:''},setTimeout:()=>1,clearTimeout(){},document:{getElementById:element,get hidden(){return hidden;},createElement:()=>({}),querySelectorAll:()=>[],addEventListener(t,f){listeners[t]=f;}},fetch:async(url)=>{requests.push(url);return {ok:true,json:async()=>url.includes('/latest')?{status:'Connected',devices:[]}:({window:element('ewelinkPeriod').value,points:[[now-600000,0],[now-300000,20]],bucket_ms:300000,bucket_unit:element('ewelinkPeriod').value==='30d'?'week':element('ewelinkPeriod').value==='7d'?'day':'hour',bins:[bin(0,null),bin(1,0),bin(2,20)],daily_bins:[bin(0,null),bin(1,0),bin(2,20)]})};}};
sandbox.window={addEventListener(t,f){listeners[t]=f;}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[2]||'router/web/ewelink.js','utf8'),sandbox);
const flush=()=>new Promise(r=>setImmediate(r));
async function period(value){element('ewelinkPeriod').value=value;element('ewelinkPeriod').handlers.change();await flush();await flush();}
(async()=>{
 sandbox.window.startEwelink();await flush();await flush();assert.equal(charts.at(-1).config.type,'line');
 const count=requests.length;media.matches=true;media.handler();assert.equal(charts.at(-1).config.type,'bar');assert.equal(requests.length,count);
 assert.equal(charts.at(-1).data.datasets[0].data[0],null);assert.equal(charts.at(-1).data.datasets[0].data[1],0);
 charts.at(-1).config.options.onClick({x:1});assert.match(element('ewelinkDetails').textContent,/0 W/);
 await period('live');assert.equal(charts.at(-1).config.type,'line');
 for(const value of ['24h','7d','30d']){await period(value);assert.equal(charts.at(-1).config.type,'bar');}
 const before=requests.length;element('ewelinkDrill').handlers.click();assert.equal(requests.length,before);assert.equal(element('ewelinkBack').hidden,false);
 element('ewelinkBack').handlers.click();assert.equal(requests.length,before);assert.equal(element('ewelinkBack').hidden,true);
 hidden=true;listeners.visibilitychange();sandbox.window.startEwelink();await flush();assert.equal(requests.length,before);
 hidden=false;listeners.visibilitychange();await flush();await flush();assert.ok(requests.length>before);
 assert.ok(requests.every(url=>url.startsWith('/api/ewelink/')));
 console.log('PASS: desktop lines, mobile hourly/daily/weekly bars, zero vs missing, selection, drill/back without fetch, resize reuse, hidden pause, cached-only requests.');
})().catch(e=>{console.error(e);process.exitCode=1;});
