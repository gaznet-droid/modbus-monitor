// Run against the isolated preview server and a disposable Chrome debug profile.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
(async()=>{
 const meta=await (await fetch(process.env.BROWSER_DEBUG_URL||'http://127.0.0.1:9225/json/version')).json();
 const ws=new WebSocket(meta.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
 let id=0;const pending=new Map();
 ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.id){const p=pending.get(d.id);pending.delete(d.id);if(d.error)p.reject(Error(JSON.stringify(d.error)));else p.resolve(d.result);}};
 function send(method,params={},sessionId){return new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params,...(sessionId?{sessionId}:{})}));});}
 const target=await send('Target.createTarget',{url:'about:blank'});
 const {sessionId}=await send('Target.attachToTarget',{targetId:target.targetId,flatten:true});
 const cmd=(method,params)=>send(method,params,sessionId);
 async function js(expression){const r=await cmd('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.text+': '+r.exceptionDetails.exception?.description);return r.result.value;}
 async function until(expression){for(let n=0;n<100;n++){if(await js(expression))return;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out: '+expression);}
 try{
  await cmd('Page.enable');await cmd('Runtime.enable');
  await cmd('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await cmd('Page.navigate',{url:process.env.APP_URL||'http://127.0.0.1:8766/'});
  await until("document.getElementById('currentVal')?.textContent.includes('150')");
  assert.equal(await js("__requests.filter(u=>u.includes('/voltage')||u.includes('/tuya/')).length"),0);
  await js("document.getElementById('juwaiTab').click()");
  await until("document.getElementById('shedPower').textContent==='1.02 W' && juwaiHistoryKey==='today'");
  assert.equal(await js("__requests.filter(u=>u.startsWith('/api/tuya/latest')).length"),1);
  assert.equal(await js("__requests.filter(u=>u.startsWith('/api/tuya/history')).length"),2);
  assert.equal(await js("__requests.filter(u=>u.startsWith('/api/voltage')).length"),0);
  const count=await js('__requests.length');
  await js("document.getElementById('juwaiMetric').value='voltage_v';document.getElementById('juwaiMetric').dispatchEvent(new Event('change'))");
  assert.equal(await js('__requests.length'),count);
  assert.equal(await js("document.getElementById('juwaiStatus').hidden"),true);
  assert.equal(await js("document.getElementById('totalEnergy').textContent"),'3.750 kWh');
  assert.equal(await js("document.getElementById('shedEnergy').textContent"),'1.250 kWh');
  assert.equal(await js("document.getElementById('totalPower').textContent"),'19.39 W');
  for(const range of ['live','24h','previous24h','yesterday','7d','30d','today']){
    await js("document.getElementById('juwaiWindow').value='"+range+"';document.getElementById('juwaiWindow').dispatchEvent(new Event('change'))");
    await until("juwaiHistoryKey==='"+range+"'");
    assert.equal(await js("document.getElementById('totalEnergy').textContent"),'3.750 kWh');
  }
  assert.equal(await js('document.documentElement.scrollWidth<=innerWidth'),true);
  assert.equal(await js("document.getElementById('juwaiPanel').parentElement.classList.contains('container')"),true);
  assert.equal(await js("getComputedStyle(document.querySelector('.juwai-grid')).gridTemplateColumns.split(' ').length"),2);
  await js("document.getElementById('juwaiMetric').value='power_w';document.getElementById('juwaiMetric').dispatchEvent(new Event('change'))");
  assert.equal(await js("mobileChart.config.type"),'bar');
  assert.equal(await js('mobileChart.data.datasets.length'),2);
  assert.equal(await js('juwaiChart.data.datasets.length'),3);
  await js("showMobileBin(0)");
  assert.match(await js("document.getElementById('mobileBucketDetails').textContent"),/No data/);
  await js("showMobileBin(2)");
  assert.match(await js("document.getElementById('mobileBucketDetails').textContent"),/0.000 kWh/);
  for(const range of ['7d','30d']){
    await js("document.getElementById('juwaiWindow').value='"+range+"';document.getElementById('juwaiWindow').dispatchEvent(new Event('change'))");
    await until("juwaiHistoryKey==='"+range+"'");
    assert.equal(await js('mobileChart.config.type'),'bar');
  }
  await js("document.getElementById('mobileJuwaiChart').scrollIntoView({block:'center'});showMobileBin(0)");
  const tap=await js("(()=>{const r=document.getElementById('mobileJuwaiChart').getBoundingClientRect();return {x:r.left+mobileChart.scales.x.getPixelForValue(mobileBins.length-1),y:r.top+(mobileChart.chartArea.top+mobileChart.chartArea.bottom)/2}})()");
  await cmd('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[tap]});
  await cmd('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await until('mobileBinIndex===mobileBins.length-1');
  const beforeDrill=await js('__requests.length');
  await js("document.getElementById('mobileWeekDrill').click()");
  assert.equal(await js('mobileWeek!==null'),true);
  assert.equal(await js('__requests.length'),beforeDrill);
  await js("document.getElementById('mobileWeekBack').click()");
  assert.equal(await js('mobileWeek'),null);
  await js("document.getElementById('juwaiWindow').value='live';document.getElementById('juwaiWindow').dispatchEvent(new Event('change'))");
  await until("juwaiHistoryKey==='live'");
  assert.equal(await js('mobileChart.config.type'),'line');
  assert.equal(await js('mobileChart.data.datasets.length'),1);
  assert.equal(await js('mobileChart.data.datasets[0].pointRadius'),0);
  for(const metric of ['voltage_v','current_a']){await js("document.getElementById('juwaiMetric').value='"+metric+"';document.getElementById('juwaiMetric').dispatchEvent(new Event('change'))");assert.equal(await js('mobileChart.config.type'),'line');}
  await js("document.getElementById('juwaiMetric').value='power_w';document.getElementById('juwaiMetric').dispatchEvent(new Event('change'))");
  await js("document.getElementById('juwaiWindow').value='7d';document.getElementById('juwaiWindow').dispatchEvent(new Event('change'))");
  await until("juwaiHistoryKey==='7d'");
  assert.equal(await js('document.documentElement.scrollWidth<=innerWidth'),true);
  let shot=await cmd('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});fs.writeFileSync(path.join(os.tmpdir(),'modbus-juwai-mobile.png'),Buffer.from(shot.data,'base64'));
  await cmd('Emulation.setDeviceMetricsOverride',{width:320,height:844,deviceScaleFactor:1,mobile:true});
  assert.equal(await js('document.documentElement.scrollWidth<=innerWidth'),true);
  await cmd('Emulation.setDeviceMetricsOverride',{width:1200,height:900,deviceScaleFactor:1,mobile:false});
  await until("getComputedStyle(document.querySelector('.juwai-mobile-only')).display==='none'");
  assert.equal(await js('juwaiChart.config.type'),'line');
  assert.equal(await js('juwaiChart.data.datasets.length'),3);
  shot=await cmd('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});fs.writeFileSync(path.join(os.tmpdir(),'modbus-juwai-desktop.png'),Buffer.from(shot.data,'base64'));
  await js("window.__testHidden=true;document.dispatchEvent(new Event('visibilitychange'))");
  assert.equal(await js('juwaiTimer===null&&juwaiController===null'),true);
  const hidden=await js('__requests.length');await js('readJuwai(juwaiVersion)');await js('readVoltage(voltageVersion)');assert.equal(await js('__requests.length'),hidden);
  await js("window.__testHidden=false;document.dispatchEvent(new Event('visibilitychange'))");
  await until('juwaiTimer!==null');
  await js("document.getElementById('voltageTab').click()");
  await until("document.getElementById('voltageValue').textContent==='239.40 V'");
  await js("document.getElementById('powerTab').click()");
  assert.equal(await js('voltageTimer===null&&voltageController===null'),true);
  const old=await js("__requests.filter(u=>u.startsWith('/api/voltage')).length");await js('readVoltage(voltageVersion)');assert.equal(await js("__requests.filter(u=>u.startsWith('/api/voltage')).length"),old);
  await js("document.getElementById('powerTab').dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}))");assert.equal(await js('activeTab'),'juwai');
  await js("document.getElementById('juwaiTab').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}))");assert.equal(await js('activeTab'),'power');
  assert.deepEqual(await js('__errors'),[]);
  console.log('PASS: lazy requests, cached metric switches, hidden-tab pause, voltage start/stop, keyboard navigation, mobile/desktop layout, no JS errors.');
 }finally{await send('Target.closeTarget',{targetId:target.targetId});ws.close();}
})().catch(e=>{console.error(e);process.exit(1)});
