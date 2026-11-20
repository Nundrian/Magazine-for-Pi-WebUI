// Magazine native view. Plain browser ESM: no imports, no Node built-ins, so
// the host serves this single file from client/ and nothing can resolve
// outside the client subtree. Data is rendered with textContent only.
// Dashboard, restoration order and compact ledger follow Magazine Simple
// 0.3.0-scheduler.rc1, in Pi's own layout and styling.
const API='/plugins-api/magazine-pi/rpc';
const LAST_RUN='magazine-pi-run';
const LAST_PROJECT='magazine-pi-project';
const STATUS_TEXT={READY:'READY',QUEUED:'QUEUED',RUNNING:'RUNNING',STOP_REQUESTED:'STOP REQUESTED',STOPPED:'STOPPED',HALTED:'HALTED',COMPLETED:'COMPLETED'};
const store={get(k){try{return localStorage.getItem(k)||'';}catch{return '';}},set(k,v){try{v?localStorage.setItem(k,v):localStorage.removeItem(k);}catch{}}};
const isLive=r=>Boolean(r&&(r.active||['RUNNING','QUEUED','STOP_REQUESTED'].includes(r.display_status)));

export function chooseRun(runs, saved) {
  // rc1 order: the remembered run if it is running or queued; otherwise
  // another running run; otherwise the remembered run in any state; otherwise
  // nothing (no invented replacement). Viewing never starts or reorders work.
  const remembered=runs.find(r=>r.run_id===saved);
  if(remembered&&isLive(remembered)) return remembered.run_id;
  const running=runs.find(r=>r.active||r.display_status==='RUNNING');
  if(running) return running.run_id;
  return remembered?remembered.run_id:'';
}

export function tokenLine(t) {
  if(!t||!t.available) return t&&t.attempts?`Tokens: unavailable (${t.attempts} attempt${t.attempts===1?'':'s'} reported no usage)`:'Tokens: unavailable';
  const v=k=>t[k]==null?'unavailable':Number(t[k]).toLocaleString();
  const cache=(t.cache_read||t.cache_write)?` · cache read ${v('cache_read')} / write ${v('cache_write')}`:'';
  const state=t.incomplete?` · incomplete${t.unavailable_attempts?`, ${t.unavailable_attempts} attempt(s) without usage`:''}`:'';
  return `Tokens: ${v('input')} in / ${v('output')} out / ${v('total')} total${cache}${state}`;
}

export const shortModel=value=>String(value||'unavailable').split('/').pop();
export function tokenCell(t) {
  if(!t||!t.attempts) return ['—', ''];
  if(!t||!t.available) return ['unavailable', t&&t.attempts?'not reported':''];
  return [t.total==null?'unavailable':Number(t.total).toLocaleString(), t.incomplete?'incomplete':'complete'];
}
export function duration(seconds, active) {
  if(seconds==null) return '—';
  const s=Math.round(seconds), text=s<120?`${s} s`:(s<7200?`${Math.round(s/60)} min`:`${(s/3600).toFixed(1)} h`);
  return text+(active?' · active':'');
}
/** Token counter figures for one run (or an aggregate). Exact reported counts only. */
export function tokenCounter(t, status) {
  const n=k=>t&&t.available&&t[k]!=null?Number(t[k]).toLocaleString():'—';
  const running=status==='RUNNING'||status==='STOP_REQUESTED';
  let note;
  if(!t||!t.available) note=running?'Waiting for reported usage: Pi reports usage when each model reply ends.':
    (t&&t.attempts?`Token usage unavailable: ${t.attempts} attempt${t.attempts===1?'':'s'} reported no usage.`:'No tokens reported yet.');
  else note=(running?'Live: updates as each model reply ends.':'Reported by Pi for this run.')+
    ((running?t.unavailable_attempts>0:t.incomplete)?` Incomplete${t.unavailable_attempts?`: ${t.unavailable_attempts} attempt${t.unavailable_attempts===1?'':'s'} without reported usage`:''}.`:'');
  const cache=t&&t.available&&(t.cache_read||t.cache_write)?`Cache read ${n('cache_read')} · cache write ${n('cache_write')}`:'';
  return {input:n('input'),output:n('output'),total:n('total'),cache,note};
}
/** Sum of the reported counts across projects (unreported projects are counted, not guessed). */
export function sumTokens(list) {
  const out={available:false,incomplete:false,input:null,output:null,total:null,cache_read:null,cache_write:null,projects:0,unreported:0};
  for(const t of list||[]){
    out.projects++;
    if(!t||!t.available){out.unreported++;continue;}
    out.available=true;if(t.incomplete)out.incomplete=true;
    for(const k of ['input','output','total','cache_read','cache_write']) if(Number.isInteger(t[k])) out[k]=(out[k]||0)+t[k];
  }
  return out;
}
/** Models a workflow declares that are available here: a shared default when every cartridge names the
 *  same available model, otherwise per-cartridge picks for those naming an available model. */
export function declaredModels(cartridges, available) {
  const ids=new Set((available||[]).map(m=>m.id)),named=(cartridges||[]).map(c=>c.job_type||c.model||'');
  const usable=named.filter(m=>ids.has(m));
  if(named.length&&usable.length===named.length&&new Set(usable).size===1)return {shared:usable[0],perCartridge:{}};
  const perCartridge={};(cartridges||[]).forEach((c,i)=>{if(ids.has(named[i]))perCartridge[c.id]=named[i];});
  return {shared:'',perCartridge};
}
/** Human-readable text of a committed handover payload: the /result note or a plain string result. */
export function handoverText(payload) {
  const r=payload&&payload.result;
  if(typeof r==='string')return r;
  if(r&&typeof r==='object'&&typeof r.note==='string'&&r.note.trim())return r.note;
  return '';
}
export function queueText(q) {
  if(!q) return '—';
  if(q.recovery_required) return 'Recovery required';
  if(q.holder) return 'Executing';
  if(q.queued) return q.next?`Next (#${q.position})`:`#${q.position}`;
  return '—';
}

export function ledgerChoice(rows, saved, current) {
  if(rows.some(c=>c.id===saved))return saved;
  return rows.find(c=>c.id===current)?.id||rows.find(c=>c.status==='failed'||c.status==='halted')?.id||rows[0]?.id||'';
}

