// Actual React/HTTP in an isolated hidden Electron window; no product service or media.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdtemp, writeFile, mkdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const electron = require('electron')
const folder = await mkdtemp(path.join(tmpdir(), 'studio-transfer-ui-'))
const output = path.resolve('output/commercial-delivery-2026-09-16/project-transfer-ui')
await mkdir(output, { recursive: true })
try {
  await build({ stdin: { contents: `
    import {createRoot} from 'react-dom/client';
    import {useState} from 'react';
    import {StudioProjectTransfer} from './frontend/features/short-drama/StudioProjectTransfer';
    function Check() {
      const [unsaved,setUnsaved]=useState(true); window.setUnsaved=setUnsaved;
      return <div className="sd-app"><StudioProjectTransfer projectId="current-project" hasUnsavedEditor={unsaved}
        onImported={async()=>{window.caseState.importNotifications++}}
        onOpen={id=>{window.caseState.opened.push(id)}}
        onError={error=>{window.caseState.errors.push(String(error))}}/></div>;
    }
    createRoot(document.getElementById('root')).render(<Check/>);
  `, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, platform: 'browser', jsx: 'automatic', outfile: path.join(folder, 'app.js') })
  await writeFile(path.join(folder, 'index.html'), `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="app.css"><div id="root"></div><script>
    window.caseState={importNotifications:0,opened:[],errors:[],revealed:[]};
    window.electronAPI={
      getBackend:async()=>({url:'http://127.0.0.1:'+new URLSearchParams(location.search).get('port'),token:''}),
      showItemInFolder:async({filePath})=>{window.caseState.revealed.push(filePath)}
    };
  </script><script src="app.js"></script>`)
  const main = `
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
app.setPath('userData',path.join(__dirname,'isolated-profile'));
const row=(id,extra={})=>({id,operation:'collect',project_id:'current-project',project_name:id,status:'queued',stage:'queued',message:'等待开始',processed_files:0,total_files:null,processed_bytes:0,total_bytes:null,cancellable:true,created_at:new Date().toISOString(),updated_at:new Date().toISOString(),...extra});
const packageFor=id=>({package_id:'package-'+id,path:'/local/completed-'+id+'.zip',filename:'完成项目.zip',download_url:'/download/'+id,episode_count:2,media_count:3,missing_media:[]});
let records=Array.from({length:22},(_,i)=>row('history-'+String(i).padStart(2,'0'),{status:'completed',stage:'completed',cancellable:false,created_at:'2026-09-01T00:00:00Z'}));
records.find(r=>r.id==='history-00').package=packageFor('history-00');
let collectCount=0,importCount=0,getCount=0,downloadCount=0,offline=false;
let lateSnapshot=null,lateStarted=false;
const update=(id,extra)=>{const found=records.find(row=>row.id===id);Object.assign(found,extra,{updated_at:new Date().toISOString()});return found};
const server=http.createServer((req,res)=>{
  res.setHeader('Access-Control-Allow-Origin','*');res.setHeader('Access-Control-Allow-Headers','Content-Type');res.setHeader('Access-Control-Allow-Methods','GET,POST,OPTIONS');res.setHeader('Content-Type','application/json');
  if(req.method==='OPTIONS'){res.end();return}
  const url=new URL(req.url,'http://localhost'),pathname=url.pathname;
  const send=data=>res.end(JSON.stringify(data));
  if(pathname.startsWith('/download/')){downloadCount++;return send({})}
  if(req.method==='GET'&&offline){res.statusCode=503;return send({detail:'local offline fixture'})}
  if(pathname.endsWith('/project-transfers')){
    const all=[...records].sort((a,b)=>b.created_at.localeCompare(a.created_at)||b.id.localeCompare(a.id));
    const before=url.searchParams.get('before');const offset=before?all.findIndex(row=>row.id===before)+1:0;
    const page=all.slice(offset,offset+20);return send({transfers:page,next_cursor:offset+20<all.length?page.at(-1).id:null});
  }
  if(pathname.endsWith('/package-jobs')){collectCount++;const next=row('collect-'+collectCount);records.unshift(next);res.statusCode=202;return send(next)}
  if(pathname.endsWith('/import-package-jobs')){
    req.resume();req.on('end',()=>setTimeout(()=>{importCount++;const next=row('import-'+importCount,{operation:'import',project_name:'导入验收.zip'});records.unshift(next);res.statusCode=202;send(next)},600));return;
  }
  const parts=pathname.split('/');const cancelling=parts.at(-1)==='cancel';const id=decodeURIComponent(parts.at(cancelling?-2:-1));
  if(cancelling)return send(update(id,{status:'cancelling',cancellable:false,message:'正在取消并清理'}));
  getCount++;
  if(lateSnapshot&&id===lateSnapshot.id){const snapshot=lateSnapshot;lateSnapshot=null;lateStarted=true;setTimeout(()=>send(snapshot),1300);return}
  const found=records.find(row=>row.id===id);if(!found){res.statusCode=404;return send({detail:'not found'})}send(found);
});
const check=(value,message)=>{if(!value)throw Error(message)};
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
app.whenReady().then(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const win=new BrowserWindow({show:false,width:880,height:820,webPreferences:{contextIsolation:true,nodeIntegration:false}});
  const js=code=>win.webContents.executeJavaScript(code);
  const wait=async(test,message)=>{const end=Date.now()+8000;while(Date.now()<end){if(await test())return;await delay(25)}throw Error(message)};
  const text=async value=>wait(()=>js('document.body.textContent.includes('+JSON.stringify(value)+')'),'Missing text: '+value);
  const click=async label=>js('Array.from(document.querySelectorAll("button")).find(button=>button.textContent.trim()==='+JSON.stringify(label)+').click()');
  const close=async()=>js('document.querySelector("button[aria-label=关闭项目迁移]").click()');
  await win.loadFile(path.join(__dirname,'index.html'),{query:{port:String(server.address().port)}});
  await text('项目收集与迁移');await click('项目收集与迁移');await text('history-21');
  await click('查看更多记录');await text('history-00');
  await click('收集当前项目');await text('collect-1');
  update('collect-1',{status:'running',stage:'media',message:'正在收集测试媒体',processed_files:0,total_files:1,processed_bytes:300,total_bytes:1000});
  await text('30%');await text('0 / 1 份文件');await text('300 B / 1000 B');
  await close();await delay(100);const closedGets=getCount;await delay(1100);check(getCount===closedGets,'Closed panel continued polling');
  update('collect-1',{status:'completed',stage:'completed',message:'项目包已完成',package:packageFor('collect-1'),cancellable:false});
  await click('项目收集与迁移');await text('项目包已完成');
  await js('Array.from(document.querySelectorAll(".sd-transfer-record")).find(row=>row.textContent.includes("collect-1")).querySelector(".sd-primary").click()');
  await wait(()=>js('caseState.revealed.length===1'),'Native package was not revealed');check(downloadCount===0,'Native package downloaded a Blob');
  await js('const input=document.querySelector("input[type=file]");const dt=new DataTransfer();dt.items.add(new File(["test zip content"],"导入验收.zip",{type:"application/zip"}));input.files=dt.files;input.dispatchEvent(new Event("change",{bubbles:true}))');
  await text('正在发送项目包');check(await js('document.querySelector("button[aria-label=关闭项目迁移]").disabled'),'Upload allowed panel close before acknowledgement');
  await text('导入 · 导入验收.zip');check(importCount===1,'Upload resubmitted');
  check(!(await js('document.querySelector("button[aria-label=关闭项目迁移]").disabled')),'Accepted upload still blocks panel close');
  update('import-1',{status:'completed',stage:'completed',cancellable:false,message:'导入完成',imported:{project_id:'new-project',episode_ids:['new-project','new-episode'],media_count:3,missing_media:[{source_path:'/旧媒体/缺失图片.png',reason:'文件缺失',archive_path:'media/missing',status:'missing',bytes:0}],trace_path:'/trace',notice:'已有项目未覆盖'}});
  await text('导入完成');await text('1 份媒体缺失');await wait(()=>js('caseState.importNotifications===1'),'Import completion did not refresh project list');
  check(await js('Array.from(document.querySelectorAll("button")).find(button=>button.textContent==="打开导入的项目").disabled'),'Unsaved editor was not protected');
  await js('window.setUnsaved(false)');await delay(30);await click('打开导入的项目');check((await js('caseState.opened[0]'))==='new-project','Small import result did not navigate to project_id');
  await click('项目收集与迁移');await text('collect-1');await click('收集当前项目');await text('collect-2');
  update('collect-2',{status:'running',stage:'media',message:'正在复制',processed_bytes:10,total_bytes:100});lateSnapshot={...records.find(row=>row.id==='collect-2')};
  await wait(async()=>lateStarted,'Polling did not start');
  await js('Array.from(document.querySelectorAll(".sd-transfer-record")).find(row=>row.textContent.includes("collect-2")).querySelector("button").click()');
  await text('正在取消并清理');await delay(1450);
  check(await js('Array.from(document.querySelectorAll(".sd-transfer-record")).find(row=>row.textContent.includes("collect-2")).dataset.status==="cancelling"'),'Late GET erased cancellation');
  offline=true;await text('状态暂时无法更新');offline=false;
  update('collect-2',{status:'cancelled',stage:'cancelled',message:'取消完成',cancellable:false});await text('取消完成');
  const finishedGets=getCount;await delay(1100);check(getCount===finishedGets,'Terminal task still polled');
  const result={reactHttpPassed:true,historyPagination:true,actualStageCounters:true,closedPanelStopsPolling:true,reopenFindsResult:true,uploadAckBoundary:true,importSummaryNavigation:true,unsavedEditorProtected:true,lateCancellationProtected:true,reconnected:true,terminalPollingStopped:true,collectCount,importCount,downloadCount,client:await js('caseState'),scope:'Actual React/DOM and local deterministic HTTP; isolated Electron profile; no product database, Provider or user media.'};
  check(result.client.errors.length===0,'Unexpected UI errors '+JSON.stringify(result.client.errors));
  fs.writeFileSync(${JSON.stringify(path.join(output, 'react-http.json'))},JSON.stringify(result,null,2));console.log('PROJECT_TRANSFER_UI_PASS '+JSON.stringify(result));
  win.destroy();server.close();app.exit(0);
}).catch(error=>{console.error(error);server.close();app.exit(1)});
setTimeout(()=>{console.error('Project transfer UI acceptance timed out');app.exit(2)},45000);
`
  await writeFile(path.join(folder, 'main.cjs'), main)
  const result = await new Promise(resolve => {
    const child = spawn(electron, [path.join(folder, 'main.cjs')], { stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = '', stderr = ''
    child.stdout.on('data', value => { stdout += value })
    child.stderr.on('data', value => { stderr += value })
    child.on('error', error => resolve({ code: 1, stdout, stderr: String(error) }))
    child.on('exit', code => resolve({ code, stdout, stderr }))
  })
  await writeFile(path.join(output, 'react-http.txt'), result.stdout + result.stderr)
  process.stdout.write(result.stdout)
  assert.equal(result.code, 0, result.stderr)
} finally { await rm(folder, { recursive: true, force: true }) }
