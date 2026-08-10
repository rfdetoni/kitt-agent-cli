import { buildRepoMap, extractTagsWithCache } from '../context/repoMap/index.js'
import { loadCache } from '../context/repoMap/cache.js'
import { getRepoFiles } from '../context/repoMap/gitFiles.js'
import { buildGraph } from '../context/repoMap/graph.js'
import { rankFiles } from '../context/repoMap/pagerank.js'
import { countTokens } from '../context/repoMap/tokenize.js'
import type { FileTags, Tag } from '../context/repoMap/types.js'

export interface ContextBlock {
  path: string
  content: string
}

export function extractFocusFromTask(taskDescription: string): {
  focusFiles: string[]
  focusSymbols: string[]
} {
  if (!taskDescription) {
    return { focusFiles: [], focusSymbols: [] }
  }

  // Match file paths (e.g., src/query.ts, main.py, components/Button.tsx)
  const filePathRegex = /[a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+/g
  const matchedFiles = Array.from(new Set(taskDescription.match(filePathRegex) || []))

  // Match identifier symbols (e.g. function/class names like getRelevantContext, QueryEngine)
  const identifierRegex = /\b[A-Za-z_$][A-Za-z0-9_$]{2,}\b/g
  const matchedIdentifiers = Array.from(
    new Set(taskDescription.match(identifierRegex) || []),
  )

  // Filter out common English/programming stopwords
  const stopwords = new Set([
    'the', 'and', 'for', 'that', 'with', 'this', 'from', 'have', 'file',
    'function', 'class', 'method', 'add', 'create', 'update', 'fix', 'remove',
    'delete', 'change', 'make', 'use', 'code', 'task', 'test', 'repo',
  ])

  const focusSymbols = matchedIdentifiers.filter(
    id => !stopwords.has(id.toLowerCase()),
  )

  return {
    focusFiles: matchedFiles,
    focusSymbols,
  }
}

/**
 * Get relevant structural context blocks for a task, ordered by relevance and token budget.
 */
export async function getRelevantContext(
  taskDescription: string,
  maxTokens = 2048,
  root: string = process.cwd(),
): Promise<ContextBlock[]> {
  const { focusFiles, focusSymbols } = extractFocusFromTask(taskDescription)

  const repoFiles = await getRepoFiles(root)
  const cache = loadCache(root)

  const allFileTags = await extractTagsWithCache({
    files: repoFiles,
    root,
    cache,
  })

  // Resolve focus files matching symbol names in task description
  const symbolSet = new Set(focusSymbols)
  const matchedSymbolFiles: string[] = []
  for (const ft of allFileTags) {
    if (ft.tags.some(t => t.kind === 'def' && symbolSet.has(t.name))) {
      matchedSymbolFiles.push(ft.path)
    }
  }

  const combinedFocusFiles = Array.from(
    new Set([...focusFiles, ...matchedSymbolFiles]),
  )

  const graph = buildGraph(allFileTags)
  const ranked = rankFiles(graph, combinedFocusFiles)

  const fileTagsMap = new Map<string, FileTags>()
  for (const ft of allFileTags) {
    fileTagsMap.set(ft.path, ft)
  }

  const blocks: ContextBlock[] = []
  let currentTokens = 0

  for (const { path } of ranked) {
    const ft = fileTagsMap.get(path)
    if (!ft) continue

    const defs = ft.tags
      .filter(t => t.kind === 'def')
      .sort((a, b) => a.line - b.line)

    if (defs.length === 0) continue

    const lines: string[] = [`${path}:`]
    let lastLine = 0
    for (const def of defs) {
      if (def.line > lastLine + 1) {
        lines.push('⋮')
      }
      lines.push(`  ${def.signature}`)
      lastLine = def.line
    }
    lines.push('⋮')
    const content = lines.join('\n')
    const blockTokens = countTokens(content)

    if (currentTokens + blockTokens > maxTokens) {
      continue
    }

    blocks.push({ path, content })
    currentTokens += blockTokens
  }

  return blocks
}
