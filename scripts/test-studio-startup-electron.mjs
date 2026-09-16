// Actual React in an isolated hidden Electron window, with local test service/IPC boundaries.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdtemp, writeFile, mkdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const electron = require('electron')
const folder = await mkdtemp(path.join(tmpdir(), 'studio-startup-check-'))
const output = path.resolve('output/commercial-delivery-2026-09-16')
await mkdir(output, { recursive: true })
try {
  await build({ stdin: { contents: `
    import {createRoot} from 'react-dom/client';
    import App from './frontend/App';
    import {StudioAudioResources} from './frontend/features/short-drama/StudioAudioResources';
    const root=createRoot(document.getElementById('root'));
    root.render(<App/>);
    window.openAudioResources=()=>root.render(<StudioAudioResources/>);
  `, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, platform: 'browser', jsx: 'automatic',
  outfile: path.join(folder, 'app.js'), plugins: [{ name: 'workspace-boundary', setup(plugin) {
    plugin.onResolve({ filter: /\/StudioWorkspace$/ }, () => ({ path: 'workspace', namespace: 'test-boundary' }))
    plugin.onLoad({ filter: /.*/, namespace: 'test-boundary' }, () => ({ loader: 'tsx', contents: `
      export function StudioWorkspace({backendStatus,onReconnect}) { return <main data-workspace="true">
      <input aria-label="未发送内容" defaultValue="保留输入"/><span data-status="true">{backendStatus}</span>
      <button onClick={()=>void onReconnect()}>恢复连接</button></main> }
    `, resolveDir: process.cwd() }))
  } }] })
  await writeFile(path.join(folder, 'index.html'), `<!doctype html><meta charset="utf-8"><div id="root"></div><script>
    window.caseState={checkFails:true,startFails:true,checkCalls:0,startCalls:0,logOpens:0,status:null,listeners:[]};
    const state=window.caseState;
    window.publish=status=>{state.status={status};for(const listener of state.listeners)listener(state.status)};
    window.electronAPI={
      checkPythonReady:async()=>{state.checkCalls++;if(state.checkFails)throw Error('本地测试：运行环境检查失败');return {ready:true}},
      startPythonBackend:async()=>{state.startCalls++;if(state.startFails)throw Error('本地测试：安装包缺少运行环境');window.publish('alive')},
      getBackendHealthStatus:async()=>state.status,
      onBackendHealthStatus:listener=>{state.listeners.push(listener);return ()=>{state.listeners=state.listeners.filter(item=>item!==listener)}},
      getLogPath:async()=>({logPath:'/test/logs/current.log',logDir:'/test/logs'}),
      openLogFolder:async()=>{state.logOpens++;return true},
      writeLog:async()=>{},getBackend:async()=>({url:window.testBackend,token:''})
    };
    const port=new URLSearchParams(location.search).get('port');window.testBackend='http://127.0.0.1:'+port;
  </script><script src="app.js"></script>`)
  await writeFile(path.join(folder, 'main.cjs'), `
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs');const path=require('node:path');const http=require('node:http');
app.setPath('userData',path.join(__dirname,'isolated-profile'));
let resourceStatus='not_prepared',preparations=0;
const resources=()=>({supported:true,message:'声音资源按需准备；播放、剪辑和导出无需准备。',resources:[
  {id:'transcription',label:'对白识别与字幕对齐',model:'test/asr',status:resourceStatus,can_prepare:true,
   missing_files:resourceStatus==='ready'?[]:['完整模型权重'],error:resourceStatus==='failed'?'本地测试：磁盘不可写':null},
  {id:'speaker',label:'音色比较',model:null,status:'disabled',can_prepare:false,missing_files:[],error:null}]});
const server=http.createServer((req,res)=>{
  res.setHeader('Access-Control-Allow-Origin','*');res.setHeader('Access-Control-Allow-Headers','Content-Type');
  res.setHeader('Access-Control-Allow-Methods','GET,POST,OPTIONS');res.setHeader('Content-Type','application/json');
  if(req.method==='OPTIONS'){res.end();return}
  if(req.method==='POST'){preparations++;resourceStatus='preparing';setTimeout(()=>{resourceStatus=preparations===1?'failed':'ready'},120);res.statusCode=202}
  res.end(JSON.stringify(resources()));
});
const check=(condition,message)=>{if(!condition)throw Error(message)};
app.whenReady().then(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const win=new BrowserWindow({show:false,webPreferences:{contextIsolation:true,nodeIntegration:false}});
  const js=code=>win.webContents.executeJavaScript(code);
  const waitFor=async text=>{const until=Date.now()+7000;while(Date.now()<until){if(await js('document.body.textContent.includes('+JSON.stringify(text)+')'))return;await new Promise(resolve=>setTimeout(resolve,30))}throw Error('Missing UI: '+text)};
  const click=async text=>js('Array.from(document.querySelectorAll("button")).find(button=>button.textContent.includes('+JSON.stringify(text)+')).click()');
  await win.loadFile(path.join(__dirname,'index.html'),{query:{port:String(server.address().port)}});
  await waitFor('运行环境检查失败');await waitFor('/test/logs/current.log');
  check((await js('caseState.startCalls'))===0,'Readiness error started the backend');
  await js('caseState.checkFails=false');await click('重试启动');await waitFor('安装包缺少运行环境');
  await click('打开日志文件夹');check((await js('caseState.logOpens'))===1,'Log action was not invoked');
  await js('caseState.startFails=false');await click('重试启动');await waitFor('恢复连接');
  await js('document.querySelector("input").value="尚未发送的中文输入";window.publish("dead")');
  await waitFor('dead');
  check((await js('document.querySelector("input").value'))==='尚未发送的中文输入','Connection failure discarded workspace');
  await click('恢复连接');await waitFor('alive');
  check((await js('document.querySelector("input").value'))==='尚未发送的中文输入','Reconnect discarded workspace');
  const startup=await js('({readinessChecks:caseState.checkCalls,startRequests:caseState.startCalls,logOpens:caseState.logOpens})');
  await js('window.openAudioResources()');await waitFor('尚未准备');
  check(preparations===0,'Opening resource settings started preparation');
  await click('准备资源（需联网）');await waitFor('正在准备');await waitFor('磁盘不可写');
  await click('重试准备');await waitFor('正在准备');await waitFor('本机缓存可用');
  check(preparations===2,'Retry count incorrect');
  const result={startup,resourceStatus,preparations,workspacePreserved:true,
    scope:'Actual React/DOM and HTTP; deterministic test startup/prepare boundaries, no model download or product database.'};
  fs.writeFileSync(${JSON.stringify(path.join(output, 'startup-audio-resources-ui.json'))},JSON.stringify(result,null,2));
  console.log('STARTUP_AUDIO_UI_PASS '+JSON.stringify(result));win.destroy();server.close();app.exit(0);
}).catch(error=>{console.error(error);server.close();app.exit(1)});
setTimeout(()=>{console.error('Startup UI acceptance timed out');app.exit(2)},40000);
`)
  const result = await new Promise(resolve => {
    const child = spawn(electron, [path.join(folder, 'main.cjs')], { stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = '', stderr = ''
    child.stdout.on('data', value => { stdout += value })
    child.stderr.on('data', value => { stderr += value })
    child.on('error', error => resolve({ code: 1, stdout, stderr: String(error) }))
    child.on('exit', code => resolve({ code, stdout, stderr }))
  })
  assert.equal(result.code, 0, result.stdout + result.stderr)
  assert.match(result.stdout, /STARTUP_AUDIO_UI_PASS/)
  console.log(result.stdout.trim())
} finally { await rm(folder, { recursive: true, force: true }) }
