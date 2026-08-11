import { describe, expect, test, beforeEach, afterEach } from 'bun:test'
import fs from 'fs'
import path from 'path'
import os from 'os'
import { parseEditBlocks, applyEditBlocks } from './index.js'

describe('Structured Diff Edit Format (SEARCH/REPLACE)', () => {
  let tmpDir: string

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'edit-format-test-'))
  })

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true })
  })

  test('parses valid SEARCH/REPLACE block', () => {
    const input = `
Here is the code change:

src/utils/math.ts
<<<<<<< SEARCH
function add(a: number, b: number): number {
  return a + b
}
=======
function add(a: number, b: number): number {
  return a + b + 0
}
>>>>>>> REPLACE
`
    const blocks = parseEditBlocks(input)
    expect(blocks.length).toBe(1)
    expect(blocks[0].filePath).toBe('src/utils/math.ts')
    expect(blocks[0].searchContent).toContain('return a + b')
    expect(blocks[0].replaceContent).toContain('return a + b + 0')
  })

  test('applies valid block to file', () => {
    const targetFile = path.join(tmpDir, 'test.txt')
    fs.writeFileSync(targetFile, 'line 1\nold line\nline 3', 'utf-8')

    const blocks = [
      {
        filePath: 'test.txt',
        searchContent: 'old line',
        replaceContent: 'new line',
      },
    ]

    const result = applyEditBlocks(blocks, tmpDir)
    expect(result.success).toBe(true)
    expect(result.appliedFiles).toContain('test.txt')

    const newContent = fs.readFileSync(targetFile, 'utf-8')
    expect(newContent).toBe('line 1\nnew line\nline 3')
  })

  test('fails with clear error on SEARCH content mismatch', () => {
    const targetFile = path.join(tmpDir, 'test.txt')
    fs.writeFileSync(targetFile, 'actual line 1\nactual line 2', 'utf-8')

    const blocks = [
      {
        filePath: 'test.txt',
        searchContent: 'nonexistent line',
        replaceContent: 'replaced',
      },
    ]

    const result = applyEditBlocks(blocks, tmpDir)
    expect(result.success).toBe(false)
    expect(result.errors.length).toBeGreaterThan(0)
    expect(result.errors[0]).toContain("SEARCH block mismatch in file 'test.txt'")
  })

  test('handles multiple blocks in the same file', () => {
    const targetFile = path.join(tmpDir, 'multi.txt')
    fs.writeFileSync(targetFile, 'const a = 1\nconst b = 2\nconst c = 3', 'utf-8')

    const blocks = [
      {
        filePath: 'multi.txt',
        searchContent: 'const a = 1',
        replaceContent: 'const a = 10',
      },
      {
        filePath: 'multi.txt',
        searchContent: 'const c = 3',
        replaceContent: 'const c = 30',
      },
    ]

    const result = applyEditBlocks(blocks, tmpDir)
    expect(result.success).toBe(true)

    const finalContent = fs.readFileSync(targetFile, 'utf-8')
    expect(finalContent).toBe('const a = 10\nconst b = 2\nconst c = 30')
  })

  test('creates new file when SEARCH block is empty or file does not exist', () => {
    const newFilePath = path.join(tmpDir, 'new_module.ts')

    const blocks = [
      {
        filePath: 'new_module.ts',
        searchContent: '',
        replaceContent: 'export const greeting = "hello world"',
        isNewFile: true,
      },
    ]

    const result = applyEditBlocks(blocks, tmpDir)
    expect(result.success).toBe(true)
    expect(result.createdFiles).toContain('new_module.ts')
    expect(fs.existsSync(newFilePath)).toBe(true)

    const content = fs.readFileSync(newFilePath, 'utf-8')
    expect(content).toBe('export const greeting = "hello world"')
  })
})
