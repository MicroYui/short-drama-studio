// Actual backend ASS and browser font metrics, with no project writes or Provider.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {execFileSync,spawn} from 'node:child_process'

const require=createRequire(import.meta.url),{build}=createRequire(require.resolve('vite'))('esbuild')
const folder=await mkdtemp(path.join(tmpdir(),'studio-subtitle-font-'))
const output=path.resolve('output/commercial-delivery-2026-09-16/native-editing-fixture/playback-readonly-review/subtitle-font-check')
await mkdir(output,{recursive:true})
try{
  const python=`
import json,subprocess,sys
from pathlib import Path
import imageio_ffmpeg
import numpy as np
from PIL import Image
from short_drama.studio_editorial import EditorTimeline,EditorTrack,EditorSubtitle
from short_drama.studio_export import _write_subtitles
from short_drama.studio_subtitle_fonts import default_subtitle_font_family
output=Path(sys.argv[1]); family=default_subtitle_font_family()
assert family is not None, 'No confirmed local default subtitle font available for this acceptance'
rows=[]
for width,height in [(720,1280),(1280,720)]:
 for key,text in [('chinese','第一段：原声降低一半'),('ascii','Sound stays in sync'),('mixed','镜头02 Audio 50%')]:
  destination=output/f'{width}x{height}-{key}';destination.mkdir(exist_ok=True)
  timeline=EditorTimeline(id='local',name='Local',tracks=[EditorTrack(id='sub',name='字幕',type='subtitle')],clips=[],subtitles=[EditorSubtitle(id='cue',text=text,startTime=0,endTime=1,trackIndex=0,style={})])
  before=timeline.model_dump_json();_,ass=_write_subtitles(timeline,destination,width,height)
  assert timeline.model_dump_json()==before,'Rendering wrote logical subtitle style'
  frame=destination/'ass.png'
  cmd=[imageio_ffmpeg.get_ffmpeg_exe(),'-y','-hide_banner','-f','lavfi','-i',f'color=black:s={width}x{height}:r=25:d=1','-vf',f"subtitles=filename='{ass}'",'-frames:v','1',str(frame)]
  run=subprocess.run(cmd,capture_output=True,text=True);assert run.returncode==0,run.stderr
  (destination/'font-selection.log').write_text(run.stderr)
  rgb=np.asarray(Image.open(frame).convert('RGB'));y,x=np.where((rgb>210).all(axis=2))
  rows.append({'key':key,'text':text,'width':width,'height':height,'family':family,'inkWidth':int(x.max()-x.min()+1),'inkHeight':int(y.max()-y.min()+1),'lines':1})
(output/'ass.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2));print(json.dumps(rows,ensure_ascii=False))
`
  const rows=JSON.parse(execFileSync('.venv/bin/python',['-c',python,output],{cwd:path.resolve('backend'),encoding:'utf8'}))
  await build({stdin:{contents:`export{confirmStudioSubtitleFont,studioSubtitlePreviewFont}from'./frontend/lib/studio-subtitle-font';export{DEFAULT_SUBTITLE_STYLE}from'./frontend/types/project-model';`,resolveDir:process.cwd(),loader:'ts'},bundle:true,platform:'browser',format:'iife',globalName:'SubtitleFont',outfile:path.join(folder,'font.js')})
  await writeFile(path.join(folder,'index.html'),'<meta charset="utf-8"><style>*{box-sizing:border-box}</style><script src="font.js"></script>')
  const browserCheck=async rows=>{
    const {confirmStudioSubtitleFont:confirm,studioSubtitlePreviewFont:fontFor,DEFAULT_SUBTITLE_STYLE:defaults}=SubtitleFont
    const family=await confirm(rows[0].family)
    if(family!==rows[0].family)throw Error('Backend-selected family is unavailable to this browser')
    if(await confirm('Studio nonexistent acceptance font 999')!==null)throw Error('Missing face was silently accepted')
    const original=JSON.stringify(defaults),results=[]
    for(const row of rows){
      const font=fontFor(defaults,row.height/1080,family)
      if(!font.matchedDefault)throw Error('Confirmed default was not calibrated')
      const canvas=document.createElement('canvas');canvas.width=row.width;canvas.height=row.height
      const ctx=canvas.getContext('2d');ctx.fillStyle='#000';ctx.fillRect(0,0,row.width,row.height)
      ctx.font=`${font.fontSize}px ${JSON.stringify(font.fontFamily)}`;ctx.fillStyle='#fff';ctx.fillText(row.text,20,120)
      const pixels=ctx.getImageData(0,0,row.width,row.height).data
      let minX=row.width,minY=row.height,maxX=-1,maxY=-1
      for(let i=0;i<pixels.length;i+=4)if(pixels[i]>210&&pixels[i+1]>210&&pixels[i+2]>210){const x=i/4%row.width,y=Math.floor(i/4/row.width);minX=Math.min(x,minX);maxX=Math.max(x,maxX);minY=Math.min(y,minY);maxY=Math.max(y,maxY)}
      const paragraph=document.createElement('div');Object.assign(paragraph.style,{display:'flex',width:row.width+'px'})
      const span=document.createElement('span');Object.assign(span.style,{maxWidth:'90%',fontFamily:font.fontFamily,fontSize:font.fontSize+'px',lineHeight:'1.375',margin:'auto',whiteSpace:'pre-wrap',padding:'0'});span.textContent=row.text;paragraph.append(span);document.body.append(paragraph)
      const ys=[];for(let i=0;i<row.text.length;i++){const range=document.createRange();range.setStart(span.firstChild,i);range.setEnd(span.firstChild,i+1);ys.push(range.getBoundingClientRect().y)}
      results.push({...row,browser:{inkWidth:maxX-minX+1,inkHeight:maxY-minY+1,lines:new Set(ys).size,fontSize:font.fontSize,family:font.fontFamily},png:canvas.toDataURL()});paragraph.remove()
    }
    const explicit={...defaults,fontFamily:"'Arial', sans-serif",fontSize:71,fontWeight:'bold',italic:true}
    const copy=JSON.stringify(explicit),unchanged=fontFor(explicit,1,family)
    if(unchanged.fontFamily!==explicit.fontFamily||unchanged.fontSize!==71||unchanged.matchedDefault||JSON.stringify(explicit)!==copy)throw Error('Explicit font style changed')
    if(fontFor(defaults,1,null).matchedDefault)throw Error('Unconfirmed font claimed matching')
    for(const generic of ['','   ','sans-serif',"'sans-serif'",'system-ui'])if(!fontFor({...defaults,fontFamily:generic},1,family).matchedDefault)throw Error('Legacy generic default was not resolved')
    if(JSON.stringify(defaults)!==original)throw Error('Default logical style mutated')
    return {results,explicitStylePreserved:true,unconfirmedFontNotClaimed:true,logicalStylePreserved:true}
  }
  await writeFile(path.join(folder,'main.cjs'),`const{app,BrowserWindow}=require('electron');const fs=require('node:fs'),path=require('node:path');app.setPath('userData',path.join(__dirname,'profile'));app.whenReady().then(async()=>{const win=new BrowserWindow({show:false,webPreferences:{contextIsolation:true,nodeIntegration:false}});await win.loadFile(path.join(__dirname,'index.html'));const data=await win.webContents.executeJavaScript(${JSON.stringify('('+browserCheck.toString()+')('+JSON.stringify(rows)+')')});for(const row of data.results){const filename=path.join(${JSON.stringify(output)},row.width+'x'+row.height+'-'+row.key,'browser.png');fs.writeFileSync(filename,Buffer.from(row.png.split(',')[1],'base64'));delete row.png}fs.writeFileSync(${JSON.stringify(path.join(output,'browser.json'))},JSON.stringify(data,null,2));console.log('BROWSER_FONT_CHECK_PASS');win.destroy();app.exit(0)}).catch(e=>{console.error(e);app.exit(1)});setTimeout(()=>app.exit(2),20000)`)
  const result=await new Promise(resolve=>{const child=spawn(require('electron'),[path.join(folder,'main.cjs')],{stdio:['ignore','pipe','pipe']});let stdout='',stderr='';child.stdout.on('data',d=>stdout+=d);child.stderr.on('data',d=>stderr+=d);child.on('exit',code=>resolve({code,stdout,stderr}))})
  assert.equal(result.code,0,result.stderr)
  const comparison=JSON.parse(await readFile(path.join(output,'browser.json'),'utf8'))
  for(const row of comparison.results){
    assert.ok(Math.abs(row.inkWidth-row.browser.inkWidth)<=3,`${row.width}x${row.height} ${row.key}: width differs ${row.inkWidth}/${row.browser.inkWidth}`)
    assert.ok(Math.abs(row.inkHeight-row.browser.inkHeight)<=2,`${row.width}x${row.height} ${row.key}: height differs ${row.inkHeight}/${row.browser.inkHeight}`)
    assert.equal(row.browser.lines,row.lines,`${row.key}: line wrapping differs`)
  }
  const summary={passed:true,cases:comparison.results.map(row=>({size:`${row.width}x${row.height}`,text:row.key,family:row.family,ass:[row.inkWidth,row.inkHeight],browser:[row.browser.inkWidth,row.browser.inkHeight],lines:row.browser.lines})),explicitStylePreserved:comparison.explicitStylePreserved,unconfirmedFontNotClaimed:comparison.unconfirmedFontNotClaimed,logicalStylePreserved:comparison.logicalStylePreserved,scope:'Actual current backend _write_subtitles/libass and browser helper/canvas/DOM, three text classes at portrait and landscape resolutions; default confirmed local face only. Explicit fallback-font equivalence is not claimed.'}
  await writeFile(path.join(output,'result.json'),JSON.stringify(summary,null,2))
  await writeFile(path.join(output,'result.txt'),result.stdout+result.stderr+'\n'+JSON.stringify(summary)+'\n')
  console.log('STUDIO_SUBTITLE_FONT_PASS '+JSON.stringify(summary))
}finally{await rm(folder,{recursive:true,force:true})}
