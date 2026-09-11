const path = require('node:path')
const { execFileSync } = require('node:child_process')

module.exports = async function checkStudioPackage(context) {
  // macOS ships this interpreter; the development venv is not the app runtime.
  if (context.electronPlatformName !== 'darwin') return
  const root = path.resolve(__dirname, '..')
  const python = path.join(root, 'python-embed', 'bin', 'python3')
  execFileSync(python, ['-c', [
    'import sys',
    'sys.path.insert(0, sys.argv[1])',
    'from short_drama.audio_inspection import normalize_dialogue_text',
    "actual = normalize_dialogue_text('學生說：他沒按計算器。')",
    "assert actual == '学生说他没按计算器', actual",
    "print('Studio packaged audio runtime: ready')",
  ].join('\n'), path.join(root, 'backend')], { stdio: 'inherit' })
}
