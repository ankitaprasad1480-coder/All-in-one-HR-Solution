/* Preview only: stands in for the server so the screens can be clicked through without one.
   Accounts live in this browser, data is made-up demo data. NOT secure and NOT used by the real app. */
(function(){
const LS={g:k=>{try{return localStorage.getItem(k)}catch(e){return null}},s:(k,v)=>{try{localStorage.setItem(k,v)}catch(e){}}};
let mem=null;const load=()=>mem||(mem=JSON.parse(LS.g('dd_preview')||'{}')),save=()=>LS.s('dd_preview',JSON.stringify(mem));
let sess=null;
const hx=async s=>{try{const d=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s));return [...new Uint8Array(d)].map(x=>x.toString(16).padStart(2,'0')).join('')}catch(e){return btoa(unescape(encodeURIComponent(s)))}};
const A5='ABCDEFGHJKLMNPQRSTUVWXYZ23456789',rc=()=>{let s='';for(let i=0;i<20;i++)s+=A5[Math.floor(Math.random()*A5.length)];return s.match(/.{5}/g).join('-')};
const nc=s=>String(s).toUpperCase().replace(/[^A-Z0-9]/g,'');
const bad=(m,c)=>{const e=new Error(m);e.status=c||400;throw e};
const CFGM={industries:["Technology","Hospitality","Retail","Healthcare","Education","Manufacturing","Finance","Construction","Logistics","Other"],sizes:["1-10","11-50","51-200","201-1000","1000+"],currencies:["AED","USD","INR","EUR","GBP","SAR","QAR","KWD","BHD","OMR","PKR","LKR","BDT","THB","VND","PHP","IDR","MYR","SGD","CAD","AUD","ZAR","EGP","TRY"],signup_code_required:false,demo:true};
const meOf=a=>({account:a.account,company:a.company,has_data:!!a.has,filename:a.file||null,updated_at:a.has?Date.now()/1000:null});
const cur=()=>{if(!sess)bad('Please sign in.',401);const a=load()[sess];if(!a)bad('Please sign in.',401);return a};
const co=b=>({name:b.company,industry:b.industry,size:b.size,country:b.country,data_currency:b.data_currency,display_currency:b.display_currency});
window.MOCK_API=async(p,b)=>{const D=load();b=b||{};
switch(p){
case 'config':return CFGM;
case 'signup':{const e=(b.email||'').trim().toLowerCase();
 if(!b.company||!b.industry||!b.size||!b.country||!b.name||!e)bad('Please fill in all the required fields.');
 if(!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(e))bad('Please enter a valid email address.');
 if((b.password||'').length<10)bad('Password must be 10 to 128 characters.');
 if(D[e])bad('An account with this email already exists. Try signing in.',409);
 const code=rc();D[e]={account:{email:e,name:b.name,phone:b.phone||''},company:co(b),pw:await hx(e+':'+b.password),rc:await hx(nc(code))};save();sess=e;return{recovery_code:code,...meOf(D[e])}}
case 'login':{const e=(b.email||'').trim().toLowerCase(),a=D[e];if(!a||a.pw!==await hx(e+':'+(b.password||'')))bad('Incorrect email or password.',401);sess=e;return meOf(a)}
case 'logout':sess=null;return{ok:true};
case 'me':return meOf(cur());
case 'data':{const a=cur();return a.has?{records:makeDemo(),report:demoReport()}:{records:[],report:null}}
case 'upload':{const a=cur();a.has=true;a.file=b.filename||'employees.csv';save();return{records:makeDemo(),report:demoReport()}}
case 'company':{const a=cur();a.company=co(b);save();return meOf(a)}
case 'password':{const a=cur();if(a.pw!==await hx(a.account.email+':'+(b.current||'')))bad('Your current password is not correct.',403);if((b.new||'').length<10)bad('Password must be 10 to 128 characters.');a.pw=await hx(a.account.email+':'+b.new);save();return{ok:true}}
case 'recovery':{const a=cur();if(a.pw!==await hx(a.account.email+':'+(b.password||'')))bad('Your password is not correct.',403);const c=rc();a.rc=await hx(nc(c));save();return{recovery_code:c}}
case 'reset':{const e=(b.email||'').trim().toLowerCase(),a=D[e];if(!a||a.rc!==await hx(nc(b.recovery_code||'')))bad('The email or recovery code is not correct.',401);if((b.new_password||'').length<10)bad('Password must be 10 to 128 characters.');a.pw=await hx(e+':'+b.new_password);const c=rc();a.rc=await hx(nc(c));save();sess=e;return{recovery_code:c,...meOf(a)}}
case 'delete':{const a=cur();if(a.pw!==await hx(a.account.email+':'+(b.password||'')))bad('Your password is not correct.',403);delete D[sess];save();sess=null;return{ok:true}}
}
bad('Not found.',404)};
})();
