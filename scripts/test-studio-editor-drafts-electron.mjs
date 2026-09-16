// Real IndexedDB transactions in isolated, hidden Electron windows. No product DB or media.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdtemp, writeFile, mkdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const electron = require('electron')
const folder = await mkdtemp(path.join(tmpdir(), 'studio-draft-check-'))
const output = path.resolve('output/maturity-goal-2026-09-16/editor-recovery')
await mkdir(output, { recursive: true })
try {
  await build({ entryPoints: ['frontend/features/short-drama/studio-editor-drafts.ts'], bundle: true,
    platform: 'browser', format: 'iife', globalName: 'Drafts', outfile: path.join(folder, 'drafts.js') })
  await writeFile(path.join(folder, 'index.html'), '<!doctype html><meta charset="utf-8"><script src="drafts.js"></script>')
  const main = `
const {app, BrowserWindow} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
app.setPath('userData', path.join(__dirname, 'isolated-profile'));
app.on('window-all-closed', () => {});
const open = async () => { const win = new BrowserWindow({ show:false, webPreferences:{contextIsolation:true,nodeIntegration:false} }); await win.loadFile(path.join(__dirname,'index.html')); return win; };
app.whenReady().then(async () => {
  let win = await open();
  const first = await win.webContents.executeJavaScript(
    ${JSON.stringify(`(async () => {
      const {editorDraftStorage:store, editorDraftKey:key}=Drafts;
      const base={version:2,id:'episode-1',name:'本集',createdAt:0,updatedAt:0,bins:{},assets:[],timelines:[],activeTimelineId:undefined};
      const draft=name=>({version:1,revision:3,updatedAt:Date.now(),base,current:{...base,name}});
      const keys=[{projectId:'project',episodeId:'episode-1'},
        {projectId:'project',episodeId:'episode-1',shotId:'shot',timelineId:'film',anchorClipId:'use-1'},
        {projectId:'project',episodeId:'episode-1',shotId:'shot',timelineId:'film',anchorClipId:'use-2'},
        {projectId:'project',episodeId:'episode-2'}].map(key);
      for(let i=0;i<keys.length;i++){ if(await store.claim(keys[i],'window-1')!==null) throw Error('unexpected existing draft'); await store.write(keys[i],'window-1',draft('draft-'+i)); await store.release(keys[i],'window-1'); }
      return {keys,stored:4,bytes:JSON.stringify(draft('draft-0')).length};
    })()`)});
  win.destroy();
  win = await open();
  const second = await win.webContents.executeJavaScript(
    ${JSON.stringify(`(async () => {
      const store=Drafts.editorDraftStorage;
      const keys=${'KEYS'};
      const recovered=[];
      for(let i=0;i<keys.length;i++){ const draft=await store.claim(keys[i],'window-2'); if(draft.current.name!=='draft-'+i) throw Error('wrong scope recovery'); recovered.push(draft.current.name); }
      if(await store.write(keys[0],'window-1',null)!==false) throw Error('old owner cleared new session');
      await store.release(keys[0],'window-1',true);
      const still=await store.claim(keys[0],'window-2');
      if(still.current.name!=='draft-0') throw Error('late release removed the recovery');
      await store.write(keys[0],'window-2',null);
      await store.release(keys[0],'window-2');
      if(await store.claim(keys[0],'window-3')!==null) throw Error('saved draft reappeared');
      await store.release(keys[1],'window-2',true);
      if(await store.claim(keys[1],'window-3')!==null) throw Error('cancelled local edit reappeared');
      if((await store.claim(keys[2],'window-3')).current.name!=='draft-2') throw Error('cancel erased another occurrence');
      await store.relocate(keys[2],'adopted-key','window-3');
      if((await store.claim('adopted-key','window-3')).current.name!=='draft-2') throw Error('adoption lost the saved draft');
      if(await store.claim(keys[2],'window-3')!==null) throw Error('old preview identity remains after adoption');
      let blocked=false;
      try { await store.relocate('adopted-key',keys[3],'window-3'); } catch { blocked=true; }
      if(!blocked || (await store.claim('adopted-key','window-3')).current.name!=='draft-2') throw Error('failed adoption relocation lost the last draft');
      return {recovered,oldResponseBlocked:true,savedCleared:true,cancelCleared:true,otherOccurrencePreserved:true,
        adoptionRelocated:true,failedRelocationPreservesDraft:true};
    })()`)}.replace('KEYS', JSON.stringify(first.keys)));
  const result={first,second};
  fs.writeFileSync(${JSON.stringify(path.join(output, 'indexeddb-real.json'))}, JSON.stringify(result,null,2));
  console.log('REAL_INDEXEDDB_PASS '+JSON.stringify(result));
  win.destroy(); app.exit(0);
}).catch(error=>{console.error(error);app.exit(1)});
setTimeout(()=>{console.error('IndexedDB acceptance timed out');app.exit(2)},45000);
`
  await writeFile(path.join(folder, 'main.cjs'), main)
  const result = await new Promise(resolve => {
    const child = spawn(electron, [path.join(folder, 'main.cjs')], { stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = '', stderr = ''
    child.stdout.on('data', value => { stdout += value })
    child.stderr.on('data', value => { stderr += value })
    child.on('close', code => resolve({ code, stdout, stderr }))
  })
  await writeFile(path.join(output, 'indexeddb-real.txt'), result.stdout + result.stderr)
  process.stdout.write(result.stdout)
  assert.equal(result.code, 0, result.stderr)
} finally { await rm(folder, { recursive: true, force: true }) }