const STYLE=`
.mag{box-sizing:border-box;max-width:1080px;width:100%;min-width:0;margin:0 auto;padding:16px 20px;color:var(--text);font:13px/1.6 var(--sans);overflow-wrap:anywhere}
.mag h1{font-size:20px;font-weight:600;line-height:1.35;margin:0 0 4px}.mag h2{font-size:14px;font-weight:600;line-height:1.4;margin:0 0 8px}.mag p{margin:6px 0 10px}
.mag section{border:0;border-top:1px solid var(--border-soft);padding:14px 0 0;margin:14px 0 0}
.mag label{display:block;margin:6px 0;color:var(--text-dim)}.mag label.inline{display:inline-block;margin:4px 14px 4px 0}.mag [hidden]{display:none!important}
.mag input,.mag select,.mag textarea{box-sizing:border-box;min-width:0;max-width:100%;color:var(--text);background:var(--bg-elev);border:1px solid var(--border);border-radius:8px;padding:5px 9px;font:inherit}
.mag select,.mag textarea,.mag input[type=search]{display:block;width:100%;margin-top:4px}.mag textarea{min-height:120px;resize:vertical;font-family:var(--mono)}.mag input[type=checkbox],.mag input[type=radio]{accent-color:var(--accent);margin:0 5px 0 0;vertical-align:middle}
.mag button{display:inline-flex;align-items:center;justify-content:center;gap:6px;max-width:100%;color:var(--text);background:var(--bg-elev2);border:1px solid var(--border);border-radius:8px;padding:6px 11px;margin:3px 6px 3px 0;font:inherit;line-height:1.4;cursor:pointer}
.mag button:hover:not(:disabled){background:var(--bg-elev3);border-color:var(--text-faint)}.mag button.primary{background:var(--accent);border-color:var(--accent);color:#fff}.mag button.stop{background:var(--red-soft);border-color:var(--red);color:var(--red-text)}.mag button:disabled{opacity:.4;cursor:default}.mag button.small{padding:2px 8px;font-size:12px}
.mag :is(button,input,select,textarea,summary):focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.mag pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--code-bg);color:var(--code-text);border:1px solid var(--code-border);padding:var(--code-pad);border-radius:6px;max-height:360px;overflow:auto;font:12.5px/1.55 var(--mono);margin:6px 0}
.mag .row{display:flex;align-items:end;gap:12px;flex-wrap:wrap}.mag .row>*{flex:1;min-width:min(100%,200px)}.mag .row>.shrink{flex:0 0 auto;min-width:0}
.mag .status{padding:7px 10px;border-left:2px solid var(--accent);background:var(--accent-soft);border-radius:0 6px 6px 0;white-space:pre-wrap}.mag .error{border-left-color:var(--notice-err-border);background:var(--notice-err-bg);color:var(--err-text)}
.mag details{margin:4px 0}.mag summary{cursor:pointer;color:var(--text-dim);padding:2px 0}.mag summary:hover{color:var(--text)}.mag small,.mag .dim{color:var(--text-dim);font-size:12px}
.mag .badge{display:inline-block;padding:1px 7px;border:1px solid var(--border);border-radius:6px;background:var(--accent-soft);color:var(--text);font-size:11px;font-weight:600;margin-right:6px}
.mag .badge.COMPLETED{border-color:var(--green,var(--border))}.mag .badge.HALTED,.mag .badge.STOPPED,.mag .badge.STOP_REQUESTED{border-color:var(--red);color:var(--red-text);background:var(--red-soft)}
.mag table{border-collapse:collapse;width:100%;font-size:12px}.mag th,.mag td{text-align:left;padding:3px 6px;border-bottom:1px solid var(--border-soft);vertical-align:top}.mag th{color:var(--text-dim);font-weight:600}
.mag .cart{border-bottom:1px solid var(--border-soft);padding:2px 0}.mag .cart summary{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.mag .cart select{display:inline-block;width:auto;margin:0}
.mag .feed{max-height:180px;overflow:auto;margin:0;padding-left:18px;font-size:12px}.mag .feed li.warn{color:var(--red-text)}
@media(max-width:600px){.mag{padding:12px}.mag .row>*{flex-basis:100%}.mag button,.mag select,.mag input:not([type=checkbox]):not([type=radio]){min-height:40px}.mag textarea{font-size:16px}}
.mag .dash{overflow:auto;max-height:60vh;border:1px solid var(--border-soft);border-radius:8px}
.mag .dash table,.mag .ledger-table{table-layout:fixed;min-width:640px}.mag .dash thead th,.mag .ledger-table thead th{position:sticky;top:0;background:var(--bg-elev);z-index:1}
.mag .dash th:first-child,.mag .ledger-table th:first-child{width:24%}.mag .dash td,.mag .ledger-table td{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;vertical-align:middle}
.mag .dash tr[aria-selected=true]{background:var(--accent-soft)}.mag .linkish{background:none;border:0;padding:0;margin:0;color:var(--text);text-align:left;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}
.mag .linkish:hover{text-decoration:underline;background:none}.mag .sub{display:block;color:var(--text-dim);font-size:11px;line-height:1.2}
.mag .ledger-scroll{overflow:auto;max-height:60vh}.mag .attempt{border-top:1px solid var(--border-soft);padding:6px 0;white-space:normal}
.mag .facts{display:grid;grid-template-columns:minmax(110px,26%) minmax(0,1fr);gap:2px 10px;margin:4px 0}.mag .facts dt{color:var(--text-dim)}.mag .facts dd{margin:0;overflow-wrap:anywhere}
.mag .tok{display:flex;flex-wrap:wrap;gap:6px 28px;margin:6px 0 2px;padding:8px 12px;border:1px solid var(--border-soft);border-radius:8px;background:var(--bg-elev)}
.mag .tok h3{flex-basis:100%;margin:0;font-size:12px;font-weight:600;color:var(--text-dim)}.mag .tok dl{display:contents}.mag .tok div{min-width:90px}
.mag .tok dt{font-size:11px;color:var(--text-dim)}.mag .tok dd{margin:0;font-size:20px;font-weight:700;font-variant-numeric:tabular-nums;color:var(--text)}
.mag .tok p{flex-basis:100%;margin:0}.mag .tok.compact dd{font-size:15px}
.mag .msg{white-space:pre-wrap;overflow-wrap:anywhere;max-height:160px;overflow:auto;margin:4px 0}.mag .detail-row td{white-space:normal}
.mag .project-header{position:sticky;top:0;z-index:3;padding:10px 12px;border:1px solid var(--border-soft);border-radius:8px;margin:10px 0;background:var(--bg-elev)}
.mag .project-header details[open]{max-height:160px;overflow:auto}
.mag .project-header .tok{border:0;padding:0;background:none}.mag .project-header .tok dd{font-size:16px}.mag .project-header .tok h3{display:none}
.mag .mobile-run-facts{display:none}
.mag .nav{position:sticky;top:var(--mag-header-height,0px);z-index:2;background:var(--bg-elev);display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--border);padding:4px 0 8px}.mag .nav button{margin:0;padding:6px 10px}.mag .nav button[aria-selected=true]{background:var(--accent-soft);border-color:var(--accent);font-weight:600}
.mag [role=tabpanel]{border:0;padding-top:14px;margin:0}.mag .chat-layout{display:grid;grid-template-columns:minmax(210px,28%) minmax(0,1fr);gap:14px}.mag .chat-list{max-height:65vh;overflow:auto;border:1px solid var(--border-soft);border-radius:8px;padding:6px}.mag .chat-list button{display:block;width:100%;text-align:left;white-space:normal;margin:0 0 5px;overflow-wrap:anywhere;justify-content:start}.mag .chat-list button[aria-current=true]{border-color:var(--accent);background:var(--accent-soft)}
.mag .chat-messages{max-height:65vh;min-height:180px;overflow:auto;padding:4px 10px;border:1px solid var(--border-soft);border-radius:8px;overscroll-behavior:auto}.mag .chat-message{padding:12px 0;border-bottom:1px solid var(--border-soft)}.mag .chat-message h3{font-size:12px;margin:0 0 6px;color:var(--text-dim)}.mag .chat-message p{white-space:pre-wrap}.mag .chat-message pre{max-height:none}.mag .chat-message.error{padding:10px}
.mag .manage-row{padding:8px 0;border-bottom:1px solid var(--border-soft)}.mag .manage-row strong{display:block}.mag .confirm{border:2px solid var(--red);border-radius:8px;padding:14px;margin:12px 0}.mag .plan-preview{max-height:240px;overflow:auto}.mag .allocation{padding:6px 0;color:var(--text-dim)}
@media(max-width:700px){.mag .chat-layout{grid-template-columns:1fr}.mag .chat-list{max-height:200px}.mag .facts{grid-template-columns:1fr}.mag .facts dd{margin-bottom:6px}.mag .nav{gap:6px}.mag .nav button{flex:1 0 28%}}
@media(max-width:600px){.mag .project-header{padding:8px}.mag .project-header .tok,.mag .project-header [data-id=current-chat],.mag .project-header [data-id=run-summary]>p.dim{display:none}.mag .mobile-run-facts{display:block;font-size:12px;margin:4px 0}.mag .project-header .run-actions{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px}.mag .project-header .run-actions>button{margin:0;min-width:0;padding:4px;font-size:11px;line-height:1.2}.mag .project-header .run-actions>label{grid-column:1/-1}.mag .nav button{box-sizing:border-box;flex:1 0 21%;min-width:0;padding:6px;font-size:12px}}
/* Magazine's own appearance is scoped to the plugin, leaving Pi's theme intact. */
.mag[data-appearance=dark]{color-scheme:dark;--text:#edf2fa;--text-dim:#bac8da;--text-faint:#9cafc7;--bg:#0e1726;--bg-elev:#182334;--bg-elev2:#202d42;--bg-elev3:#2a3a52;--border:#718098;--border-soft:#46576f;--accent:#aac7ff;--accent-soft:#263b58;--red:#ce8e9a;--red-text:#ffd3db;--red-soft:#382731;--green:#86d1b4;--notice-err-border:#ce8e9a;--notice-err-bg:#382731;--err-text:#ffd3db;--code-bg:#111c2d;--code-text:#edf2fa;--code-border:#46576f;--code-pad:12px}
.mag[data-appearance=light]{color-scheme:light;--text:#18283d;--text-dim:#4e627c;--text-faint:#61758e;--bg:#eef3fa;--bg-elev:#fff;--bg-elev2:#edf2f9;--bg-elev3:#e3ecf8;--border:#7f91a9;--border-soft:#c4cfde;--accent:#315f9f;--accent-soft:#e2ecfc;--red:#a13c54;--red-text:#8b2540;--red-soft:#fff0f3;--green:#237453;--notice-err-border:#a13c54;--notice-err-bg:#fff0f3;--err-text:#8b2540;--code-bg:#f3f6fb;--code-text:#18283d;--code-border:#c4cfde;--code-pad:12px}
.mag{max-width:1160px;padding:22px 24px;min-height:100%;background:var(--bg);color:var(--text);font:14px/1.55 Arial,Helvetica,sans-serif}
.mag :is(h1,h2,h3,p,strong,label,td,th,dt,dd){color:inherit}.mag[data-appearance=dark],.mag[data-appearance=dark] *,.mag[data-appearance=light],.mag[data-appearance=light] *{text-shadow:none!important}.mag :is(p,small,summary){text-shadow:none}
.mag h1{font-size:23px;line-height:1.3}.mag h2{font-size:17px;margin:0 0 16px}.mag h3{font-size:15px;margin:18px 0 10px}.mag p{margin:8px 0 12px}
.mag .titlebar{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:6px}.mag .appearance{display:flex;align-items:center;gap:10px;margin:0;flex-shrink:0}.mag .appearance select{width:auto;min-width:150px;margin:0}.mag .intro{color:var(--text-dim);margin-bottom:14px}
.mag input,.mag select,.mag textarea{border-radius:7px;background:var(--code-bg);color:var(--text);padding:8px 10px;border-color:var(--border)}.mag select{min-height:38px}.mag input[type=search]{min-height:38px}.mag input:is([type=checkbox],[type=radio]){accent-color:var(--accent);width:16px;height:16px}
.mag button{min-height:36px;padding:8px 13px;border-color:var(--border);background:var(--bg-elev2);color:var(--text);font-weight:500;margin:3px 6px 3px 0}.mag button.primary{background:var(--accent);border-color:var(--accent);color:#11213a;font-weight:600}.mag button.primary:hover:not(:disabled){background:#c3d8ff;color:#11213a}.mag[data-appearance=light] button.primary{color:#fff}.mag[data-appearance=light] button.primary:hover:not(:disabled){background:#244d87;color:#fff}.mag button.stop{background:transparent;border-color:var(--red);color:var(--red-text)}.mag button:disabled{opacity:.45}.mag button.small{min-height:30px;padding:4px 10px}
.mag :is(button,input,select,textarea,summary):focus-visible{outline:2px solid var(--accent);outline-offset:3px}.mag summary{color:var(--accent);padding:6px 0}.mag summary:hover{color:var(--text)}.mag small,.mag .dim{font-size:12.5px;color:var(--text-dim)}
.mag .status{font-size:13px;background:var(--bg-elev);border:1px solid var(--border-soft);border-left:3px solid var(--accent);border-radius:7px;padding:9px 12px;margin-bottom:14px}.mag .status.error{background:var(--notice-err-bg);color:var(--err-text);border-color:var(--notice-err-border)}
.mag .nav{top:var(--mag-header-height,0px);background:var(--bg);gap:6px;padding:8px 0 12px;border-bottom:0;margin:0}.mag .nav button{min-height:36px;border-radius:7px;padding:7px 13px;background:var(--bg-elev);font-weight:500}.mag .nav button[aria-selected=true]{background:var(--accent-soft);border-color:var(--accent);color:var(--text);font-weight:700}
.mag [role=tabpanel]{background:var(--bg-elev);border:1px solid var(--border);border-radius:12px;padding:20px;margin:0 0 18px}.mag .row{gap:14px}.mag .row>label{margin-top:0}.mag .row>.shrink{align-self:end}.mag .history-picker{border:1px solid var(--border-soft);background:var(--bg-elev2);border-radius:9px;padding:14px;margin-bottom:20px}.mag .history-picker h3{margin:0 0 10px}.mag .history-picker label{margin:0}
.mag .project-header{padding:14px 16px;margin:0 0 4px;border:1px solid var(--border);border-radius:11px;background:var(--bg-elev)}.mag .run-overview{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:16px;align-items:start}.mag .run-identity{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.mag .run-identity strong{font-size:16px}.mag .run-caption{margin:0 0 6px;color:var(--text-dim);font-size:12px}.mag .run-overview p{margin:3px 0 6px}.mag .run-overview details{margin:0}.mag .run-overview summary{font-size:12px;padding:3px 0}.mag .run-progress{width:100%;height:6px;display:block;margin:9px 0;accent-color:var(--accent)}
.mag .run-progress{appearance:none;border:1px solid var(--border-soft);border-radius:4px;background:var(--bg-elev2)}.mag .run-progress::-webkit-progress-bar{background:var(--bg-elev2);border-radius:4px}.mag .run-progress::-webkit-progress-value{background:var(--accent);border-radius:4px}.mag .run-progress::-moz-progress-bar{background:var(--accent);border-radius:4px}
.mag .run-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.mag .run-toolbar .run-actions{display:flex;align-items:center;flex-wrap:wrap}.mag .run-toolbar .run-actions button{margin-top:4px;margin-bottom:4px}.mag .run-toolbar>[data-id=current-chat]{margin-right:0}.mag .run-help{font-size:12px;margin:0}.mag .run-help summary{padding:2px 0}.mag .project-header .tok{display:block;min-width:230px;margin:0;padding:0}.mag .project-header .tok dl{display:flex;gap:18px}.mag .project-header .tok div{min-width:65px}.mag .project-header .tok dd{font-size:19px;color:var(--text)}.mag .project-header .tok dt{font-size:11px}.mag .project-header .tok p{max-width:280px;font-size:11px;margin:5px 0 0}.mag .project-header details[open]{max-height:140px;overflow:auto}
.mag .badge{border-radius:14px;font-size:11px;font-weight:600;background:var(--bg-elev2);color:var(--text-dim);padding:2px 9px}.mag .badge.COMPLETED{color:var(--green)}.mag .badge.HALTED,.mag .badge.STOPPED,.mag .badge.STOP_REQUESTED{color:var(--red-text)}
.mag .dash{border-color:var(--border-soft);border-radius:8px;max-height:55vh}.mag table{font-size:12.5px}.mag th,.mag td{padding:10px 9px;border-bottom-color:var(--border-soft)}.mag th{font-weight:600;color:var(--text)}.mag .dash thead th{background:var(--bg-elev)}.mag .dash tbody tr:nth-child(2n){background:var(--bg-elev2)}.mag .dash tr[aria-selected=true]{background:var(--accent-soft)}.mag .sub{font-size:11px;line-height:1.45;margin-top:3px}.mag .linkish{border:1px solid var(--border);border-radius:6px;padding:5px 7px;font-size:12px;min-height:30px;background:var(--bg-elev2);font-weight:600}.mag .tok.compact{margin:12px 0;background:var(--bg-elev2);padding:10px 13px}
.mag .workflow-count{float:right}.mag .library-actions{margin:12px 0}.mag .import-box,.mag .tool-settings,.mag [data-id=plan-box]{border:1px solid var(--border-soft);border-radius:8px;padding:9px 12px;margin:16px 0}.mag .import-box summary,.mag .tool-settings>summary,.mag [data-id=plan-box]>summary{font-weight:600}.mag .allocation{margin:8px 0 16px;padding:10px 12px;border-radius:7px;background:var(--bg-elev2);color:var(--text-dim)}
.mag .cart{border:1px solid var(--border);border-radius:9px;background:var(--bg-elev2);margin:10px 0;padding:10px 12px}.mag .cart summary{display:grid;grid-template-columns:22px minmax(110px,.7fr) minmax(180px,1fr) minmax(0,1.7fr);gap:12px;align-items:center;color:var(--text);list-style:none}.mag .cart-title{font-weight:600;overflow-wrap:anywhere}.mag .cart-title .dim{font-weight:400;display:block;font-size:11px}.mag .cart .disclosure{display:inline-block;margin-right:5px;color:var(--accent)}.mag .cart[open] .disclosure{transform:rotate(90deg)}.mag .cart .cart-model-cell{display:block;margin:0;font-size:11px;color:var(--text-dim)}.mag .cart .cart-model-cell>select{display:block;width:100%;margin-top:4px;font-size:12px}.mag .shared-pick{display:block;font-size:12px;margin-top:5px;overflow-wrap:anywhere}.mag .cart select:not([hidden])+.shared-pick{display:none}.mag .prompt-preview{font-size:12.5px;line-height:1.55;white-space:pre-line;color:var(--text)}.mag .prepare-actions{border-top:1px solid var(--border-soft);padding-top:14px;margin-top:18px}.mag .prepare-actions small{display:block;margin-top:5px}.mag [data-id=tools]{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin:12px 0}.mag [data-id=tools] label{display:block;margin:0;padding:10px;border:1px solid var(--border-soft);border-radius:7px;background:var(--bg-elev2);color:var(--text)}
.mag .facts{gap:6px 14px;margin:12px 0}.mag .attempt{padding:10px 0}.mag .attempt>summary{color:var(--accent);font-weight:600}.mag .manage-row{padding:14px 0}.mag .chat-list{background:var(--code-bg)}.mag .chat-list button{background:var(--bg-elev)}.mag .chat-list button[aria-current=true]{background:var(--accent-soft)}.mag .chat-messages{background:var(--code-bg);padding:8px 16px}.mag .chat-message h3{color:var(--text-dim)}
@media(max-width:800px){.mag{padding:16px}.mag .run-overview{grid-template-columns:1fr}.mag .project-header .tok{display:none}.mag .mobile-run-facts{display:block;font-size:12px;margin:3px 0!important}.mag .cart summary{grid-template-columns:22px minmax(100px,1fr) minmax(160px,1.4fr)}.mag .prompt-preview{grid-column:2/-1}.mag [role=tabpanel]{padding:16px}}
@media(max-width:600px){.mag{padding:12px;font-size:14px}.mag .titlebar{align-items:start;gap:8px}.mag h1{font-size:21px}.mag .appearance{font-size:12px;display:block}.mag .appearance select{min-width:0;width:145px;font-size:12px;min-height:34px}.mag .intro{font-size:12px}.mag [role=tabpanel]{padding:14px}.mag .nav{gap:5px}.mag .nav button{min-height:38px;padding:6px;font-size:12px;flex:1 0 21%}.mag .project-header{padding:10px}.mag .run-identity strong{font-size:14px}.mag .run-overview{gap:2px}.mag .run-toolbar{display:block}.mag .run-toolbar .run-actions{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px}.mag .run-toolbar .run-actions>button{font-size:11px;padding:5px 2px;margin:3px 0;min-height:38px}.mag .run-caption{display:none}.mag .run-help summary{font-size:11px}.mag .history-picker{padding:10px}.mag .cart summary{grid-template-columns:20px minmax(0,1fr);gap:8px}.mag .cart .cart-model-cell,.mag .prompt-preview{grid-column:2}.mag .cart .cart-model-cell>select{font-size:13px}.mag .cart{padding:10px}.mag .facts{grid-template-columns:1fr}.mag .facts dd{margin-bottom:8px}}

`;


