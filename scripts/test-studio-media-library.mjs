import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../frontend/features/short-drama/studio-media-library.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { assetGroups, mediaLabel, projectMediaFiles, libraryFileCards, fileReadLabel, fileModificationPrompt, selectedFileScope } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)

function version(id, extra = {}) {
  return { id, version: 1, descriptor: `情况说明｜${id}`, media_type: 'IMAGE', local_path: `/media/${id}.png`,
    origin_type: 'LOCAL_DERIVED', freshness: 'CURRENT', approval_status: 'DRAFT', width: 1440, height: 2032, ...extra }
}
function asset(id, versions, extra = {}) { return { id, name: id, kind: 'PROP', current_version_id: versions.at(-1)?.id, versions, ...extra } }
function project(assets, takes = [], extra = {}) {
  return { document: { assets, shots: [], reference_packs: [], voice_references: [], ...extra }, takes }
}
function take(id, extra = {}) { return { id, shot_id: 'shot-1', media_path: `/media/${id}.mp4`, thumbnail_path: '',
  candidate_index: 1, provider: 'acedata', model: 'MiniMax-H3', availability: 'AVAILABLE', freshness: 'CURRENT',
  qa_status: 'PENDING', is_selected: false, width: 1440, height: 2560, duration_seconds: 6,
  shot_windows: [{ shot_id: 'shot-1', source_in_seconds: 0, duration_seconds: 3 }, { shot_id: 'shot-2', source_in_seconds: 3, duration_seconds: 3 }], ...extra } }

test('full narrative state, punctuation and long text survive the label and detail handoff', () => {
  const description = 'wi-paper-signature | 情况说明｜仅赵衡签复核，经办空白。正文保持：本次报价出现错误，现补充核对。\n签字处不要增加印章。'
  assert.equal(mediaLabel(description), description.replace('wi-paper-signature | ', ''))
  const long = '情况说明｜' + '保持具体说明。'.repeat(25)
  assert.equal(mediaLabel(long), long)
  assert.equal(mediaLabel("AB/C｜don't change 13.00"), "AB/C｜don't change 13.00")
})

test('all media includes unbound usable DRAFT states; default reference groups keep previous behavior', () => {
  const p = project([asset('paper', [version('blank'), version('single'), version('both'), version('text', { media_type: 'TEXT_REFERENCE', local_path: null })], { current_version_id: 'single' })])
  const before = structuredClone(p)
  assert.deepEqual(assetGroups(p)[0].files.map(file => file.version.id), ['single'])
  const groups = assetGroups(p, { allMedia: true })
  const files = projectMediaFiles(p, groups)
  assert.deepEqual(files.map(file => file.id), ['blank', 'single', 'both'])
  assert.equal(files[0].state, '候选素材')
  assert.equal(fileReadLabel(files[0]), '尚未验证读取')
  assert.equal(fileReadLabel(files[0], 'loaded'), '可预览')
  assert.deepEqual(p, before)
})

test('missing, empty, rejected and stale records never acquire an implicit usable verdict', () => {
  const p = project([asset('paper', [version('missing', { local_path: null }), version('empty', { file_size_bytes: 0 }),
    version('rejected', { approval_status: 'REJECTED' }), version('stale', { freshness: 'STALE' })])])
  const files = projectMediaFiles(p, assetGroups(p, { allMedia: true }))
  assert.equal(fileReadLabel(files[0], 'loaded'), '文件缺失或为空')
  assert.equal(fileReadLabel(files[1], 'loaded'), '文件缺失或为空')
  assert.equal(files[2].warning, '已拒绝')
  assert.equal(files[3].warning, '来源已变化，需核对')
  assert.equal(fileReadLabel(files[2], 'error'), '无法读取文件')
})

test('one generated file covers multiple shots and merges an existing video reference without losing source', () => {
  const video = take('group')
  const p = project([asset('video-reference', [version('video-file', { media_type: 'VIDEO', local_path: video.media_path })])], [video],
    { shots: [{ id: 'shot-1', sequence: 1, narrative_purpose: '开始', references: [] }, { id: 'shot-2', sequence: 2, narrative_purpose: '回应', references: [] }] })
  const files = projectMediaFiles(p, assetGroups(p, { allMedia: true }))
  assert.equal(files.length, 1)
  assert.equal(files[0].take.id, 'group')
  assert.deepEqual(files[0].shotIds, ['shot-1', 'shot-2'])
  assert.match(files[0].source, /acedata.*MiniMax-H3/)
  assert.deepEqual(selectedFileScope(files[0]), { kind: 'asset', id: 'video-reference', label: '情况说明｜video-file', asset_version_id: 'video-file', take_id: 'group' })
})

