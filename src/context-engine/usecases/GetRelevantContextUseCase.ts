import type { ContextBlock, TaskFocus } from '../domain/entities.js'
import { ExtractTaskFocusUseCase } from './ExtractTaskFocusUseCase.js'
import { extractTagsWithCache } from '../../context/repoMap/index.js'
import { loadCache } from '../../context/repoMap/cache.js'
import { getRepoFiles } from '../../context/repoMap/gitFiles.js'
import { buildGraph } from '../../context/repoMap/graph.js'
import { rankFiles } from '../../context/repoMap/pagerank.js'
import { countTokens } from '../../context/repoMap/tokenize.js'
import type { FileTags } from '../domain/entities.js'

export class GetRelevantContextUseCase {
  constructor(
    private readonly focusExtractor: ExtractTaskFocusUseCase = new ExtractTaskFocusUseCase(),
  ) {}

  async execute(
    taskDescription: string,
    maxTokens = 2048,
    root: string = process.cwd(),
  ): Promise<ContextBlock[]> {
    const focus: TaskFocus = this.focusExtractor.execute(taskDescription)

    const repoFiles = await getRepoFiles(root)
    const cache = loadCache(root)

    const allFileTags = await extractTagsWithCache({
      files: repoFiles,
      root,
      cache,
    })

    const symbolSet = new Set(focus.focusSymbols)
    const matchedSymbolFiles: string[] = []
    for (const ft of allFileTags) {
      if (ft.tags.some(t => t.kind === 'def' && symbolSet.has(t.name))) {
        matchedSymbolFiles.push(ft.path)
      }
    }

    const combinedFocusFiles = Array.from(
      new Set([...focus.focusFiles, ...matchedSymbolFiles]),
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
}
