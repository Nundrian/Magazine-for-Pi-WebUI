"""Independent browser journey on an isolated Pi host with scripted events.

No local LLM is called. The only provider is ScriptedProvider on loopback.
The default browser is Firefox; never point this script at an existing host.
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'tests'), str(ROOT/'magazine-pi')]
from real_host_smoke import Host, install, probe, rpc
from real_host_ui import open_magazine
from pi6_fakes import ScriptedProvider, agent_dir, project_zip
import ledger as lg


def wait_status(port, rid, expected, timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        r=rpc(port,'status',{'run_id':rid})['result']
        if r['display_status']==expected: return r
        time.sleep(.05)
    raise AssertionError(f'{rid} did not reach {expected}: {r}')


def prepare(port, ids, prompts, name):
    manifest='name: '+name+'\ncartridges:\n'+''.join(f'  - {{id: {cid}, model: fixture/model-a, prompt_file: {cid}.md}}\n' for cid in ids)
    pid=rpc(port,'import',{'file_name':name.replace(' ','-')+'.zip','file_data_b64':project_zip(manifest,{cid+'.md':p for cid,p in zip(ids,prompts)})})['result']['project_id']
    preview=rpc(port,'preview',{'project_id':pid})['result']
    rid=rpc(port,'prepare',{'project_id':pid,'magazine':json.dumps(preview['document']),
                           'default_model':'fixture/model-a','tool_ids':[], 'handover_mode':'result_file_v1'})['result']['run_id']
    return pid,rid


def synthetic_attempt(base, ledger, cid, number, status):
    aid,sid=uuid.uuid4().hex,str(uuid.uuid4())
    session=base/'sessions'/f'{sid}.jsonl';session.parent.mkdir(exist_ok=True)
    rows=[{'type':'session','id':sid},
          {'type':'message','id':aid+'u','message':{'role':'user','content':[{'type':'text','text':f'Historical {cid} attempt {number} task'}]}},
          {'type':'message','id':aid+'a','message':{'role':'assistant','provider':'fixture','model':'model-a','content':[
              {'type':'thinking','thinking':'PRIVATE_THOUGHT'}, {'type':'text','text':f'Historical {cid} attempt {number} visible answer'}]}},
          {'type':'message','id':aid+'t','message':{'role':'toolResult','toolName':'bash','isError':status=='failed',
              'content':[{'type':'text','text':f'Fixture exit {1 if status=="failed" else 0}'}]}}]
    session.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    ledger['cartridges'][cid]['attempts'].append({'attempt_id':aid,'attempt_number':number,'kind':'work','status':status,
        'model':'fixture/model-a','started':f'2026-01-01T00:{number:02}:00+00:00','finished':f'2026-01-01T00:{number:02}:01+00:00',
        'session_id':sid,'session_file':session.relative_to(base).as_posix(), 'code':'FIXTURE_FAILURE' if status=='failed' else None,
        'retry_decision':{'decision':'stop' if number==5 else 'retry','reason':'Retained fixture attempt'},
        'tokens':{'available':True,'input':100,'output':10,'total':110,'cache_read':0,'cache_write':0,'state':'known','incomplete':False}})
    ledger['cartridges'][cid]['status']=status
    return aid


def main():
    p=argparse.ArgumentParser();p.add_argument('--host-root',required=True);p.add_argument('--port',type=int,default=18965)
    p.add_argument('--out',default=str(ROOT/'evidence/pi8-browser'));p.add_argument('--browser',choices=['firefox','chromium'],default='firefox')
    p.add_argument('--plugin',default=str(ROOT/'magazine-pi'));p.add_argument('--python',default=sys.executable)
    p.add_argument('--baseline',action='store_true',help='Capture the accepted rc.3 layout using isolated fixture data only')
    args=p.parse_args();out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='pi8-independent-ui-'));provider=ScriptedProvider();provider.usage={'prompt_tokens':100,'completion_tokens':7,'total_tokens':107}
    agent=agent_dir(tmp/'agent',provider.url);cwd=tmp/'workspace';cwd.mkdir();data=tmp/'data'
    install(Path(args.plugin),data,{'workspace':str(cwd/'magazine'),'agentDir':str(agent),'python':args.python,'allowedTools':['read']})
    log=(out/'isolated-host.log').open('wb');host=Host(args.host_root,data,agent,cwd,args.port,log)
    checks={};errors=[];network_console=[];http_errors=[];browser=None
    try:
        host.start();probe(args.host_root,args.port)
        ids=[f'c{i:03}' for i in range(1,146)]
        pid,longrun=prepare(args.port,ids,[f'SCRIPT ok\nLong fixture {cid}' for cid in ids],'Long fixture')
        base=data/'magazine/runs/local'/longrun
        ledger=lg.new(longrun,ids,'result_file_v1')
        aids=[synthetic_attempt(base,ledger,ids[0],n,'failed') for n in range(1,6)]
        synthetic_attempt(base,ledger,ids[-1],1,'done');lg.save(base,ledger)
        (base/'run/state.json').write_text(json.dumps({'done':[],'current':ids[0],'todo':ids[1:],'status':'HALTED','revision':1}))
        oldpid,oldrun=prepare(args.port,['old'],['SCRIPT ok\nOld project retained task'],'Old project')
        rpc(args.port,'start',{'run_id':oldrun});wait_status(args.port,oldrun,'COMPLETED')
        livepid,liverun=prepare(args.port,['live','next'],['SCRIPT gate:LIVE\nFirst live visible task','SCRIPT gate:NEXT\nSecond live visible task'],'Live project')
        gate=provider.gate('LIVE');nextgate=provider.gate('NEXT')
        rpc(args.port,'start',{'run_id':liverun});assert provider.wait_reached('LIVE')
        sparepid,sparerun=prepare(args.port,['spare'],['SCRIPT ok\nNever started spare'],'Spare project')
        with sync_playwright() as pw:
            browser=getattr(pw,args.browser).launch(headless=True)
            # Host PWA asset caching is outside the plugin under test. In a
            # fresh ephemeral host, block SW interception so reload checks test
            # the plugin rather than a host precache race on lazy chunks.
            context=browser.new_context(viewport={'width':1366,'height':1000},service_workers='block');page=context.new_page()
            page.on('pageerror',lambda e:errors.append(str(e)))
            def response_error(response):
                if response.status < 400: return
                try: action=response.request.post_data_json.get('action')
                except Exception: action=None
                http_errors.append({'url':response.url,'status':response.status,'action':action})
            page.on('response',response_error)
            def console_error(message):
                if message.type!='error': return
                if 'Failed to load resource:' in message.text:
                    network_console.append({'text':message.text,'url':message.location.get('url','')})
                    return
                try:
                    details=[a.evaluate("x=>typeof x==='object'&&x!==null?JSON.stringify({text:String(x),message:x.message,stack:x.stack,name:x.name}):String(x)") for a in message.args]
                except Exception: details=[]
                errors.append(message.text+' '+str(details))
            page.on('console',console_error)
            startup_refusals=[];mutation_probe={'enabled':False,'requests':0}
            if json.loads((Path(args.plugin)/'manifest.json').read_text())['version']=='0.1.0-pi.8-ui.2':
                def delayed_activation(route):
                    data=route.request.post_data_json
                    if data.get('action')=='library' and not startup_refusals:
                        startup_refusals.append(True)
                        route.fulfill(status=404,content_type='text/plain',body='not found')
                    elif data.get('action')=='start' and mutation_probe['enabled']:
                        mutation_probe['requests']+=1
                        route.fulfill(status=503,content_type='application/json',body=json.dumps({'ok':False,'error':'Fixture Start refusal; no inference requested'}))
                    else:route.continue_()
                page.route('**/plugins-api/magazine-pi/rpc',delayed_activation)
            open_magazine(page,f'http://127.0.0.1:{args.port}')
            if startup_refusals:
                checks['initial_read_recovers_from_route_activation_race']='not ready' not in page.locator('[data-id=message]').inner_text() and not page.locator('[data-id=message]').evaluate("e=>e.classList.contains('error')")
                # This injected startup response is expected independently of
                # the invalid-plan refusal later in the journey.
                for entry in list(http_errors):
                    if entry['status']==404 and entry['action']=='library':http_errors.remove(entry)
                network_console[:]=[entry for entry in network_console if '404' not in entry['text']]
            if args.baseline:
                page.locator(f'button[data-run="{longrun}"]').click()
                page.locator('[data-id=ledger] table').wait_for()
                page.screenshot(path=str(out/'before-overview.png'),full_page=True)
                page.locator('[data-id=ledger] button').first.click()
                page.locator('[data-id=ledger] .attempt').first.wait_for()
                page.screenshot(path=str(out/'before-ledger.png'),full_page=True)
                checks['baseline_loaded_without_inference']=True
                report={'result':'PASS','browser':args.browser,'provider':'scripted loopback fixture; no real local inference','checks':checks,'errors':errors}
                (out/'baseline-ui.json').write_text(json.dumps(report,indent=2)+'\n')
                print(json.dumps(report,indent=2))
                return 0
            # One visible primary panel, full controls remain discoverable.
            for nav in ('overview','workflows','setup','ledger','chats','results','manage'):
                checks['nav_'+nav]=page.locator('[data-id=nav-'+nav+']').count()==1
            page.locator('[data-id=nav-overview]').focus();page.locator('[data-id=nav-overview]').press('ArrowRight')
            checks['keyboard_panel_navigation']=page.locator('[data-id=nav-workflows]').get_attribute('aria-selected')=='true'
            page.locator('[data-id=nav-workflows]').click();page.locator('[data-id=projects]').select_option(pid)
            if page.locator('[data-id=load-workflow]').count():
                checks['select_requires_explicit_load']=page.locator('[data-id=prepare]').is_disabled() and not page.locator('[data-id=preview-head]').inner_text()
                page.screenshot(path=str(out/'workflows-before-load.png'),full_page=True)
                page.locator('[data-id=load-workflow]').click()
            page.locator('[data-id=preview-head]').get_by_text('Long fixture',exact=False).wait_for()
            page.locator('[data-id=nav-setup]').click()
            if page.locator('[data-id=appearance]').count():
                checks['dark_reference_palette']=page.locator('.mag').evaluate("e=>getComputedStyle(e).color==='rgb(237, 242, 250)'")
                checks['long_workflow_steps_can_collapse']=not page.locator('[data-id=workflow-steps]').evaluate('(e)=>e.open')
                page.screenshot(path=str(out/'setup-shared-model.png'),full_page=True)
                page.locator('[data-id=appearance]').select_option('light')
                checks['light_appearance_available']=page.locator('.mag').evaluate("e=>getComputedStyle(e).color==='rgb(24, 40, 61)'")
                page.screenshot(path=str(out/'setup-light.png'),full_page=True)
                page.locator('[data-id=appearance]').select_option('dark')
            checks['model_review_visible']=page.locator('[data-id=shared-model]').input_value()=='fixture/model-a'
            page.locator('[data-id=plan-box] > summary').click()
            page.locator('[data-id=brief-build]').click()
            checks['brief_requires_sensitive_review']=page.locator('[data-id=planning-brief]').input_value()==''
            page.locator('[data-id=brief-consent]').check();page.locator('[data-id=brief-build]').click()
            page.wait_for_function("document.querySelector('[data-id=planning-brief]').value.includes('available_models')")
            checks['planning_brief_includes_tasks_and_ids']='Long fixture' in page.locator('[data-id=planning-brief]').input_value() and 'fixture/model-a' in page.locator('[data-id=planning-brief]').input_value()
            page.locator('[data-id=brief-consent]').uncheck()
            checks['copy_disabled_without_consent']=not page.locator('[data-id=brief-copy]').is_enabled()
            suggested={'schema_version':'1.0','workflow_name':'Long fixture','cartridge_ids':ids,
                       'cartridge_models':{cid:('fixture/model-b' if cid==ids[0] else 'fixture/model-a') for cid in ids},
                       'rationale':{ids[0]:'Different model for the bounded first fixture.'}}
            page.locator('[data-id=plan-json]').fill(json.dumps(suggested))
            page.locator('[data-id=plan-check]').click()
            page.wait_for_function("!document.querySelector('[data-id=plan-apply]').disabled")
            checks['validate_does_not_apply']=page.locator('[data-id=shared-model]').input_value()=='fixture/model-a' and not page.locator('[data-id=individual]').is_checked()
            invalid={**suggested,'cartridge_models':{**suggested['cartridge_models'],ids[-1]:'ollama/installed-only'}}
            page.locator('[data-id=plan-json]').fill(json.dumps(invalid))
            with page.expect_response(lambda r:r.request.method=='POST' and '"model_plan"' in (r.request.post_data or '')) as refused:
                page.locator('[data-id=plan-check]').click()
            checks['invalid_plan_http_refusal']=refused.value.status==400
            page.wait_for_function("document.querySelector('[data-id=message]').textContent.includes('Choose an exact available Pi model')")
            checks['invalid_plan_has_no_partial_apply']=not page.locator('[data-id=plan-apply]').is_enabled() and not page.locator('[data-id=individual]').is_checked() and page.locator('[data-id=shared-model]').input_value()=='fixture/model-a'
            page.locator('[data-id=plan-json]').fill(json.dumps(suggested));page.locator('[data-id=plan-check]').click()
            page.wait_for_function("!document.querySelector('[data-id=plan-apply]').disabled")
            page.locator('[data-id=plan-apply]').click()
            page.wait_for_function("document.querySelector('[data-id=individual]').checked")
            picks=page.locator('[data-id=cartridges] select[data-cartridge]')
            checks['explicit_all_or_none_apply']=picks.count()==145 and picks.first.input_value()=='fixture/model-b'
            checks['apply_keeps_rationale_and_clears_old_error']='Different model for the bounded first fixture.' in page.locator('[data-id=plan-preview]').inner_text() and 'error' not in (page.locator('[data-id=message]').get_attribute('class') or '')
            picks.first.select_option('fixture/model-a')
            checks['manual_override_after_plan']=picks.first.input_value()=='fixture/model-a'
            page.screenshot(path=str(out/'setup-model-plan-applied.png'),full_page=True)
            # Native dashboard/run selection is intentionally independent of setup.
            page.locator('[data-id=nav-overview]').click();page.locator(f'button[data-run="{longrun}"]').click()
            page.locator('[data-id=nav-ledger]').click()
            select=page.locator('[data-id=cartridge-select]');select.wait_for()
            checks['individual_145_options']=select.locator('option').count()==145
            for cid in (ids[0],ids[72],ids[-1]):
                select.select_option(cid)
                page.wait_for_function('(cid)=>document.querySelector("[data-id=cartridge-select]").value===cid',arg=cid)
                text=page.locator('[data-id=ledger]').inner_text()
                checks['ledger_'+cid]=cid in text
                # Attempts belonging to other records must not leak into the current detail.
                if cid!=ids[0]: checks['no_other_attempts_'+cid]='Historical c001' not in text and 'FIXTURE_FAILURE' not in text
            select.select_option(ids[0])
            checks['five_attempt_details']=page.locator('[data-id=ledger] .attempt').count()==5
            page.locator('[data-id=ledger] .attempt > summary').first.click()
            page.locator('[data-id=ledger] [data-attempt-chat]').first.click()
            page.wait_for_function("document.querySelector('[data-id=chat-heading]').textContent.includes('attempt 1')")
            checks['attempt_deep_link_keeps_full_history']=page.locator('[data-id=chat-list]').is_visible() and page.locator('[data-id=chat-list] [data-chat]').count()>=7
            checks['thought_hidden_in_visible_and_raw_view']='PRIVATE_THOUGHT' not in page.locator('[data-id=chat-messages]').inner_text() and 'PRIVATE_THOUGHT' not in page.locator('[data-id=chat-raw]').text_content()
            page.locator('[data-id=nav-ledger]').click()
            # Reset the actual host's scrolling surface, so the dropdown is in
            # the screenshot rather than hidden above a sticky header.
            page.locator('.mag').evaluate('e=>{for(let p=e.parentElement;p;p=p.parentElement)p.scrollTop=0}')
            page.screenshot(path=str(out/'ledger-145-five-attempts.png'),full_page=True)
            select.select_option(ids[72]);page.locator('[data-id=progress]').click()
            page.wait_for_function("document.querySelector('[data-id=message]').textContent.includes('Status refreshed:')")
            checks['selection_persists_refresh']=select.input_value()==ids[72]
            checks['pending_without_attempts']='No attempts' in page.locator('[data-id=ledger]').inner_text()
            # History stays present when choosing an old or active chat.
            page.locator('[data-id=nav-chats]').click()
            history=rpc(args.port,'chats',{'limit':200})['result']['chats']
            oldchat=next(c for c in history if c['run_id']==oldrun)
            livechat=next(c for c in history if c['run_id']==liverun and c['cartridge_id']=='live')
            def choose(chat):
                page.locator(f'[data-id=chat-list] [data-chat="{chat["chat_id"]}"]').click()
            choose(oldchat)
            page.locator('[data-id=chat-messages]').get_by_text('Old project retained task',exact=False).wait_for()
            checks['full_history_stays_visible']=page.locator('[data-id=chat-list]').is_visible()
            choose(livechat);page.locator('[data-id=chat-messages]').get_by_text('First live visible task',exact=False).wait_for()
            checks['active_user_prompt_visible']='First live visible task' in page.locator('[data-id=chat-messages]').inner_text()
            choose(oldchat);choose(livechat)
            gate.set();assert provider.wait_reached('NEXT')
            page.locator('[data-id=chat-messages]').get_by_text('Finished the task.',exact=False).wait_for(timeout=20000)
            checks['live_events_update_without_blocking_writer']=True
            checks['no_private_thinking']='PRIVATE_THOUGHT' not in page.locator('[data-id=chat-messages]').inner_text()
            page.screenshot(path=str(out/'history-live-updated.png'),full_page=True)
            nextgate.set();wait_status(args.port,liverun,'COMPLETED')
            page.locator('[data-id=nav-overview]').click();page.locator(f'button[data-run="{liverun}"]').click()
            page.locator('[data-id=nav-results]').click()
            page.locator('[data-id=results] > details > summary').first.click()
            checks['verified_result_separate_from_final_reply']='Result (from the committed handover)' in page.locator('[data-id=results]').inner_text() and page.locator('[data-id=results]').get_by_text('ok',exact=True).count()>0
            if page.locator('[data-id=previous-runs]').count():
                page.locator('[data-id=nav-overview]').click()
                page.locator('[data-id=previous-runs]').select_option(oldrun)
                checks['history_selection_does_not_start_or_open']=page.locator('[data-id=run-summary]').inner_text().find('Live project')>=0
                page.locator('[data-id=view-previous]').click()
                page.locator('[data-id=run-summary]').get_by_text('Old project',exact=True).wait_for()
                checks['explicit_view_previous_work']=rpc(args.port,'status',{'run_id':oldrun})['result']['display_status']=='COMPLETED'
                # Prepare a new run through the UI and show it READY before any Start.
                page.locator('[data-id=nav-workflows]').click();page.locator('[data-id=projects]').select_option(sparepid)
                page.locator('[data-id=load-workflow]').click()
                page.locator('[data-id=preview-head]').get_by_text('Spare project',exact=False).wait_for()
                page.locator('[data-id=prepare]').click()
                page.wait_for_function("document.querySelector('[data-id=message]').textContent.includes('Workflow READY')")
                checks['prepare_opens_ready_run_without_start']=page.locator('[data-id=nav-overview]').get_attribute('aria-selected')=='true' and 'READY' in page.locator('[data-id=run-summary]').inner_text() and page.locator('[data-id=start]').is_enabled()
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                checks['prepared_run_heading_is_in_view']=page.locator('.titlebar').bounding_box()['y']>=0
                (out/'navigation-scroll.json').write_text(json.dumps(page.locator('.mag').evaluate('e=>{const a=[];for(let p=e;p;p=p.parentElement)a.push({tag:p.tagName,cls:p.className,scrollTop:p.scrollTop,height:p.clientHeight,scrollHeight:p.scrollHeight,overflow:getComputedStyle(p).overflowY});return a}'),indent=2)+'\n')
                page.screenshot(path=str(out/'prepared-run-before-start.png'),full_page=True)
                prepared=rpc(args.port,'runs')['result']['runs'][0]['run_id']
                mutation_probe['enabled']=True
                page.locator('[data-id=start]').click()
                page.wait_for_function("document.querySelector('[data-id=message]').textContent.includes('Fixture Start refusal')")
                page.wait_for_timeout(1800)
                checks['start_failure_is_never_retried']=mutation_probe['requests']==1
                checks['failed_start_keeps_prepared_run_ready']=rpc(args.port,'status',{'run_id':prepared})['result']['status']=='READY'
                mutation_probe['enabled']=False
                http_errors[:]=[entry for entry in http_errors if not(entry['status']==503 and entry['action']=='start')]
                network_console[:]=[entry for entry in network_console if '503' not in entry['text']]
            page.locator('[data-id=nav-overview]').click();page.locator(f'button[data-run="{longrun}"]').click()
            # Keyboard access: focus the ledger selector and use native keys.
            page.locator('[data-id=nav-ledger]').click();select.focus();select.press('Home');select.press('ArrowDown')
            checks['firefox_keyboard_selector']=select.input_value()==ids[1]
            page.locator('[data-id=nav-manage]').click()
            page.locator(f'[data-delete-run="{sparerun}"]').click()
            checks['deletion_has_no_typed_confirmation']=page.locator('[data-id=delete-dialog] input').count()==0
            checks['deletion_confirm_ready_without_typing']=page.locator('[data-id=delete-confirm]').is_enabled()
            page.locator('[data-id=delete-confirm]').click()
            page.wait_for_function('(rid)=>!document.querySelector(`[data-delete-run="${rid}"]`)',arg=sparerun)
            checks['delete_inactive_run']=not (data/'magazine/runs/local'/sparerun).exists()
            checks['template_retained_after_run_delete']=(data/'magazine/library'/sparepid).is_dir()
            page.screenshot(path=str(out/'manage-inactive-deleted.png'),full_page=True)
            page.locator('[data-id=manage-trash]').get_by_role('button',name='Restore',exact=True).first.click()
            page.locator(f'[data-delete-run="{sparerun}"]').wait_for()
            checks['restore_inactive_run_from_ui']=(data/'magazine/runs/local'/sparerun).is_dir()
            page.locator('[data-id=nav-ledger]').click();select.select_option(ids[72]);page.reload();open_magazine(page,f'http://127.0.0.1:{args.port}')
            page.locator('[data-id=nav-ledger]').click()
            checks['cartridge_choice_persists_page_reload']=page.locator('[data-id=cartridge-select]').input_value()==ids[72]
            if page.locator('[data-id=appearance]').count():
                checks['appearance_choice_persists_reload']=page.locator('[data-id=appearance]').input_value()=='dark'
            page.set_viewport_size({'width':390,'height':844})
            checks['mobile_no_horizontal_overflow']=page.locator('.mag').evaluate('e=>e.scrollWidth<=e.clientWidth+1')
            checks['mobile_header_leaves_reading_space']=page.locator('.mag').evaluate('e=>e.querySelector(".project-header").offsetHeight+e.querySelector(".nav").offsetHeight < innerHeight*.65')
            select.focus();select.press('End')
            checks['mobile_keyboard_ledger_usable']=select.input_value()==ids[-1]
            page.screenshot(path=str(out/'mobile-ledger.png'),full_page=True)
            # Chromium reports the deliberately rejected invalid plan as a
            # resource error. Exempt exactly that verified response, while
            # retaining and failing any additional HTTP/console error.
            expected={'url':f'http://127.0.0.1:{args.port}/plugins-api/magazine-pi/rpc','status':400,'action':'model_plan'}
            checks['only_expected_http_refusal']=http_errors==[expected]
            for item in network_console:
                if not (http_errors==[expected] and item['url']==expected['url'] and '400' in item['text']):
                    errors.append(item['text']+' '+item['url'])
            checks['no_unexpected_console_errors']=not errors
            report={'result':'PASS' if all(checks.values()) and not errors else 'FAIL','browser':args.browser,
                    'provider':'scripted loopback fixture; no real local inference','host_service_worker':'blocked in isolated browser context; host PWA caching not under test','checks':checks,'errors':errors,'http_errors':http_errors,'network_console':network_console}
    except Exception as exc:
        report={'result':'FAIL','browser':args.browser,'checks':checks,'errors':errors,'exception':repr(exc)}
        if browser:
            try:page.screenshot(path=str(out/'failure.png'),full_page=True)
            except Exception:pass
    finally:
        for event in provider.gates.values():event.set()
        if browser:
            try:browser.close()
            except Exception:pass
        host.stop();log.close();provider.close();shutil.rmtree(tmp,ignore_errors=True)
    (out/'independent-ui.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));return 0 if report['result']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