const HTML=`<style>${STYLE}</style><main class="mag">
<h1>Magazine</h1><p class="intro">Load your workflow, review its models and tools, then prepare and start explicitly.</p>
<div class="status" role="status" aria-live="polite" data-id="message">Reconnecting to saved projects…</div>
<section aria-labelledby="mag-dash"><h2 id="mag-dash">Magazine dashboard</h2>
 <div class="row"><label>Scheduling<select data-id="scheduler-mode"><option value="sequential">Sequential</option><option value="round_robin">Round Robin</option></select></label>
  <label>Find a project<input type="search" data-id="run-filter" placeholder="Name, ID or status"></label>
  <label>Show<select data-id="status-filter"><option value="all">All projects</option><option value="live">Running or queued</option><option value="READY">Ready</option><option value="HALTED">Halted</option><option value="STOPPED">Stopped</option><option value="COMPLETED">Completed</option></select></label></div>
 <p><small data-id="scheduler-note">One cartridge executes at a time. Sequential finishes a project before the next; Round Robin rotates after each committed cartridge. A mode change applies after the active cartridge and its recovery finish.</small></p>
<div class="tok compact" data-id="dash-tokens" role="group" aria-labelledby="mag-dash-tok-h"><h3 id="mag-dash-tok-h">Reported tokens, all projects</h3><dl><div><dt>Input</dt><dd data-id="dash-tok-input">—</dd></div><div><dt>Output</dt><dd data-id="dash-tok-output">—</dd></div><div><dt>Total</dt><dd data-id="dash-tok-total">—</dd></div></dl><p class="dim" data-id="dash-tok-note"></p></div>
 <div class="dash"><table><caption class="sr-only" hidden>Saved and active Magazine projects</caption><thead><tr><th>Magazine</th><th>Status</th><th>Finished</th><th>Current cartridge</th><th>Model</th><th>Reported tokens</th><th>Queue</th></tr></thead><tbody data-id="dashboard"></tbody></table></div>
 <p class="dim" data-id="dashboard-state"></p></section>
<section><h2>Your workflows <span class="badge workflow-count" data-id="workflow-count"></span></h2>
 <div class="row"><label>Find a workflow<input type="search" data-id="project-filter" placeholder="Filter by name"></label><label>Workflow<select data-id="projects"><option value="">Select a workflow</option></select></label><div class="shrink"><button class="primary" data-id="load-workflow" disabled>Load your workflow</button><button data-id="reload">Refresh list</button></div></div>
 <div class="row"><label>Workflow ZIP<input type="file" accept=".zip" data-id="zip"></label><div class="shrink"><button data-id="import">Import ZIP</button></div></div>
 <details><summary>Use a Magazine JSON document</summary><textarea data-id="json" aria-label="Magazine JSON" spellcheck="false" placeholder="Paste a validated Magazine document"></textarea><button data-id="validate">Validate document</button></details>
 <div data-id="preview-head"></div>
 <div data-id="cart-tools" hidden><button class="small" data-id="expand">Expand all</button><button class="small" data-id="collapse">Collapse all</button><button class="small" data-id="select-all">Select all</button><button class="small" data-id="select-none">Select none</button></div>
 <div data-id="cartridges"></div></section>
<section><h2>2. Models, tools and protection</h2>
 <div class="row"><label>Workflow model<select data-id="shared-model"></select></label><label class="shrink inline"><input type="checkbox" data-id="individual"> Assign models per cartridge</label></div>
 <div class="row" data-id="bulk" hidden><label>Model for selected cartridges<select data-id="bulk-model"></select></label><div class="shrink"><button data-id="bulk-apply">Apply to selected</button></div></div>
 <p><small>Selected tools apply to every cartridge and run with the Pi server's OS permissions; a tool allowlist is not a sandbox, and the open chat's permission preset does not apply to workers. No tools are selected by default.</small></p><div data-id="tools"></div>
 <div class="row"><div><label class="inline"><input type="checkbox" data-id="antiloop" checked> Antiloop (stops confirmed repeated tool loops)</label></div>
  <div data-id="mode-box"><label class="inline"><input type="radio" name="mag-mode" value="result_file_v1" checked> Committed result file (recommended)</label><label class="inline"><input type="radio" name="mag-mode" value="legacy_text"> Final text only (pi.5 compatible)</label></div></div>
 <button class="primary" data-id="prepare" disabled>Prepare workflow</button><small>Preparation saves a READY run. Nothing executes until you choose Start.</small></section>
<section><h2>3. Selected project</h2>
 <div data-id="run-summary"><p class="dim">Select a Magazine above to view its saved progress.</p></div>
 <div class="tok" data-id="token-counter" role="group" aria-labelledby="mag-tok-h" hidden><h3 id="mag-tok-h">Reported tokens</h3><dl><div><dt>Input tokens</dt><dd data-id="tok-input">—</dd></div><div><dt>Output tokens</dt><dd data-id="tok-output">—</dd></div><div><dt>Total tokens</dt><dd data-id="tok-total">—</dd></div></dl><p class="dim" data-id="tok-cache"></p><p class="dim" data-id="tok-note" role="status"></p></div>
 <div><button class="primary" data-id="start" disabled>Start workflow</button><button class="stop" data-id="stop" disabled>Stop workflow</button><button data-id="recover" disabled>Prepare recovery</button><button data-id="progress" disabled>Check progress</button><label class="inline" data-id="run-antiloop-box" hidden><input type="checkbox" data-id="run-antiloop"> Antiloop for this run</label></div>
 <div><button data-id="export" disabled>Export Finished Work</button><button data-id="download" disabled>Download results ZIP</button><button data-id="download-jsonl" disabled>Download Run JSONL</button></div>
 <p><small>STOP is permanent for a run that has started and is not a pause; for a run still waiting for its first turn, Stop removes it from the queue and leaves it READY. A failed run can be recovered to READY and started again within the five-attempt ceiling. After a restart, retained queue entries never resume by themselves: choose Start (after Prepare recovery when the run is HALTED). COMPLETED confirms execution and verified saved results, not real-world correctness.</small></p>
 <div class="row"><h2 class="shrink" style="margin:8px 0 0">Ledger</h2><label class="shrink">Filter<select data-id="ledger-filter"><option value="all">All cartridges</option><option value="done">Done</option><option value="running">Running</option><option value="failed">Failed</option><option value="halted">Halted</option><option value="stopped">Stopped</option><option value="interrupted">Interrupted</option><option value="pending">Pending</option></select></label></div>
 <div data-id="ledger"></div>
 <details data-id="activity-box"><summary>Activity</summary><ol class="feed" data-id="activity"></ol></details>
 <details data-id="markdown-box"><summary>Ledger (Markdown, as exported)</summary><pre data-id="markdown"></pre></details>
 <details data-id="results-box"><summary>Saved results</summary><div data-id="results"></div></details></section>
</main>`;

