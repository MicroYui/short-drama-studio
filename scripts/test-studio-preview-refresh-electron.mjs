// Real React, HTTP and native media elements in an isolated hidden Electron window.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { execFileSync, spawn } from 'node:child_process'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const folder = await mkdtemp(path.join(tmpdir(), 'studio-preview-refresh-'))
const output = path.resolve('output/commercial-delivery-2026-09-16/native-editing-fixture/playback-readonly-review')
await mkdir(output, { recursive: true })
try {
  const defaultSubtitleFontFamily = JSON.parse(execFileSync('.venv/bin/python', ['-c', 'import json; from short_drama.studio_subtitle_fonts import default_subtitle_font_family; print(json.dumps(default_subtitle_font_family()))'], { cwd: 'backend', encoding: 'utf8' }))
  const ffmpeg = execFileSync('backend/.venv/bin/python', ['-c', 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'], { encoding: 'utf8' }).trim()
  execFileSync(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=160x284:rate=25', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '20', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ac', '2', '-movflags', '+faststart', path.join(folder, 'source.mp4')])
  await build({ stdin: { contents: `
    import {createRoot} from 'react-dom/client';
    import {useState} from 'react';
    import {StudioEditorialPreview} from './frontend/features/short-drama/StudioEditorialPreview';
    import {refreshPreviewSession} from './frontend/features/short-drama/studio-preview-refresh';
    import {getEditorModel} from './frontend/views/editor/editor-project-bridging';
    import {projectSchema} from './frontend/types/project-model';
    window.previewHelpers={refreshPreviewSession,getEditorModel,projectSchema};
    function Check() {
      const [props,setProps]=useState({projectId:'project-one',revision:8,showSubtitles:true});
      window.updatePreview=patch=>setProps(current=>({...current,...patch}));
      return <StudioEditorialPreview {...props} aspectRatio={160/284}/>;
    }
    createRoot(document.getElementById('root')).render(<Check/>);
  `, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, platform: 'browser', jsx: 'automatic', outfile: path.join(folder, 'app.js') })
  await writeFile(path.join(folder, 'index.html'), `<!doctype html><meta charset="utf-8"><style>
    #root{width:600px;height:720px}.relative{position:relative}.absolute{position:absolute}.hidden{display:none}.flex{display:flex}
  </style><div id="root"></div><script>
    window.electronAPI={getBackend:async()=>({url:'http://127.0.0.1:'+new URLSearchParams(location.search).get('port'),token:''})};
    window.createdAudio=[];window.browserErrors=[];
    window.addEventListener('error',event=>window.browserErrors.push(event.message));
    const nativeCreate=document.createElement.bind(document);
    document.createElement=function(...args){const element=nativeCreate(...args);if(args[0]==='audio')window.createdAudio.push(element);return element};
  </script><script src="app.js"></script>`)
  await writeFile(path.join(folder, 'main.cjs'), `
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
app.setPath('userData',path.join(__dirname,'isolated-profile'));
app.commandLine.appendSwitch('autoplay-policy','no-user-gesture-required');
const source=path.join(__dirname,'source.mp4');
const asset={id:'media',type:'video',path:source,duration:20,resolution:'160x284'};
const make=(id,revision,text,volume=1,timelineId='main')=>({revision,scope:'EPISODE',default_subtitle_font_family:${JSON.stringify(defaultSubtitleFontFamily)},project:{version:2,id,name:id,createdAt:0,updatedAt:revision,bins:{},assets:[asset],activeTimelineId:timelineId,timelines:[{id:timelineId,name:timelineId,createdAt:0,tracks:[{id:'video',name:'Video',muted:false,locked:false,kind:'video'},{id:'subtitle',name:'Subtitle',muted:false,locked:false,type:'subtitle'}],clips:[{id:'clip',assetId:asset.id,asset,type:'video',startTime:0,duration:20,trimStart:0,trimEnd:0,trackIndex:0,volume}],subtitles:[{id:'sub',text,startTime:0,endTime:20,trackIndex:1}]}]}});
let current=make('project-one',8,'保存前字幕'),nextDelay=0,nextError=false,requests=0;
const second=make('project-two',1,'第二集字幕');
const server=http.createServer((req,res)=>{
  res.setHeader('Access-Control-Allow-Origin','*');res.setHeader('Content-Type','application/json');requests++;
  const response=req.url.includes('project-two')?second:structuredClone(current),delay=nextDelay,error=nextError;nextDelay=0;nextError=false;
  setTimeout(()=>{res.statusCode=error?503:200;res.end(JSON.stringify(error?{detail:'isolated refresh unavailable'}:response))},delay);
});
const check=(condition,message)=>{if(!condition)throw Error(message)};
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
app.whenReady().then(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const win=new BrowserWindow({show:false,width:880,height:820,webPreferences:{contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  win.webContents.setAudioMuted(true);
  const js=code=>win.webContents.executeJavaScript(code);
  const wait=async(condition,message)=>{const until=Date.now()+8000;while(Date.now()<until){if(await condition())return;await delay(20)}throw Error(message+'; '+await js('document.body.textContent'))};
  const text=value=>wait(()=>js('document.body.textContent.includes('+JSON.stringify(value)+')'),'Missing '+value);
  const update=patch=>js('window.updatePreview('+JSON.stringify(patch)+')');
  const playing=()=>js('Boolean(document.querySelector("button[aria-label=暂停预览]"))');
  await win.loadFile(path.join(__dirname,'index.html'),{query:{port:String(server.address().port)}});
  await text('保存前字幕');
  if(current.default_subtitle_font_family)await wait(()=>js('Array.from(document.querySelectorAll("span")).some(span=>span.textContent==="保存前字幕"&&span.style.fontFamily.includes('+JSON.stringify(current.default_subtitle_font_family)+'))'),'Backend subtitle font metadata did not reach the actual monitor');
  await wait(()=>js('Boolean(document.querySelector("video")?.readyState>=2)'),'Initial media not decoded');
  await js('window.originalVideo=document.querySelector("video"); document.querySelector("button[aria-label=播放预览]").click()');
  await wait(()=>js('originalVideo.currentTime>1.0&&!originalVideo.paused'),'Initial playback did not advance');
  const before=await js('({time:originalVideo.currentTime,frames:originalVideo.getVideoPlaybackQuality().totalVideoFrames})');
  current=make('project-one',9,'保存后字幕',0.5);nextDelay=600;
  await update({revision:9});await delay(250);
  check(await js('document.querySelector("video")===originalVideo&&originalVideo.isConnected'),'Pending save unmounted the monitor');
  check(await playing(),'Pending save paused playback');
  await text('保存后字幕');
  await wait(()=>js('createdAudio.some(audio=>audio.volume===0.5&&!audio.paused)'),'Updated volume did not reach the actual audio element');
  const after=await js('({sameVideo:document.querySelector("video")===originalVideo,time:originalVideo.currentTime,frames:originalVideo.getVideoPlaybackQuality().totalVideoFrames,error:originalVideo.error?.message??null})');
  check(after.sameVideo&&after.time>before.time&&after.frames>before.frames&&!after.error,'Save reset or interrupted the decoding media instance');
  check(await playing(),'Save lost the playing state');
  const subtitleRequests=requests;
  await update({showSubtitles:false});await wait(()=>js('!document.body.textContent.includes("保存后字幕")'),'Subtitle toggle failed');
  await update({showSubtitles:true});await text('保存后字幕');
  check(requests===subtitleRequests,'Display-only subtitle toggle reloaded editorial');
  check(await js('document.querySelector("video")===originalVideo'),'Subtitle toggle recreated the monitor');
  nextError=true;await update({revision:10});await text('更新剪辑失败');
  check(await playing(),'Failed refresh paused the existing preview');
  check(await js('document.querySelector("video")===originalVideo'),'Failed refresh destroyed loaded media');
  current=make('project-one',11,'较慢旧回包');nextDelay=500;const count=requests;
  await update({revision:11});await wait(async()=>requests>count,'Delayed request did not start');
  current=make('project-one',12,'较新字幕',0.25);await update({revision:12});await text('较新字幕');await delay(550);
  check(!(await js('document.body.textContent.includes("较慢旧回包")')),'Late response replaced a newer revision');
  check(await playing(),'Out-of-order responses lost playing state');
  await js('document.querySelector("button[aria-label=暂停预览]").click()');await delay(80);
  const pausedTime=await js('originalVideo.currentTime');
  current=make('project-one',13,'暂停时保存');await update({revision:13});await text('暂停时保存');
  check(!(await playing()),'Paused refresh started playback');
  check(await js('document.querySelector("video")===originalVideo&&Math.abs(originalVideo.currentTime-'+pausedTime+')<0.1'),'Paused refresh reset the playhead');
  current=make('project-one',14,'另一个时间线',1,'alternate');await update({revision:14});await text('另一个时间线');
  await wait(()=>js('document.querySelector("video")!==originalVideo&&document.querySelector("video")?.currentTime<0.1'),'Timeline switch did not reset');
  await js('window.timelineVideo=document.querySelector("video")');
  nextDelay=400;await update({revision:15});await delay(30);await update({projectId:'project-two',revision:1});await text('第二集字幕');await delay(450);
  check(!(await js('document.body.textContent.includes("另一个时间线")')),'Old project response crossed scope');
  check(await js('document.querySelector("video")!==timelineVideo'),'Project switch retained the previous media instance');
  await js('window.projectVideo=document.querySelector("video")');
  await update({shotId:'shot-one'});await text('第二集字幕');await wait(()=>js('document.querySelector("video")!==projectVideo'),'Shot scope did not reset');
  const helperChecks=await js(${JSON.stringify(`(()=>{
    const {refreshPreviewSession:refresh,getEditorModel,projectSchema}=previewHelpers;
    const raw=${'FIXTURE'};
    const model=getEditorModel(projectSchema.parse(raw.project));
    const first=refresh(null,model,8,true),originalStore=first.store;
    first.store.getState().setStateWithoutHistory(state=>({...state,session:{...state.session,transport:{...state.session.transport,isPlaying:true,currentTime:3}}}));
    first.playbackTimeRef.current=3.21;
    const next=refresh(first,model,9,true);
    if(next.store!==originalStore||next.store.getState().state.session.transport.currentTime!==3.21)throw Error('Throttled transport replaced latest animation time');
    if(refresh(next,model,8,true)!==next)throw Error('Older revision accepted');
    const shorter={...model,timelines:model.timelines.map(t=>({...t,clips:t.clips.map(c=>({...c,duration:2})),subtitles:[]}))};
    const clamped=refresh(next,shorter,10,true);
    if(clamped.store!==originalStore||clamped.playbackTimeRef.current!==2)throw Error('Shortened content did not clamp the existing clock');
    return {sameStore:true,latestAnimationClockPreserved:true,olderRevisionIgnored:true,shorterTimelineClamped:true};
  })()`)}.replace('FIXTURE',JSON.stringify(make('check',8,'check'))));
  const errors=await js('window.browserErrors');check(errors.length===0,'Browser errors '+JSON.stringify(errors));
  const result={before,after,savePreservedNativeVideo:true,actualAudioVolumeUpdated:true,subtitleVisibilityWithoutReload:true,failedRefreshPreservedPlayback:true,outOfOrderResponsesIgnored:true,pausedPositionPreserved:true,timelineSwitchReset:true,projectAndShotScopeReset:true,helperChecks,requests,scope:'Actual React/HTTP + ProgramMonitor + playback/audio hooks with a locally generated 20s H264/AAC MP4; isolated hidden Electron profile. No live product DB, project writes or Provider. This does not establish the cause of the earlier Chromium Unable to play media error.'};
  fs.writeFileSync(${JSON.stringify(path.join(output,'preview-refresh-real.json'))},JSON.stringify(result,null,2));
  console.log('PREVIEW_REFRESH_PASS '+JSON.stringify(result));win.destroy();server.close();app.exit(0);
}).catch(error=>{console.error(error);server.close();app.exit(1)});
setTimeout(()=>{console.error('Preview refresh acceptance timed out');app.exit(2)},45000);
`)
  const result = await new Promise(resolve => {
    const child = spawn(require('electron'), [path.join(folder, 'main.cjs')], { stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = '', stderr = ''
    child.stdout.on('data', value => { stdout += value })
    child.stderr.on('data', value => { stderr += value })
    child.on('error', error => resolve({ code: 1, stdout, stderr: String(error) }))
    child.on('exit', code => resolve({ code, stdout, stderr }))
  })
  await writeFile(path.join(output, 'preview-refresh-real.txt'), result.stdout + result.stderr)
  process.stdout.write(result.stdout)
  assert.equal(result.code, 0, result.stderr)
} finally { await rm(folder, { recursive: true, force: true }) }
