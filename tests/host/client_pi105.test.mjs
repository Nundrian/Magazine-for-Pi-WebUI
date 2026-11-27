import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const dir = process.env.JSDOM_DIR;
const doc = {schema_version:'1.0', cartridges:[
  {id:'a', job_type:'work', prompt:'First'}, {id:'b', job_type:'work', prompt:'Second'},
]};
const plain = {valid:true, name:'Fixture', document:doc, protocol:{recovery:{max_attempts:2}}};
const model = {id:'ollama/fixture', provider:'ollama', thinking_levels:['off','low','high'], inherited_thinking_level:'low'};
const plan = {schema_version:'1.1', workflow_name:'Fixture', cartridge_ids:['a','b'],
  cartridge_models:{a:model.id,b:model.id}, default_thinking_level:'high',
  cartridge_thinking_levels:{b:'inherit'}, rationale:{}};
const settle = () => new Promise(resolve => setImmediate(resolve));

test('late validates and applies cannot replace current setup', {skip:!dir}, async () => {
  const {JSDOM}=await import(pathToFileURL(path.join(dir,'lib/api.js')));
  const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true});
  for(const key of ['window','document','localStorage','getComputedStyle','Event','HTMLElement'])
    globalThis[key]=key==='window'?dom.window:dom.window[key];
  globalThis.CSS={escape:value=>value};
  const pending=[];
  const rpc={
    catalogue:{models:[model],tools:[]},library:{projects:{workflows:[]}},
    view_state:{},runs:{runs:[],scheduler:{mode:'sequential'}},validate:plain,
    scheduler:{mode:'sequential'},
  };
  globalThis.fetch=async(_url,init)=>{
    const {action}=JSON.parse(init.body);
    const result=action==='model_plan'?await new Promise(resolve=>pending.push(resolve)):rpc[action];
    return new Response(JSON.stringify({ok:true,result}),{headers:{'content-type':'application/json'}});
  };
  const {default:magazine}=await import('../../magazine-pi/client/entry.mjs');
  const unmount=magazine.mount(document.getElementById('root'));
  const q=id=>document.querySelector('[data-id="'+id+'"]');
  const type=value=>{q('plan-json').value=value;q('plan-json').dispatchEvent(new Event('input'));};
  try{
    await settle();
    q('validate').click();
    await settle();
    assert.match(q('shared-model').textContent,/fixture/);
    assert.doesNotMatch(q('shared-model').textContent,/undefined/);
    type('first');q('plan-check').click();
    type('second');q('plan-check').click();
    pending[1]({plan});await settle();
    assert.equal(q('plan-apply').disabled,false);
    pending[0]({plan:{...plan,cartridge_models:{a:'stale',b:'stale'}}});await settle();
    assert.doesNotMatch(q('plan-preview').textContent,/stale/);
    q('plan-apply').click();
    type('third');
    pending[2]({plan});await settle();
    assert.equal(q('plan-apply').disabled,true);
    assert.equal(q('workflow-thinking').value,'inherit');
    q('plan-check').click();pending[3]({plan});await settle();
    q('plan-apply').click();pending[4]({plan});await settle();
    assert.equal(q('workflow-thinking').value,'high');
    assert.equal(document.querySelector('[data-thinking="b"]').value,'inherit');
    assert.equal(document.querySelector('[data-thinking="a"]').value,'');
    const legacy=document.querySelector('[name=mag-mode][value=legacy_text]');
    legacy.click();
    assert.equal(legacy.checked,true);
    q('validate').click();await settle();
    assert.equal(legacy.checked,true);
    rpc.validate={...plain,name:'Required',protocol:{declared_mode:true,handover_mode:'result_file_v1'}};
    q('validate').click();await settle();
    assert.equal(legacy.disabled,true);
    assert.match(q('mode-note').textContent,/required/);
  }finally{unmount();dom.window.close();}
});

