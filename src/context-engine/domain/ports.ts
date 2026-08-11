import type { FileTags, ContextBlock, TaskFocus } from './entities.js'

export interface SymbolExtractorPort {
  extractTags(filePath: string, root: string): Promise<FileTags | null>
}

export interface FocusExtractorPort {
  extractFocus(taskDescription: string): TaskFocus
}

export interface ContextRankerPort {
  rankAndBudget(
    allFileTags: FileTags[],
    focus: TaskFocus,
    maxTokens: number,
  ): ContextBlock[]
}