test('saved editorial audio is visible and actual clip takeIndex determines usage, without adding duplicate files', () => {
  const p = project([], [take('group', { is_selected: true })])
  const editorial = { assets: [
    { id: 'sound', type: 'audio', path: '/media/sound.wav', duration: 2, prompt: '桌边落笔声' },
    { id: 'video', type: 'video', path: '/media/group.mp4', takes: [{ path: '/media/other.mp4', width: 1280, height: 720 }] },
    { id: 'grade', type: 'adjustment', path: '/media/not-a-file' },
  ], timelines: [{ clips: [{ assetId: 'sound', type: 'audio' }, { assetId: 'video', type: 'video', takeIndex: 0, sourceShotId: 'shot-2' }] }] }
  const before = structuredClone({ p, editorial })
  const files = projectMediaFiles(p, [], editorial)
  assert.equal(files.length, 3)
  assert.equal(files.find(file => file.localPath === '/media/group.mp4').editorialUsed, false)
  assert.equal(files.find(file => file.localPath === '/media/other.mp4').editorialUsed, true)
  assert.equal(files.find(file => file.mediaType === 'AUDIO').editorialUsed, true)
  assert.deepEqual({ p, editorial }, before)
})

test('natural modification copy excludes IDs while hidden scope retains the exact candidate', () => {
  const selected = version('asset-version-single', { descriptor: '情况说明｜仅赵衡签复核，经办空白', text_plate: { content_state: { name: '仅复核签名' } } })
  const p = project([asset('paper', [selected])])
  const file = projectMediaFiles(p, assetGroups(p, { allMedia: true }))[0]
  assert.match(fileModificationPrompt(file), /仅赵衡签复核，经办空白/)
  assert.doesNotMatch(fileModificationPrompt(file), /asset-version-single|AssetVersion|Take|\/media/)
  assert.equal(selectedFileScope(file).asset_version_id, selected.id)
  const sourceVideo = projectMediaFiles(project([], [take('exact-take')]), [])[0]
  assert.equal(selectedFileScope(sourceVideo).take_id, 'exact-take')
})

test('file view deduplicates shared paths without changing core identities or the exact default target', () => {
  const p = project([asset('paper', [version('blank')]), asset('shared-paper', [version('same-file', { local_path: '/media/blank.png' })])])
  const groups = assetGroups(p, { allMedia: true })
  const files = projectMediaFiles(p, groups)
  const before = structuredClone(files)
  const cards = libraryFileCards(files)
  assert.equal(groups.length, 2)
  assert.equal(files.length, 2)
  assert.equal(cards.length, 1)
  assert.deepEqual(cards[0].relatedFiles.map(file => file.owner), ['paper', 'shared-paper'])
  assert.equal(selectedFileScope(cards[0]).asset_version_id, 'blank')
  assert.equal(selectedFileScope(files[1]).asset_version_id, 'same-file')
  assert.deepEqual(files, before)
})

test('two Takes of one imported file keep the first visible target and all coverage and adoption', () => {
  const first = take('take-first', { media_path: '/media/shared.mp4', shot_id: 'shot-1', shot_windows: [], is_selected: true })
  const second = take('take-second', { media_path: '/media/shared.mp4', shot_id: 'shot-2', shot_windows: [], is_selected: false })
  const p = project([], [first, second], { shots: [
    { id: 'shot-1', sequence: 1, narrative_purpose: '第一处使用' },
    { id: 'shot-2', sequence: 2, narrative_purpose: '第二处使用' },
  ] })
  const before = structuredClone(p)
  const files = projectMediaFiles(p, [])
  assert.equal(files.length, 1)
  assert.equal(files[0].id, 'take-first')
  assert.match(files[0].title, /镜头 01/)
  assert.equal(selectedFileScope(files[0]).id, 'shot-1')
  assert.equal(selectedFileScope(files[0]).take_id, 'take-first')
  assert.equal(files[0].editorialUsed, true)
  assert.deepEqual(files[0].shotIds, ['shot-1', 'shot-2'])
  assert.deepEqual(files[0].relatedTakes.map(item => item.id), ['take-first', 'take-second'])
  assert.deepEqual(p, before)
})
