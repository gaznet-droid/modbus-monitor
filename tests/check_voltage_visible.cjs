// Exercise visibility/version guards in pure JS without a browser or meter.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const html=fs.readFileSync('router/web/index.html','utf8');
const source=html.slice(html.indexOf('let voltageVisible='),html.indexOf('function selectTab(name)'));
const nodes=new Map(),requests=[],timers=[];let observer,updates=0,hidden=false;
const el=id=>{if(!nodes.has(id))nodes.set(id,{style:{},textContent:'',innerText:''});return nodes.get(id);};
const context={console,Date,Number,Math,AbortController,$:el,document:{get hidden(){return hidden;}},setTimeout(fn,delay){timers.push({fn,delay});return timers.length;},clearTimeout(){},getJSON(url){requests.push(url);return new Promise(resolve=>pending.push(resolve));},IntersectionObserver:class{constructor(callback){observer=callback;}observe(){}},Chart:class{constructor(_,config){Object.assign(this,config);}resize(){}update(){updates++;}}};
const pending=[];vm.createContext(context);vm.runInContext("let activeTab='power';"+source,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 assert.equal(requests.length,0);
 observer([{isIntersecting:false,intersectionRatio:0}]);assert.equal(requests.length,0);
 observer([{isIntersecting:true,intersectionRatio:.5}]);assert.equal(requests.length,1);
 observer([{isIntersecting:false,intersectionRatio:0}]);pending.shift()({ts:Date.now(),volts:238});await flush();assert.equal(updates,0);
 observer([{isIntersecting:true,intersectionRatio:.5}]);assert.equal(requests.length,2);
 pending.shift()({ts:Date.now(),volts:239});await flush();assert.equal(updates,1);
 const timer=timers.findLast(t=>t.delay===3000);assert.ok(timer);
 vm.runInContext("activeTab='juwai';refreshVoltage()",context);timer.fn();await flush();assert.equal(requests.length,2);
 vm.runInContext("activeTab='power'",context);hidden=true;context.refreshVoltage();assert.equal(requests.length,2);
 hidden=false;vm.runInContext('voltageSuspended=true;refreshVoltage()',context);assert.equal(requests.length,2);
 vm.runInContext('voltageSuspended=false;refreshVoltage()',context);assert.equal(requests.length,3);
 pending.shift()({ts:Date.now(),volts:240});await flush();
 assert.ok(requests.every(url=>url==='/api/voltage'));
 console.log('PASS: no initial voltage calls; viewport starts/stops; stale response discarded; tab/hidden/pagehide guards; resume works.');
})().catch(e=>{console.error(e);process.exitCode=1;});