export default {
  mount(el) {
    el.innerHTML=HTML;
    const q=id=>el.querySelector(`[data-id="${id}"]`);
    let runId='',runs=[],projects=[],preview=null,cat={models:[],tools:[]},closed=false,polling=false,last=null,tick=0;
    let selection=0,dashRequest=0,fullLedger=null,projectLoadRequest=0;
    let panel='overview',ledgerId='',ledgerHandovers={},ledgerRequest=0,planCandidate=null,planApplied=null,planRevision=0;
    let chats=[],chatId=store.get('magazine-pi-chat'),chatOffset=0,chatGeneration=null,chatRequest=0,chatBusy=false,chatListBusy=false,chatMore=false;
    let deleteTarget=null;
    let headerObserver=null;
    const chatListWaiters=[];
    const open=new Set();
    const message=(text,error=false)=>{if(closed)return;q('message').textContent=text;q('message').classList.toggle('error',error);};
    const call=async(action,data={})=>{
      const initialRead=['library','catalogue','runs','view_state'].includes(action);
      for(let attempt=0;;attempt++){
        const response=await fetch(API,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,data})});
        const json=response.headers.get('content-type')?.includes('application/json');
        // A native plugin may be listed just before its routes are activated.
        // Retry only bounded discovery reads; never repeat Prepare/Start/Stop.
        if(initialRead&&attempt<3&&([502,503].includes(response.status)||(response.status===404&&!json))){await new Promise(resolve=>setTimeout(resolve,250*(attempt+1)));continue;}
        if(!json)throw new Error(response.status===401?'Sign in to Pi again, then refresh Magazine.':`Magazine is not ready (HTTP ${response.status}). Use Refresh list; check saved status before repeating a run action.`);
        const value=await response.json();
        if(!value.ok)throw new Error(value.error||'Magazine request failed');
        return value.result;
      }
    };
    const attempt=fn=>async(...a)=>{try{await fn(...a);}catch(e){message(e.message,true);}};
    const button=(id,fn)=>q(id).addEventListener('click',attempt(fn));
    const make=(tag,props={},...kids)=>{const n=document.createElement(tag);for(const [k,v] of Object.entries(props)){if(k==='text')n.textContent=v;else if(k==='cls')n.className=v;else if(k.startsWith('aria-')||k.startsWith('data-'))n.setAttribute(k,v);else n[k]=v;}n.append(...kids);return n;};
    const option=(value,label)=>make('option',{value,text:label});
    const fillModels=(select,first='Choose a model')=>{const keep=select.value;select.replaceChildren(option('',first));for(const m of cat.models)select.append(option(m.id,m.name+' — '+m.provider));select.value=keep;};
    const remember=async(update)=>{if(update.run_id!==undefined)store.set(LAST_RUN,update.run_id);if(update.project_id!==undefined)store.set(LAST_PROJECT,update.project_id);try{await call('view_state',{set:update});}catch{}};
    const two=(a,b)=>make('td',{},document.createTextNode(a),...(b?[make('span',{cls:'sub',text:b})]:[]));

    // The existing controls retain their IDs and runtime behavior. Panels move
    // those controls into one primary task at a time; no host CSS is replaced.
    function layout(){
      const main=el.querySelector('.mag'),sections=[...main.querySelectorAll(':scope > section')];
      const [overview,workflows,setup,selected]=sections;
      const title=main.querySelector('h1'),titlebar=make('div',{cls:'titlebar'});
      const appearance=make('label',{cls:'appearance'},document.createTextNode('Appearance'),make('select',{'data-id':'appearance','aria-label':'Magazine appearance'},option('dark','Magazine dark'),option('light','Magazine light'),option('host','Follow Pi theme')));
      title.before(titlebar);titlebar.append(title,appearance);const theme=store.get('magazine-pi-appearance')||'dark';main.dataset.appearance=['dark','light','host'].includes(theme)?theme:'dark';q('appearance').value=main.dataset.appearance;
      q('appearance').addEventListener('change',()=>{main.dataset.appearance=q('appearance').value;store.set('magazine-pi-appearance',q('appearance').value);});
      const header=make('header',{cls:'project-header','data-id':'run-header','aria-label':'Selected Magazine run',hidden:true});
      const runActions=q('start').parentElement;runActions.classList.add('run-actions');
      header.append(make('p',{cls:'run-caption',text:'Run workflow · selected saved run'}),make('div',{cls:'run-overview'},make('div',{},q('run-summary'),make('p',{cls:'mobile-run-facts','data-id':'mobile-run-facts'})),q('token-counter')));
      const actions=q('export').parentElement,stopHelp=actions.nextElementSibling;
      const evidence=make('details',{cls:'run-help'},make('summary',{text:'Run controls and export help'}),stopHelp);
      const currentChat=make('button',{'data-id':'current-chat',text:'Open current chat',disabled:true});
      currentChat.addEventListener('click',attempt(()=>viewChat()));header.append(make('div',{cls:'run-toolbar'},runActions,currentChat),evidence);
      const nav=make('nav',{cls:'nav',role:'tablist','aria-label':'Magazine sections'});
      const panels={overview,workflows,setup,ledger:make('section'),chats:make('section'),results:make('section'),manage:make('section')};
      for(const [name,p] of Object.entries(panels)){
        p.dataset.panel=name;p.id='mag-panel-'+name;p.role='tabpanel';p.setAttribute('aria-labelledby','mag-nav-'+name);p.hidden=name!=='overview';
        const b=make('button',{id:'mag-nav-'+name,role:'tab',text:{overview:'Overview',workflows:'Workflows',setup:'Set up',ledger:'Ledger',chats:'Chats',results:'Results',manage:'Manage'}[name],
          'data-id':'nav-'+name,'aria-controls':p.id,'aria-selected':String(name==='overview'),tabIndex:name==='overview'?0:-1});
        b.addEventListener('click',attempt(()=>name==='chats'&&runId?viewChat():showPanel(name)));nav.append(b);
      }
      nav.addEventListener('keydown',e=>{const a=[...nav.querySelectorAll('button')],i=a.indexOf(document.activeElement);let n=i;
        if(e.key==='ArrowRight')n=(i+1)%a.length;else if(e.key==='ArrowLeft')n=(i+a.length-1)%a.length;else if(e.key==='Home')n=0;else if(e.key==='End')n=a.length-1;else return;
        e.preventDefault();a[n].click();a[n].focus();});
      setup.querySelector('h2').textContent='Set up';
      const history=make('div',{cls:'history-picker'},make('h3',{text:'Previous work'}));
      const historyRow=make('div',{cls:'row'},make('label',{},document.createTextNode('Saved run'),make('select',{'data-id':'previous-runs'},option('','Choose previous work'))),make('div',{cls:'shrink'},make('button',{'data-id':'view-previous',text:'View previous work',disabled:true}),make('button',{'data-id':'refresh-previous',text:'Refresh list'})));
      history.append(historyRow);overview.insertBefore(history,overview.children[1]);
      const importBox=make('details',{cls:'import-box'},make('summary',{text:'Add a workflow'}));
      const upload=q('zip').closest('.row'),jsonBox=q('json').closest('details');workflows.insertBefore(importBox,upload);importBox.append(upload,jsonBox);
      const manageWorkflows=make('button',{'data-id':'manage-workflows',text:'Manage / delete workflows'});workflows.append(make('div',{cls:'library-actions'},manageWorkflows));
      const modelOverview=make('p',{cls:'allocation','data-id':'allocation',text:'Load a workflow to review model assignments.'});
      const plan=make('details',{'data-id':'plan-box'},make('summary',{text:'GPT/Claude suggestions (optional)'}));
      const planContent=make('div');planContent.innerHTML=`<p>Copy a brief to your chosen planner, then paste its JSON suggestions. Magazine makes no remote AI call. Suggestions affect new preparation only.</p>
        <p class="dim">Review prompts for sensitive information before sharing them. Qwen3-Coder-30B-A3B is non-thinking; model-card claims do not establish local tool-call reliability.</p>
        <label><input type="checkbox" data-id="brief-consent"> I have reviewed the prompts and will remove sensitive information before sharing.</label>
        <button data-id="brief-build">Build planning brief</button><button data-id="brief-copy" disabled>Copy reviewed brief</button>
        <label>Planning brief (review and edit before copying)<textarea data-id="planning-brief"></textarea></label>
        <label>Suggested model assignments (JSON)<textarea data-id="plan-json" spellcheck="false"></textarea></label>
        <button data-id="plan-check">Validate suggestions</button><button data-id="plan-apply" disabled>Apply suggestions</button>
        <p data-id="plan-state" role="status">No suggestions applied.</p><div class="plan-preview" data-id="plan-preview"></div>`;
      plan.append(planContent);setup.insertBefore(q('preview-head'),setup.children[1]);setup.insertBefore(modelOverview,setup.children[2]);setup.insertBefore(plan,q('bulk'));
      const advanced=make('details',{cls:'tool-settings',open:true},make('summary',{text:'Tools and protection'}));
      const toolHelp=q('tools').previousElementSibling;advanced.append(toolHelp,q('tools'),q('antiloop').closest('.row'));setup.insertBefore(advanced,q('prepare'));
      const prepareButton=q('prepare'),prepareHelp=prepareButton.nextElementSibling;
      setup.insertBefore(q('cart-tools'),plan);setup.insertBefore(q('cartridges'),plan);
      const steps=make('details',{'data-id':'workflow-steps',open:true},make('summary',{'data-id':'workflow-steps-title',text:'Workflow steps'}));
      setup.insertBefore(steps,plan);steps.append(q('bulk'),q('cart-tools'),q('cartridges'));
      setup.append(make('div',{cls:'prepare-actions'},prepareButton,prepareHelp));
      const ledger=panels.ledger;ledger.innerHTML=`<h2>Cartridge ledger</h2><div class="row"><label>Cartridge<select data-id="cartridge-select"></select></label><div class="shrink"><button data-id="cartridge-prev">Previous</button><button data-id="cartridge-next">Next</button><button data-id="cartridge-chat">View in Chats</button></div></div>`;
      ledger.append(q('ledger-filter').closest('label'),q('ledger'),q('activity-box'),q('markdown-box'));
      const resultsBox=q('results-box');panels.results.append(make('h2',{text:'Verified results'}),actions,resultsBox);resultsBox.open=true;
      const ch=panels.chats;ch.innerHTML=`<h2>Magazine chats</h2><p class="dim">All retained Magazine worker chats and attempts. This read-only history does not include unrelated ordinary Pi chats and cannot send prompts or change execution.</p>
        <div class="chat-layout"><aside aria-label="Magazine chat history"><label>Search chats<input type="search" data-id="chat-search" placeholder="Run, cartridge, model or status"></label>
        <label>Run<select data-id="chat-run-filter"><option value="">All runs</option></select></label><label>Model<select data-id="chat-model-filter"><option value="">All models</option></select></label><label>Status<select data-id="chat-status-filter"><option value="">All statuses</option><option value="live">Running</option><option value="failed">Failed</option><option value="done">Done</option><option value="interrupted">Interrupted</option><option value="stopped">Stopped</option></select></label>
        <p class="dim" data-id="chat-count"></p><div class="chat-list" data-id="chat-list"></div></aside>
        <div><h3 data-id="chat-heading">Select a chat</h3><p class="dim" data-id="chat-state" role="status"></p><label class="inline"><input type="checkbox" data-id="chat-follow" checked> Auto-follow new messages</label>
        <div class="chat-messages" data-id="chat-messages" role="log" aria-label="Read-only worker conversation" tabindex="0"></div><button data-id="chat-load-more" disabled>Load next messages</button>
        <details><summary>Visible message data (JSON)</summary><pre data-id="chat-raw"></pre></details></div></div>`;
      const manage=panels.manage;manage.innerHTML=`<h2>Manage workflows and saved work</h2><p>Template deletion and saved-run deletion are separate. Deleted Magazine records are recoverable from the trash below. Actual Windows/project files are kept.</p>
        <div class="confirm" data-id="delete-dialog" role="alertdialog" aria-label="Confirm Magazine deletion" hidden><p data-id="delete-summary"></p><label>Type the exact ID to confirm<input data-id="delete-confirmation" autocomplete="off"></label><button data-id="delete-confirm">Confirm deletion</button><button data-id="delete-cancel">Cancel</button></div>
        <h3>Workflow templates</h3><div data-id="manage-templates"></div><h3>Saved work / runs</h3><div data-id="manage-runs"></div><h3>Recoverable Magazine trash</h3><div data-id="manage-trash"></div>`;
      selected.remove();main.append(header,nav,...Object.values(panels));
      if(typeof ResizeObserver!=='undefined'){headerObserver=new ResizeObserver(()=>main.style.setProperty('--mag-header-height',`${header.hidden?0:header.offsetHeight}px`));headerObserver.observe(header);}
    }
    async function showPanel(name){
      if(closed)return;
      panel=name;store.set('magazine-pi-panel',name);
      for(const p of el.querySelectorAll('[data-panel]'))p.hidden=p.dataset.panel!==name;
      for(const b of el.querySelectorAll('[data-id^="nav-"]')){const yes=b.dataset.id==='nav-'+name;b.setAttribute('aria-selected',String(yes));b.tabIndex=yes?0:-1;}
      if(name==='ledger'&&last)await loadLedger();
      if(name==='results'&&runId)await loadResults();
      if(name==='chats')await loadChats(true);
      if(name==='manage')await renderManage();
      // Explicit navigation starts at the panel heading. Polling never calls
      // this path, so reading an older chat or ledger keeps its scroll position.
      for(let parent=el;parent;parent=parent.parentElement){if(['auto','scroll'].includes(getComputedStyle(parent).overflowY)){parent.scrollTop=0;}}
    }
    function allocation(){
      if(!preview){q('allocation').textContent='Load a workflow to review model assignments.';return;}
      const counts=new Map();for(const c of preview.document.cartridges){const s=q('cartridges').querySelector(`select[data-cartridge="${CSS.escape(c.id)}"]`);
        const model=(q('individual').checked&&s?.value)||q('shared-model').value||'Unassigned';if(s?.nextElementSibling){s.nextElementSibling.textContent=model;s.nextElementSibling.title=model;}counts.set(model,(counts.get(model)||0)+1);}
      q('allocation').textContent=[...counts].map(([m,n])=>`${n} × ${m}`).join(' · ');
    }
    async function loadLedger(){
      if(!runId)return;const mine=selection,request=++ledgerRequest,rid=runId;
      const [data,results]=await Promise.all([call('ledger',{run_id:rid}),call('results',{run_id:rid})]);
      if(closed||mine!==selection||request!==ledgerRequest)return;
      fullLedger=data.ledger;ledgerHandovers=results.handovers||{};if(last)renderLedger(last);
    }
    async function viewChat(cid,aid){
      await loadChats(false);if(closed)return;const candidates=chats.filter(c=>c.run_id===runId&&(!cid||c.cartridge_id===cid)&&(!aid||c.attempt_id===aid));
      const chosen=candidates.find(c=>c.live)||candidates[0];
      if(chosen){q('chat-search').value='';q('chat-run-filter').value='';q('chat-model-filter').value='';q('chat-status-filter').value='';}
      await showPanel('chats');if(chosen)await selectChat(chosen.chat_id);else message('No retained session has been created for this attempt yet.');
    }
    function chatList(){
      const term=q('chat-search').value.toLowerCase(),rid=q('chat-run-filter').value,status=q('chat-status-filter').value,model=q('chat-model-filter').value;
      const visible=chats.filter(c=>(!rid||c.run_id===rid)&&(!model||c.model===model)&&(!status||(status==='live'?c.live:c.status===status))&&
        (!term||`${c.run_name} ${c.run_id} ${c.cartridge_id} ${c.model} ${c.status}`.toLowerCase().includes(term)));
      q('chat-list').replaceChildren(...visible.map(c=>{const b=make('button',{'data-chat':c.chat_id,'aria-current':String(c.chat_id===chatId),
        text:`${c.live?'● Running · ':''}${c.run_name} · ${c.cartridge_id} · attempt ${c.attempt_number??'legacy'}\n${c.status} · ${c.model||'model unavailable'}`});
        b.addEventListener('click',attempt(()=>selectChat(c.chat_id)));return b;}));
      q('chat-count').textContent=`${visible.length} of ${chats.length} retained Magazine chats`;
    }
    async function loadChats(choose=false){
      if(closed)return;if(chatListBusy){await new Promise(resolve=>chatListWaiters.push(resolve));if(choose&&!closed)return loadChats(true);return;}chatListBusy=true;
      try{const all=new Map();let offset=0;do{const page=await call('chats',{offset,limit:200});for(const c of page.chats)all.set(c.chat_id,c);
          if(page.next_offset!=null&&page.next_offset<=offset)throw new Error('History pagination did not advance');offset=page.next_offset;}while(offset!=null&&!closed);
        if(closed)return;chats=[...all.values()];const keep=q('chat-run-filter').value;
        q('chat-run-filter').replaceChildren(option('','All runs'),...[...new Map(chats.map(c=>[c.run_id,c.run_name])).entries()].map(([id,name])=>option(id,`${name} · ${id}`)));q('chat-run-filter').value=keep;
        const model=q('chat-model-filter').value;q('chat-model-filter').replaceChildren(option('','All models'),...[...new Set(chats.map(c=>c.model).filter(Boolean))].sort().map(id=>option(id,id)));q('chat-model-filter').value=model;
        chatList();if(choose&&!chats.some(c=>c.chat_id===chatId)){const c=chats.find(c=>c.run_id===runId&&c.live)||chats.find(c=>c.run_id===runId)||chats[0];if(c)await selectChat(c.chat_id);}
        else if(choose&&chatId&&!q('chat-messages').children.length)await selectChat(chatId);
      }finally{chatListBusy=false;for(const resolve of chatListWaiters.splice(0))resolve();}
    }
    async function selectChat(id){
      chatId=id;chatRequest++;chatOffset=0;chatGeneration=null;chatMore=false;store.set('magazine-pi-chat',id);
      q('chat-messages').replaceChildren();q('chat-raw').textContent='';q('chat-follow').checked=true;chatList();await readChat();
    }
    function richText(text){
      const fragment=document.createDocumentFragment(),chunks=String(text).split(/(```[\s\S]*?```)/g);
      for(const chunk of chunks){if(chunk.startsWith('```')){const body=chunk.slice(3,-3).replace(/^[\w+-]*\n/,'');fragment.append(make('pre',{},make('code',{text:body})));}
        else if(chunk)fragment.append(make('p',{text:chunk}));}return fragment;
    }
    async function readChat(){
      if(!chatId||chatBusy||closed)return;chatBusy=true;const mine=chatRequest,id=chatId;
      try{let pages=0;do{const data=await call('chat',{chat_id:id,offset:chatOffset,generation:chatGeneration});if(closed||mine!==chatRequest||id!==chatId)return;
          if(data.reset){q('chat-messages').replaceChildren();q('chat-raw').textContent='';}
          const c=chats.find(c=>c.chat_id===id);q('chat-heading').textContent=c?`${c.run_name} · ${c.cartridge_id} · attempt ${c.attempt_number??'legacy'}`:'Retained Magazine chat';
          for(const m of data.messages){const title=m.role==='assistant'?`Assistant · ${m.model||'model unavailable'}`:m.role==='toolResult'?`Tool · ${m.tool_name||'result'}${m.is_error?' · Error':''}`:'User';
            const article=make('article',{cls:'chat-message'+(m.is_error?' error':''),'data-message':m.id||''},make('h3',{text:title+(m.timestamp?' · '+m.timestamp:'')}));
            for(const b of m.blocks){if(b.type==='text')article.append(richText(b.text));else if(b.type==='tool_call')article.append(make('details',{},make('summary',{text:'Tool request: '+b.name}),make('pre',{text:JSON.stringify(b.arguments,null,2)})));}
            q('chat-messages').append(article);q('chat-raw').textContent+=(q('chat-raw').textContent?'\n':'')+JSON.stringify(m);}
          chatOffset=data.next_offset;chatGeneration=data.generation;chatMore=data.more;
          q('chat-state').textContent=`${data.live?'Live · Updating':'Read-only'} · ${data.state} · Updated ${new Date(data.updated_at*1000).toLocaleTimeString()}${data.more?' · More retained messages available':''}`;
          q('chat-load-more').disabled=!data.more;
          if(q('chat-follow').checked)q('chat-messages').scrollTop=q('chat-messages').scrollHeight;
          pages++;
        }while(chatMore&&pages<5);
      }catch(e){if(mine===chatRequest)q('chat-state').textContent='Chat unavailable: '+e.message;}
      finally{chatBusy=false;if(mine!==chatRequest&&!closed)void readChat();}
    }
    async function renderManage(){
      const row=(name,...buttons)=>make('div',{cls:'manage-row'},make('strong',{text:name}),...buttons);
      const act=(text,fn,props={})=>{const b=make('button',{text,...props});b.addEventListener('click',attempt(fn));return b;};
      q('manage-templates').replaceChildren(...projects.map(p=>row(`${p.name} · ${p.workflow_id}`,
        act('View / Load',async()=>{await loadProject(p.workflow_id);await showPanel('setup');}),
        act('Delete template',()=>confirmDelete('template',p.workflow_id),{'data-delete-template':p.workflow_id}))));
      q('manage-runs').replaceChildren(...runs.map(r=>row(`${r.name||'Workflow'} · ${r.run_id} · ${r.display_status}`,
        act('View',async()=>{await selectRun(r.run_id);await showPanel('overview');}),
        act('Download run JSONL',async()=>{await selectRun(r.run_id);await download('download_jsonl','application/x-ndjson','Run JSONL downloaded.');}),
        act('Delete saved run',()=>confirmDelete('run',r.run_id),{'data-delete-run':r.run_id,disabled:isLive(r),title:isLive(r)?'Active or queued work cannot be deleted.':''}))));
      const trash=await call('trash');if(closed)return;
      q('manage-trash').replaceChildren(...(trash.items.length?trash.items.map(t=>row(`${t.kind} · ${t.id}`,act('Restore',async()=>{const v=await call('restore',{trash_id:t.trash_id});await refresh();await renderManage();message(v.message);}))) : [make('p',{cls:'dim',text:'Magazine trash is empty.'})]));
    }
    async function confirmDelete(kind,id){
      let text;if(kind==='run'){const p=await call('delete_preview',{run_id:id});text=p.message+' Removed: '+p.removed.join(', ')+'. Kept: '+p.kept.join(', ')+'.';}
      else text='Remove this workflow template from the library into recoverable Magazine trash. Saved run snapshots, actual project files, Input ZIPs and exports are kept. Live dependencies block deletion.';
      deleteTarget={kind,id};q('delete-summary').textContent=text+' ID: '+id;q('delete-confirmation').value='';q('delete-confirm').disabled=true;q('delete-dialog').hidden=false;q('delete-confirmation').focus();
    }
    layout();
    for(const id of ['chat-search','chat-run-filter','chat-model-filter','chat-status-filter'])q(id).addEventListener(id==='chat-search'?'input':'change',chatList);
    q('chat-messages').addEventListener('scroll',()=>{const n=q('chat-messages');if(n.scrollHeight-n.scrollTop-n.clientHeight>64)q('chat-follow').checked=false;});
    button('chat-load-more',readChat);
    button('cartridge-chat',()=>viewChat(ledgerId));
    q('cartridge-select').addEventListener('change',attempt(async()=>{ledgerId=q('cartridge-select').value;store.set('magazine-pi-ledger-'+runId,ledgerId);if(last)renderLedger(last);await loadLedger();}));
    for(const [id,delta] of [['cartridge-prev',-1],['cartridge-next',1]])button(id,async()=>{const s=q('cartridge-select'),next=s.selectedIndex+delta;if(next>=0&&next<s.options.length){s.selectedIndex=next;s.dispatchEvent(new Event('change'));}});
    button('brief-build',async()=>{if(!preview)throw new Error('Load a workflow first');if(!q('brief-consent').checked)throw new Error('Review the sensitive-content warning before building or copying the brief');
      const v=await call('planning_brief',{project_id:preview.project_id||'',magazine:JSON.stringify(preview.document)});q('planning-brief').value=v.warning+'\n\n'+v.brief;q('brief-copy').disabled=false;message('Planning brief ready. Review and edit sensitive content before copying.');});
    button('brief-copy',async()=>{if(!q('brief-consent').checked)throw new Error('Review the sensitive-content warning before copying');await navigator.clipboard.writeText(q('planning-brief').value);message('Planning brief copied. Review it before sharing.');});
    q('brief-consent').addEventListener('change',()=>{q('brief-copy').disabled=!q('brief-consent').checked||!q('planning-brief').value;});
    q('plan-json').addEventListener('input',()=>{planCandidate=null;q('plan-apply').disabled=true;q('plan-state').textContent='Suggestions changed. Validate before applying.';});
    button('plan-check',async()=>{planCandidate=null;q('plan-apply').disabled=true;q('plan-state').textContent='No validated suggestions applied. Review any validation error above.';if(!preview)throw new Error('Load a workflow first');const revision=planRevision;
      const v=await call('model_plan',{project_id:preview.project_id||'',magazine:JSON.stringify(preview.document),plan:q('plan-json').value});if(revision!==planRevision)return;
      planCandidate=v.plan;q('plan-preview').replaceChildren(...v.plan.cartridge_ids.map(id=>make('p',{text:`${id}: ${v.plan.cartridge_models[id]}${v.plan.rationale[id]?' — '+v.plan.rationale[id]:''}`})));
      q('plan-state').textContent='All assignments are valid in the current Pi catalogue. Review, then Apply.';q('plan-apply').disabled=false;message('Suggestions validated. Review them before Apply.');});
    button('plan-apply',async()=>{if(!planCandidate||!preview)throw new Error('Validate suggestions first');const revision=planRevision;
      const v=await call('model_plan',{project_id:preview.project_id||'',magazine:JSON.stringify(preview.document),plan:planCandidate});if(revision!==planRevision)return;
      const selects=[...q('cartridges').querySelectorAll('select[data-cartridge]')];if(selects.some(s=>!v.plan.cartridge_models[s.dataset.cartridge]))throw new Error('Workflow changed; reload suggestions');
      const changes=selects.map(s=>`${s.dataset.cartridge}: ${(q('individual').checked&&s.value)||q('shared-model').value||'Unassigned'} → ${v.plan.cartridge_models[s.dataset.cartridge]}${v.plan.rationale[s.dataset.cartridge]?' — '+v.plan.rationale[s.dataset.cartridge]:''}`);
      q('individual').checked=true;q('shared-model').value='';q('bulk').hidden=false;for(const s of selects){s.hidden=false;s.value=v.plan.cartridge_models[s.dataset.cartridge];}
      planApplied=v.plan;q('workflow-steps').open=true;q('plan-state').textContent='Applied to setup only. You may override models manually; Prepare and Start are still separate actions.';q('plan-preview').replaceChildren(...changes.map(text=>make('p',{text})));allocation();message('Suggestions applied to setup. Review manual overrides, then Prepare.');});
    q('delete-confirmation').addEventListener('input',()=>{q('delete-confirm').disabled=!deleteTarget||q('delete-confirmation').value!==deleteTarget.id;});
    button('delete-cancel',()=>{deleteTarget=null;q('delete-dialog').hidden=true;q('delete-confirm').disabled=true;});
    button('delete-confirm',async()=>{if(!deleteTarget)return;const {kind,id}=deleteTarget;if(q('delete-confirmation').value!==id)throw new Error('Type the exact ID to confirm deletion');
      const result=await call(kind==='run'?'delete_run':'delete_template',{...(kind==='run'?{run_id:id}:{project_id:id}),confirmation:id});
      q('delete-dialog').hidden=true;deleteTarget=null;if(kind==='run'&&runId===id)await selectRun('');if(kind==='template'&&preview?.project_id===id){preview=null;planRevision++;q('preview-head').replaceChildren();q('cartridges').replaceChildren();q('prepare').disabled=true;}
      await refresh();await renderManage();message(result.message);});
    q('shared-model').addEventListener('change',allocation);q('individual').addEventListener('change',allocation);q('cartridges').addEventListener('change',allocation);

    // ------------------------------------------------------------ workflow
    function renderProjects(){
      const filter=q('project-filter').value.trim().toLowerCase(),keep=q('projects').value;
      q('projects').replaceChildren(option('','Select a workflow'));
      for(const p of projects){if(filter&&!(p.name+' '+p.workflow_id).toLowerCase().includes(filter))continue;const o=option(p.workflow_id,`${p.name}${p.valid?` · ${p.cartridge_count} cartridges`:' (invalid)'}`);o.disabled=!p.valid;q('projects').append(o);}
      q('projects').value=keep;q('load-workflow').disabled=!q('projects').value;q('workflow-count').textContent=projects.length+' workflow'+(projects.length===1?'':'s');
    }
    function showPreview(value){
      preview=value;const doc=value.document,proto=value.protocol||{};
      planRevision++;planCandidate=null;planApplied=null;q('plan-apply').disabled=true;q('plan-preview').replaceChildren();q('planning-brief').value='';q('brief-copy').disabled=true;q('brief-consent').checked=false;
      q('plan-json').value=value.model_plan?.state==='valid'?JSON.stringify(value.model_plan.plan,null,2):'';
      q('plan-state').textContent=value.model_plan?.state==='invalid'?value.model_plan.error:value.model_plan?.state==='valid'?'Imported suggestions are available. Validate and explicitly Apply to setup.':'No suggestions applied.';
      q('prepare').disabled=!doc;q('cart-tools').hidden=!doc;q('workflow-steps').open=doc.cartridges.length<=8;q('workflow-steps-title').textContent=`Workflow steps — ${doc.cartridges.length} · in order`;
      const declared=proto.declared_mode;
      for(const r of el.querySelectorAll('input[name=mag-mode]')){r.disabled=Boolean(declared);if(declared)r.checked=r.value===proto.handover_mode;}
      const needsFiles=Object.values(proto.cartridges||{}).some(d=>d.handover?.length||d.inputs?.length||d.outputs?.length);
      if(needsFiles){for(const r of el.querySelectorAll('input[name=mag-mode]'))if(r.value==='legacy_text')r.disabled=true;el.querySelector('input[name=mag-mode][value=result_file_v1]').checked=true;}
      if(typeof value.antiloop==='boolean')q('antiloop').checked=value.antiloop;
      const rec=proto.recovery||{};
      q('preview-head').replaceChildren(make('p',{},make('span',{cls:'badge',text:doc.cartridges.length+' cartridges'}),
        document.createTextNode(`${value.name||'Workflow'}${rec.max_attempts?` · up to ${rec.max_attempts} attempts per cartridge, ${rec.attempt_seconds} s each`:''}${declared?` · protocol ${proto.handover_mode}`:''}${proto.ignored_fields?.length?` · ignored: ${proto.ignored_fields.join(', ')}`:''}`)));
      q('cartridges').replaceChildren();
      for(const [i,c] of doc.cartridges.entries()){
        const d=proto.cartridges?.[c.id]||{};
        const box=make('input',{type:'checkbox'});box.dataset.select=c.id;box.addEventListener('click',e=>e.stopPropagation());
        const sel=make('select',{});sel.dataset.cartridge=c.id;fillModels(sel,'Use workflow model');sel.hidden=!q('individual').checked;sel.addEventListener('click',e=>e.stopPropagation());
        const info=[d.handover?.length?`${d.handover.length} value input(s)`:'',d.inputs?.length?`${d.inputs.length} file input(s)`:'',d.outputs?.length?`outputs: ${d.outputs.join(', ')}`:''].filter(Boolean).join(' · ');
        sel.setAttribute('aria-label',`AI model for step ${i+1}, ${c.id}`);
        const title=make('span',{cls:'cart-title'},make('span',{cls:'disclosure',text:'▸'}),document.createTextNode(`${i+1} · ${c.id}`),make('span',{cls:'dim',text:info}));
        const modelCell=make('label',{cls:'cart-model-cell'},document.createTextNode(`AI model for step ${i+1}`),sel,make('span',{cls:'shared-pick',text:'Uses workflow model'}));
        const prompt=c.prompt||'';const sum=make('summary',{},box,title,modelCell,make('span',{cls:'prompt-preview',text:prompt.slice(0,220)+(prompt.length>220?'…':'')}));
        q('cartridges').append(make('details',{cls:'cart'},sum,make('pre',{text:c.prompt})));
      }
      const pick=declaredModels(doc.cartridges,cat.models);
      q('shared-model').value='';q('individual').checked=false;
      for(const s of q('cartridges').querySelectorAll('select[data-cartridge]'))s.hidden=true;
      if(pick.shared){q('shared-model').value=pick.shared;}
      else if(Object.keys(pick.perCartridge).length){q('individual').checked=true;
        for(const s of q('cartridges').querySelectorAll('select[data-cartridge]')){s.hidden=false;if(pick.perCartridge[s.dataset.cartridge])s.value=pick.perCartridge[s.dataset.cartridge];}}
      q('bulk').hidden=!q('individual').checked;allocation();
    }
    async function loadProject(id,announce=true){
      if(!id)return;const request=++projectLoadRequest;const value=await call('preview',{project_id:id});if(closed||request!==projectLoadRequest)return;if(!value.valid)throw new Error(value.error||'Invalid workflow');
      showPreview({...value,project_id:id});q('projects').value=id;q('load-workflow').disabled=false;await remember({project_id:id});
      if(announce)message('Workflow loaded. Review prompts, assign models, then Prepare.');
    }
    // ----------------------------------------------------------- dashboard
    function visible(r){
      const filter=q('run-filter').value.trim().toLowerCase(),status=q('status-filter').value;
      if(status==='live'&&!isLive(r))return r.run_id===runId;
      if(status!=='all'&&status!=='live'&&r.display_status!==status)return r.run_id===runId;
      return !filter||`${r.name||''} ${r.run_id} ${r.display_status}`.toLowerCase().includes(filter)||r.run_id===runId;
    }
    function renderDashboard(scheduler){
      if(scheduler){q('scheduler-mode').value=scheduler.mode;q('scheduler-mode').disabled=Boolean(scheduler.error);
        if(scheduler.error)q('scheduler-note').textContent=scheduler.error;}
      const previous=q('previous-runs').value||runId;q('previous-runs').replaceChildren(option('','Choose previous work'),...runs.map(r=>option(r.run_id,`${r.name||'Workflow'} · ${(r.created_at||'').slice(0,16).replace('T',' ')} · ${STATUS_TEXT[r.display_status]||r.status} · ${r.run_id}`)));q('previous-runs').value=runs.some(r=>r.run_id===previous)?previous:'';q('view-previous').disabled=!q('previous-runs').value;
      const body=q('dashboard');body.replaceChildren();
      for(const r of runs){
        if(!visible(r))continue;
        const status=STATUS_TEXT[r.display_status]||r.status;
        const name=make('button',{cls:'linkish',text:r.name||'Unnamed workflow',title:r.run_id});name.dataset.run=r.run_id;
        name.addEventListener('click',attempt(()=>selectRun(r.run_id)));
        const [tok,state]=tokenCell(r.tokens);
        const row=make('tr',{'aria-selected':String(r.run_id===runId)},
          make('td',{},name,make('span',{cls:'sub',text:`${r.run_id} · ${(r.created_at||'').slice(0,16).replace('T',' ')}`})),
          two(status,r.queue?.recovery_required?'Recovery required':''),
          make('td',{text:`${r.completed??r.done.length} / ${r.total??(r.done.length+r.todo.length)}`}),
          make('td',{text:(r.next_cartridge||'—')+(r.attempt?` · attempt ${r.attempt}`:'')}),
          make('td',{text:shortModel(r.model),title:r.model||''}),
          two(tok,state),
          make('td',{text:['COMPLETED','HALTED','STOPPED'].includes(r.display_status)&&!r.queue?.queued?'—':queueText(r.queue)}));
        body.append(row);
      }
      q('dashboard-state').textContent=`Connected · ${runs.length} project${runs.length===1?'':'s'}`;
      const agg=sumTokens(runs.map(r=>r.tokens)),aggView=tokenCounter(agg,runs.some(r=>r.display_status==='RUNNING')?'RUNNING':'');
      for(const k of ['input','output','total'])q('dash-tok-'+k).textContent=aggView[k];
      q('dash-tok-note').textContent=!runs.length?'No projects yet.':
        `${agg.projects} project${agg.projects===1?'':'s'}${agg.unreported?`, ${agg.unreported} without reported usage`:''}${agg.incomplete?'; some counts incomplete':''}.${aggView.cache?' '+aggView.cache+'.':''}`;
    }
    async function loadRuns(){
      const request=++dashRequest;const value=await call('runs');
      if(request!==dashRequest||closed)return; // a newer refresh owns the view
      runs=value.runs;renderDashboard(value.scheduler);
      if(runId&&!runs.some(r=>r.run_id===runId)){selection++;runId='';q('run-header').hidden=true;q('run-summary').replaceChildren(make('p',{cls:'dim',text:'The selected project is no longer available. Select another project.'}));}
    }
    // ------------------------------------------------------------- ledger
    function attemptBlock(c,a){
      const t=a.tokens||{};const n=k=>t[k]==null?'unavailable':Number(t[k]).toLocaleString();
      const facts=[['Model',a.model],['Kind',a.kind],['Started',a.started||'unavailable'],['Finished',a.finished||'unavailable'],
        ['Session',a.session_id||'none'],['Tokens in / out / total',`${n('input')} / ${n('output')} / ${n('total')} · ${t.available?(t.incomplete?'incomplete':'complete'):'unavailable'}`],
        ['Cache read / write',`${n('cache_read')} / ${n('cache_write')}`],['Timeout',a.timeout_seconds?`${a.timeout_seconds} s`:''],
        ['Files',(a.outputs||[]).map(f=>`${f.path} (${f.bytes} B)`).join(', ')||'none reported'],
        ['Handover',a.handover?`${a.handover.path} · sha256 ${String(a.handover.sha256).slice(0,16)}…`:'none']];
      const dl=make('dl',{cls:'facts'});for(const [k,v] of facts){if(v===''||v==null)continue;dl.append(make('dt',{text:k}),make('dd',{text:String(v)}));}
      const raw=make('details',{},make('summary',{text:'All recorded attempt, handover and recovery fields'}),make('pre',{text:JSON.stringify(a,null,2)}));
      const chat=make('button',{text:'View in Chats','data-attempt-chat':a.attempt_id||''});chat.addEventListener('click',attempt(()=>viewChat(c.id,a.attempt_id)));
      return make('div',{},chat,dl,
        make('p',{cls:'msg',text:[a.code,a.note].filter(Boolean).join(' — ')}),make('p',{cls:'msg',text:a.retry_decision?.reason||''}),raw);
    }
    function renderLedger(r){
      const rows=r.ledger?.cartridges||r.order.map(id=>({id,status:r.done.includes(id)?'done':'pending',attempts:0,tokens:null}));
      ledgerId=ledgerChoice(rows,ledgerId||store.get('magazine-pi-ledger-'+r.run_id),r.current_cartridge);
      const filter=q('ledger-filter').value,options=rows.map((c,i)=>({c,i})).filter(({c})=>filter==='all'||c.status===filter||c.id===ledgerId);
      q('cartridge-select').replaceChildren(...options.map(({c,i})=>option(c.id,`${i+1} · ${c.id} · ${c.status}`)));q('cartridge-select').value=ledgerId;
      const index=q('cartridge-select').selectedIndex;q('cartridge-prev').disabled=index<=0;q('cartridge-next').disabled=index<0||index>=options.length-1;
      const c=rows.find(c=>c.id===ledgerId);if(!c){q('ledger').replaceChildren(make('p',{text:'No cartridges recorded.'}));return;}
      const rec=fullLedger?.cartridges?.[c.id],attempts=rec?.attempts||[],t=c.tokens||{};
      const n=k=>t[k]==null?'unavailable':Number(t[k]).toLocaleString(),times=attempts.flatMap(a=>[a.started,a.finished]).filter(Boolean).sort();
      const facts=[['Status',c.status],['Exact model',c.model||r.cartridge_models?.[c.id]||'unavailable'],['Attempts',`${c.attempts??attempts.length} / 5`],
        ['Tokens input / output / total',`${n('input')} / ${n('output')} / ${n('total')}`],['Cache read / write',`${n('cache_read')} / ${n('cache_write')}`],
        ['Usage completeness',t.attempts?t.available?(t.incomplete?'Incomplete: some usage is unavailable or the attempt is active.':'Complete reported usage'):'Unavailable: no usage reported.':'No attempts yet.'],
        ['Started',attempts[0]?.started||'Not started'],['Finished',attempts.length?attempts[attempts.length-1].finished||'Still active / not recorded':'Not started'],
        ['Recorded duration',duration(c.elapsed_seconds,c.active)],['Warnings',String(c.warnings||0)],['Last failure / decision',[c.last_code,c.last_decision].filter(Boolean).join(' · ')||'none']];
      const dl=make('dl',{cls:'facts'});for(const [key,value] of facts)dl.append(make('dt',{text:key}),make('dd',{text:value}));
      const output=make('div',{'data-ledger-cartridge':c.id},make('h3',{text:`${r.order.indexOf(c.id)+1} · ${c.id}`}),dl);
      for(const a of attempts){const key=`${r.run_id}:${c.id}:${a.attempt_id}`,detail=make('details',{cls:'attempt',open:open.has(key)},make('summary',{text:`Attempt ${a.attempt_number} · ${a.status}`}));
        detail.addEventListener('toggle',()=>{if(detail.isConnected){detail.open?open.add(key):open.delete(key);}});detail.append(attemptBlock(c,a));output.append(detail);}
      if(!attempts.length)output.append(make('p',{cls:'dim',text:c.attempts?'Loading retained attempt details…':'No attempts recorded for this cartridge.'}));
      const hv=ledgerHandovers[c.id];if(hv)output.append(make('details',{},make('summary',{text:hv.error?'Committed handover unavailable':'Verified committed handover'}),make('pre',{text:hv.error||JSON.stringify(hv.payload,null,2)})));
      output.append(make('details',{},make('summary',{text:'Full cartridge record (JSON)'}),make('pre',{text:JSON.stringify(rec||c,null,2)})));
      q('ledger').replaceChildren(output);q('cartridge-chat').disabled=!attempts.length;
    }
    // ------------------------------------------------------------ the run
    function renderRun(r){
      last=r;q('run-header').hidden=false;const status=r.display_status||r.status;const qd=r.queue||{};
      const total=r.order.length,done=r.done.length;
      const head=make('div',{cls:'run-identity'},make('strong',{text:r.name||'Workflow'}),make('span',{cls:'badge '+status,text:STATUS_TEXT[status]||status}));
      const proto=r.protocol||{};
      const lines=[`Run reference: ${r.run_id} · Prepared: ${r.created_at||'unavailable'}`,tokenLine(r.tokens)+(r.tokens?.source==='retained_sessions_without_ledger'?' (legacy run: derived from retained sessions; per-cartridge association not recorded)':''),
        `Protocol: ${proto.legacy?'pi.5 legacy (single attempt per Start)':proto.handover_mode} · Antiloop: ${proto.antiloop?'On':'Off'}${proto.antiloop_frozen?' (frozen)':''} · Tools: ${r.tool_ids?.join(', ')||'none'}`,
        'Models: '+Object.entries(r.cartridge_models||{}).map(([id,m])=>`${id}: ${m}`).join('; ')];
      if(qd.queued)lines.push(qd.recovery_required?`Retained queue position #${qd.position} from before a restart: choose Start${r.status==='HALTED'?' after Prepare recovery':''} to resume it.`:`Queued at position #${qd.position}${qd.next?' (next)':''}; nothing executes until it receives its turn.`);
      if(qd.note)lines.push(qd.note);
      if(r.control)lines.push(`STOP: ${r.control.termination_status||r.control.error} · cancellation ${r.control.cancellation_status||'unknown'}`);
      if(r.execution?.halt_reason)lines.push('Halt reason: '+r.execution.halt_reason);
      if(r.export)lines.push('Finished Work: '+r.export.status+' · '+(r.export.message||r.export.directory||''));
      const elapsed=(r.ledger?.cartridges||[]).reduce((n,c)=>n+(c.elapsed_seconds||0),0);
      q('mobile-run-facts').textContent=`Tokens ${r.tokens?.total==null?'—':Number(r.tokens.total).toLocaleString()}${r.tokens?.incomplete?' · partial':''} · Duration ${duration(elapsed,Boolean(r.active))}`;
      const detail=make('details',{},make('summary',{text:'Run details, models and execution evidence'}),...lines.map(t=>make('p',{cls:'dim',text:t})));
      q('run-summary').replaceChildren(head,make('p',{text:`${done} of ${total} steps finished${r.current_cartridge?` · current ${r.current_cartridge}`:''} · ${duration(elapsed,Boolean(r.active))}`}),make('progress',{cls:'run-progress',max:Math.max(total,1),value:done,'aria-label':'Verified workflow progress'}),detail);
      const tv=tokenCounter(r.tokens,status);q('token-counter').hidden=false;
      for(const k of ['input','output','total'])q('tok-'+k).textContent=tv[k];
      q('tok-cache').textContent=tv.cache;q('tok-cache').hidden=!tv.cache;
      q('tok-note').textContent=tv.note+(r.tokens?.source==='retained_sessions_without_ledger'?' Legacy run: derived from retained sessions.':'');
      const stopped=status==='STOPPED'||Boolean(r.control);
      const waitingFirst=qd.queued&&(!qd.entered_core||qd.recovery_required)&&['READY','HALTED'].includes(r.status);
      q('start').disabled=r.status!=='READY'||stopped||(qd.queued&&!qd.recovery_required)||Boolean(r.execution&&!r.execution.halted&&!r.execution.owner_alive);
      q('stop').disabled=!(waitingFirst||(['READY','RUNNING'].includes(r.status)&&r.execution&&r.control?.termination_status!=='CONFIRMED'));
      q('stop').textContent=waitingFirst?'Remove from queue':'Stop workflow';
      q('recover').disabled=stopped||!(r.status==='HALTED'||(r.status==='READY'&&r.execution&&!r.execution.owner_alive));
      q('recover').title=stopped?'A STOPPED run is not resumable; prepare a new run instead.':'';
      q('progress').disabled=false;q('export').disabled=!['COMPLETED','HALTED'].includes(r.status);q('download').disabled=q('export').disabled;q('download-jsonl').disabled=false;
      q('current-chat').disabled=false;
      const canToggle=r.status==='READY'&&!proto.legacy&&!proto.antiloop_frozen;q('run-antiloop-box').hidden=!canToggle;q('run-antiloop').checked=Boolean(proto.antiloop);
      renderLedger(r);
      q('activity').replaceChildren(...((r.ledger?.activity)||[]).slice(-15).reverse().map(a=>make('li',{cls:a.level,text:`${(a.at||'').slice(11,19)} ${a.text}`})));
    }
    async function progress(announce=true){
      if(!runId||closed||polling)return;polling=true;const mine=selection,requested=runId;
      try{
        const r=await call('status',{run_id:requested});if(closed||mine!==selection)return; // late response for an older selection
        renderRun(r);
        if(panel==='ledger')await loadLedger();
        if(panel==='results')await loadResults(mine);
        if(q('markdown-box').open)await loadMarkdown(mine);
        if(announce)message('Status refreshed: '+(STATUS_TEXT[r.display_status]||r.status));
      }catch(e){if(announce)throw e;message(e.message,true);}finally{polling=false;}
    }
    async function loadResults(mine=selection){const out=await call('results',{run_id:runId});if(mine!==selection)return;
      const expanded=new Map([...q('results').children].map(d=>[d.dataset.cartridge,{step:d.open,json:d.querySelector('details')?.open}]));
      q('results').replaceChildren(...out.done.map(id=>{const hv=(out.handovers||{})[id],kids=[make('summary',{text:id})];
        if(hv){const text=handoverText(hv.payload);
          if(text)kids.push(make('p',{cls:'dim',text:'Result (from the committed handover):'}),make('pre',{text}));
          kids.push(make('details',{open:Boolean(expanded.get(id)?.json)},make('summary',{text:hv.error?'Committed handover unavailable':'Committed handover (JSON)'}),make('pre',{text:hv.error||JSON.stringify(hv.payload,null,2)})));
          kids.push(make('p',{cls:'dim',text:'Final reply:'}));}
        kids.push(make('pre',{text:out.results[id]??'Canonical result unavailable'}));return make('details',{'data-cartridge':id,open:Boolean(expanded.get(id)?.step)},...kids);}));}
    async function loadMarkdown(mine=selection){const value=await call('ledger',{run_id:runId});if(mine!==selection)return;q('markdown').textContent=value.markdown||('No attempt ledger: this run was prepared before attempt recording. '+tokenLine(value.tokens));}
    async function selectRun(id,persist=true){
      selection++;ledgerRequest++;runId=id;fullLedger=null;ledgerHandovers={};ledgerId=store.get('magazine-pi-ledger-'+id);last=null;renderDashboard();q('current-chat').disabled=!id;q('run-header').hidden=!id;
      if(persist)await remember({run_id:id||null});
      if(!id){q('token-counter').hidden=true;q('run-summary').replaceChildren(make('p',{cls:'dim',text:runs.length?'Select a Magazine above to view its saved progress.':'Prepare a workflow to begin.'}));q('ledger').replaceChildren();for(const b of ['start','stop','recover','progress','export','download','download-jsonl'])q(b).disabled=true;return;}
      await progress(false);
    }
    async function refresh(){
      const [lib,models]=await Promise.all([call('library'),call('catalogue')]);if(closed)return;
      cat=models;projects=lib.projects.workflows;renderProjects();fillModels(q('shared-model'));fillModels(q('bulk-model'));
      for(const s of q('cartridges').querySelectorAll('select'))fillModels(s,'Use workflow model');
      const chosen=new Set([...q('tools').querySelectorAll('input:checked')].map(i=>i.value));q('tools').replaceChildren();
      for(const tool of cat.tools){const input=make('input',{type:'checkbox',value:tool.id,checked:chosen.has(tool.id)});q('tools').append(make('label',{cls:'inline',title:tool.description||''},input,document.createTextNode(' '+tool.name)));}
      await loadRuns();allocation();message(lib.projects.message||'Magazine ready.');
    }
    // ------------------------------------------------------------- events
    q('project-filter').addEventListener('input',renderProjects);
    for(const id of ['run-filter','status-filter'])q(id).addEventListener('input',()=>renderDashboard());
    q('status-filter').addEventListener('change',()=>renderDashboard());
    q('ledger-filter').addEventListener('change',()=>{if(last)renderLedger(last);});
    q('scheduler-mode').addEventListener('change',attempt(async()=>{const mode=q('scheduler-mode').value;q('scheduler-mode').disabled=true;
      try{await call('set_scheduler',{mode});message(`Scheduling set to ${mode==='round_robin'?'Round Robin':'Sequential'}; it applies at the next committed cartridge boundary.`);}
      finally{q('scheduler-mode').disabled=false;await loadRuns();}}));
    q('projects').addEventListener('change',()=>{projectLoadRequest++;preview=null;planCandidate=null;planApplied=null;planRevision++;q('prepare').disabled=true;q('load-workflow').disabled=!q('projects').value;q('preview-head').replaceChildren();q('cartridges').replaceChildren();q('cart-tools').hidden=true;q('plan-apply').disabled=true;allocation();message('Choose Load your workflow to review the selected workflow before preparing it.');});
    button('load-workflow',async()=>{const id=q('projects').value;if(!id)throw new Error('Choose a workflow first');await loadProject(id);if(preview?.project_id===id)await showPanel('setup');});
    button('manage-workflows',()=>showPanel('manage'));
    q('previous-runs').addEventListener('change',()=>{q('view-previous').disabled=!q('previous-runs').value;});
    button('view-previous',async()=>{const id=q('previous-runs').value;if(!id)throw new Error('Choose previous work first');await selectRun(id);await showPanel('overview');message('Previous work loaded. Nothing was started.');});
    button('refresh-previous',loadRuns);
    q('individual').addEventListener('change',()=>{for(const s of q('cartridges').querySelectorAll('select'))s.hidden=!q('individual').checked;q('bulk').hidden=!q('individual').checked;if(q('individual').checked)q('workflow-steps').open=true;});
    q('results-box').addEventListener('toggle',attempt(async()=>{if(q('results-box').open&&runId)await loadResults();}));
    q('markdown-box').addEventListener('toggle',attempt(async()=>{if(q('markdown-box').open&&runId)await loadMarkdown();}));
    q('run-antiloop').addEventListener('change',attempt(async()=>{await call('set_antiloop',{run_id:runId,enabled:q('run-antiloop').checked});await progress(false);message('Antiloop setting saved for this READY run.');}));
    q('antiloop').addEventListener('change',attempt(async()=>{if(preview?.project_id)await call('set_antiloop',{project_id:preview.project_id,enabled:q('antiloop').checked});}));
    button('expand',()=>{for(const d of q('cartridges').querySelectorAll('details'))d.open=true;});
    button('collapse',()=>{for(const d of q('cartridges').querySelectorAll('details'))d.open=false;});
    button('select-all',()=>{for(const b of q('cartridges').querySelectorAll('input[data-select]'))b.checked=true;});
    button('select-none',()=>{for(const b of q('cartridges').querySelectorAll('input[data-select]'))b.checked=false;});
    button('bulk-apply',()=>{const model=q('bulk-model').value;const chosen=[...q('cartridges').querySelectorAll('input[data-select]:checked')].map(b=>b.dataset.select);if(!chosen.length)throw new Error('Select cartridges first');
      for(const id of chosen)q('cartridges').querySelector(`select[data-cartridge="${CSS.escape(id)}"]`).value=model;message(`Model ${model||'(workflow model)'} assigned to ${chosen.length} cartridge(s).`);allocation();});
    button('reload',refresh);
    button('validate',async()=>{const value=await call('validate',{magazine:q('json').value});showPreview(value);q('projects').value='';await remember({project_id:null});message('Document validated.');await showPanel('setup');});
    button('import',async()=>{
      const file=q('zip').files[0];if(!file)throw new Error('Select a workflow ZIP');if(file.size>8*1024*1024)throw new Error('ZIP exceeds 8 MiB');
      const response=await fetch('/plugins-api/magazine-pi/upload',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-Magazine-File-Name':encodeURIComponent(file.name)},body:file});
      const upload=await response.json();if(!upload.ok)throw new Error(upload.error||'ZIP import failed');await refresh();await loadProject(upload.result.project_id,false);message(upload.result.message);await showPanel('setup');
    });
    button('prepare',async()=>{
      if(!preview)throw new Error('Load and validate a workflow');const cartridge_models={};
      if(q('individual').checked)for(const s of q('cartridges').querySelectorAll('select[data-cartridge]'))if(s.value)cartridge_models[s.dataset.cartridge]=s.value;
      const default_model=q('shared-model').value;
      if(!default_model&&preview.document.cartridges.some(c=>!cartridge_models[c.id]))throw new Error('Choose a workflow model or a model for every cartridge');
      const tool_ids=[...q('tools').querySelectorAll('input:checked')].map(i=>i.value);
      const mode=el.querySelector('input[name=mag-mode]:checked')?.value;
      const data={magazine:JSON.stringify(preview.document),project_id:preview.project_id||'',...(default_model?{default_model}:{}),cartridge_models,tool_ids,antiloop:q('antiloop').checked,...(planApplied?{model_plan:planApplied}:{})};
      if(!preview.protocol?.declared_mode&&mode)data.handover_mode=mode;
      const run=await call('prepare',data);await loadRuns();await selectRun(run.run_id);message('Workflow READY. Review the prepared run, then choose Start workflow.');await showPanel('overview');
    });
    for(const action of ['start','stop','recover','export'])button(action,async()=>{const result=await call(action,{run_id:runId});await loadRuns();await progress(false);
      message(result.message||({start:result.status==='queued'?`Queued at position #${result.queue_position}.`:'Workflow started.',stop:'Stop request processed.',recover:'Recovery prepared. Choose Start to execute.',export:'Finished Work export checked.'}[action]));});
    button('progress',async()=>{await progress();await loadRuns();});
    const download=async(action,type,text)=>{const file=await call(action,{run_id:runId});const bytes=Uint8Array.from(atob(file.file_data_b64),c=>c.charCodeAt(0));const url=URL.createObjectURL(new Blob([bytes],{type}));const link=make('a',{href:url,download:file.file_name});link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message(text);};
    button('download',()=>download('download','application/zip','Results ZIP downloaded (includes the attempt ledger).'));
    button('download-jsonl',()=>download('download_jsonl','application/x-ndjson;charset=utf-8','Run JSONL downloaded. Retained records only; unrecorded history is not reconstructed.'));
    // GETs only while open: polling never starts, stops or reorders work.
    const timer=setInterval(()=>{tick++;if(closed||document.hidden)return;
      if(tick%2===0)void loadRuns().catch(e=>{q('dashboard-state').textContent='Reconnection failed: '+e.message+' · retrying';});
      if(panel==='chats'){if(tick%3===0)void loadChats(false).catch(e=>{q('chat-state').textContent=e.message;});void readChat();}
      if(!runId||polling)return;if(isLive(last)||tick%5===0)void progress(false);},1500);
    void attempt(async()=>{
      await refresh();
      let saved={};try{saved=await call('view_state');}catch{}
      const project=saved.project_id||store.get(LAST_PROJECT);
      if(project&&projects.some(p=>p.workflow_id===project&&p.valid))await loadProject(project,false);
      const remembered=saved.run_exists===false?'':(saved.run_id||store.get(LAST_RUN));
      const chosen=chooseRun(runs,remembered);
      await selectRun(chosen,chosen!==(saved.run_id||''));
      const rememberedPanel=store.get('magazine-pi-panel');await showPanel(['overview','workflows','setup','ledger','chats','results','manage'].includes(rememberedPanel)?rememberedPanel:'overview');
      if(chosen)message('Restored '+(runs.find(r=>r.run_id===chosen)?.name||'run')+'. Nothing was started.');
      else message(runs.length?'Select a Magazine in the dashboard to view its saved progress.':'Magazine ready.');
    })();
    return ()=>{closed=true;clearInterval(timer);headerObserver?.disconnect();el.replaceChildren();};
  }
};
