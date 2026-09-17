import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { Download } from 'lucide-react'
import ts from 'typescript'

// Compile the actual private renderer without loading the whole workspace UI.
const source = ts.createSourceFile('StudioAgent.tsx', readFileSync(new URL('../frontend/features/short-drama/StudioAgent.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX)
const declaration = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'ChatText')
assert(declaration, 'The current StudioAgent ChatText renderer must be present')
const compiled = ts.transpileModule(declaration.getText(source), {
  compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022 },
}).outputText
const createRenderer = new Function('React', 'Download', 'StudioApi', `${compiled}\nreturn ChatText`)

function render(text) {
  const downloads = []
  const fakeApi = { downloadMedia: async (projectId, path, name) => { downloads.push({ projectId, path, name }) } }
  const ChatText = createRenderer(React, Download, fakeApi)
  const element = ChatText({ text, projectId: 'current-episode', onError: error => { throw error } })
  return { element, html: renderToStaticMarkup(element), downloads }
}

function elements(node, type) {
  return React.Children.toArray(node).flatMap(child => React.isValidElement(child)
    ? [...(child.type === type ? [child] : []), ...elements(child.props.children, type)] : [])
}

// Passing the saved event checks the real completed film reply without touching
// the project/API. The default keeps the same minimal shape for normal offline runs.
const savedEvent = process.argv[2] ? JSON.parse(readFileSync(process.argv[2], 'utf8')) : null
const deliveryText = savedEvent?.data.text ?? '**[打开带中文字幕成片]( /workspace/中文作品/film-subtitled.mp4)**\n[无字幕版]( /workspace/中文作品/film-clean.mp4)\n[中文字幕 SRT]( /workspace/中文作品/subtitles.srt)'

test('the completed film reply renders all three downloads, including the bold link, and keeps their source paths', async () => {
  const actual = render(deliveryText)
  const buttons = elements(actual.element, 'button')
  assert.equal(buttons.length, 3)
  assert.equal(elements(elements(actual.element, 'strong').map(node => node.props.children), 'button').length, 1)
  assert(!actual.html.includes('[打开带中文字幕成片]('))
  assert.equal(elements(actual.element, 'p').length, 1)
  for (const button of buttons) button.props.onClick()
  await Promise.resolve()
  const expectedPaths = [...deliveryText.matchAll(/\[[^\]]+\]\(\s*(\/[^)]+)\)/g)].map(match => match[1].trim())
  assert.equal(expectedPaths.length, 3)
  assert.deepEqual(actual.downloads, expectedPaths.map(path => ({ projectId: 'current-episode', path, name: path.split('/').at(-1) })))
  assert.deepEqual(actual.downloads.map(row => row.name), ['film-subtitled.mp4', 'film-clean.mp4', 'subtitles.srt'])
})

test('ordinary text and bold emphasis stay inline without manufacturing links', () => {
  assert.equal(render('前文 **已经完成**，后文。').html, '<p>前文 <strong>已经完成</strong>，后文。</p>')
})

test('plain and angle-wrapped local paths preserve Chinese and internal spaces', async () => {
  const actual = render('[普通](/workspace/中文/成片.mp4) [空白](  /workspace/中文 项目/成片 副本.mp4  ) [尖括号]( </workspace/中文 项目/字幕 文件.srt> )')
  const buttons = elements(actual.element, 'button')
  assert.equal(buttons.length, 3)
  for (const button of buttons) button.props.onClick()
  await Promise.resolve()
  assert.deepEqual(actual.downloads.map(row => [row.path, row.name]), [
    ['/workspace/中文/成片.mp4', '成片.mp4'],
    ['/workspace/中文 项目/成片 副本.mp4', '成片 副本.mp4'],
    ['/workspace/中文 项目/字幕 文件.srt', '字幕 文件.srt'],
  ])
})

test('HTTP links including bold content keep the existing external-link behavior', () => {
  const actual = render('[站点]( https://example.com/film?q=1 ) **[资料](<http://example.com/info>)**')
  const links = elements(actual.element, 'a')
  assert.deepEqual(links.map(link => [link.props.href, link.props.target, link.props.rel]), [
    ['https://example.com/film?q=1', '_blank', 'noreferrer'],
    ['http://example.com/info', '_blank', 'noreferrer'],
  ])
  assert.equal(elements(actual.element, 'button').length, 0)
  assert.equal(elements(elements(actual.element, 'strong').map(node => node.props.children), 'a').length, 1)
  assert.deepEqual(actual.downloads, [])
})
