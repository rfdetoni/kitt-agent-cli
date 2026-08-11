import type { TaskFocus } from '../domain/entities.js'

const COMMON_STOPWORDS = new Set([
  'the', 'and', 'for', 'that', 'with', 'this', 'from', 'have', 'file',
  'function', 'class', 'method', 'add', 'create', 'update', 'fix', 'remove',
  'delete', 'change', 'make', 'use', 'code', 'task', 'test', 'repo',
])

export class ExtractTaskFocusUseCase {
  execute(taskDescription: string): TaskFocus {
    if (!taskDescription) {
      return { focusFiles: [], focusSymbols: [] }
    }

    const filePathRegex = /[a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+/g
    const matchedFiles = Array.from(new Set(taskDescription.match(filePathRegex) || []))

    const identifierRegex = /\b[A-Za-z_$][A-Za-z0-9_$]{2,}\b/g
    const matchedIdentifiers = Array.from(
      new Set(taskDescription.match(identifierRegex) || []),
    )

    const focusSymbols = matchedIdentifiers.filter(
      id => !COMMON_STOPWORDS.has(id.toLowerCase()),
    )

    return {
      focusFiles: matchedFiles,
      focusSymbols,
    }
  }
}
