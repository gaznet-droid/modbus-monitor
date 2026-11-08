// Shared by the sign-in and admin pages: passkeys (Face ID, Touch ID, fingerprint) and small helpers.
const b64e=buf=>btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
const b64d=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-s.length%4)%4)),c=>c.charCodeAt(0));
async function authPost(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{}),credentials:'same-origin'});
  const data=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(data.error||'Something went wrong. Please try again.');
  return data;
}
function passkeyMessage(error){
  if(error?.name==='NotAllowedError')return 'Cancelled or timed out. Please try again.';
  if(error?.name==='InvalidStateError')return 'This device already has a passkey for this person.';
  if(error?.name==='SecurityError')return 'Passkeys only work on the monitor\'s own web address.';
  return error?.message||'Something went wrong. Please try again.';
}
const passkeysSupported=()=>!!(window.PublicKeyCredential&&navigator.credentials);
async function passkeySignIn(){
  const o=await authPost('/auth/passkey/options');o.publicKey.challenge=b64d(o.publicKey.challenge);
  const c=await navigator.credentials.get({publicKey:o.publicKey});
  await authPost('/auth/passkey/login',{challengeId:o.challengeId,id:b64e(c.rawId),clientDataJSON:b64e(c.response.clientDataJSON),
    authenticatorData:b64e(c.response.authenticatorData),signature:b64e(c.response.signature),userHandle:c.response.userHandle?b64e(c.response.userHandle):null});
}
async function passkeyCreate(link){
  const o=await authPost('/auth/passkey/register-options',link?{link}:{}),pk=o.publicKey;
  pk.challenge=b64d(pk.challenge);pk.user.id=b64d(pk.user.id);pk.excludeCredentials=pk.excludeCredentials.map(x=>({...x,id:b64d(x.id)}));
  const c=await navigator.credentials.create({publicKey:pk});
  await authPost('/auth/passkey/register',{challengeId:o.challengeId,id:b64e(c.rawId),clientDataJSON:b64e(c.response.clientDataJSON),attestationObject:b64e(c.response.attestationObject)});
  return o.name;
}
