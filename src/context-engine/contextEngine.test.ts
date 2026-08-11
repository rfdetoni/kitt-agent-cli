import { describe, expect, test } from 'bun:test'
import { getRelevantContext, extractFocusFromTask } from './index.js'
import { invalidateCache } from '../context/repoMap/index.js'

describe('Context Engine', () => {
  test('extractFocusFromTask identifies files and focus symbols', () => {
    const task = 'Fix email validation bug in src/utils/validation.ts for function parseEmail'
    const focus = extractFocusFromTask(task)
    expect(focus.focusFiles).toContain('src/utils/validation.ts')
    expect(focus.focusSymbols).toContain('parseEmail')
  })

  test('getRelevantContext returns formatted context blocks with signatures only', async () => {
    const root = process.cwd()
    invalidateCache(root)

    const blocks = await getRelevantContext(
      'Update query engine in QueryEngine.ts',
      1000,
      root,
    )

    expect(blocks.length).toBeGreaterThan(0)
    for (const block of blocks) {
      expect(block).toHaveProperty('path')
      expect(block).toHaveProperty('content')
      expect(block.content).toContain(`${block.path}:`)
      expect(block.content).toContain('⋮')
    }
  }, 30000)

  test('consecutive calls use cache and run faster', async () => {
    const root = process.cwd()
    invalidateCache(root)

    const start1 = performance.now()
    await getRelevantContext('Analyze project state', 2000, root)
    const duration1 = performance.now() - start1

    const start2 = performance.now()
    await getRelevantContext('Analyze project state', 2000, root)
    const duration2 = performance.now() - start2

    expect(duration2).toBeLessThanOrEqual(duration1)
  }, 30000)
})
