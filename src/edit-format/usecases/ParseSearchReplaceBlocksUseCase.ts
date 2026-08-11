import type { EditBlock } from '../domain/entities.js'

export class ParseSearchReplaceBlocksUseCase {
  execute(text: string): EditBlock[] {
    const blocks: EditBlock[] = []
    
    const blockRegex = /(?:([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)\s*\n)?<<<<<<< SEARCH\r?\n([\s\S]*?)\r?\n=======\r?\n([\s\S]*?)\r?\n>>>>>>> REPLACE/g

    let match: RegExpExecArray | null
    while ((match = blockRegex.exec(text)) !== null) {
      let filePath = match[1]?.trim() || ''
      const searchContent = match[2]
      const replaceContent = match[3]

      if (!filePath) {
        const matchIndex = match.index
        const prefix = text.slice(0, matchIndex).trimEnd()
        const lines = prefix.split('\n')
        const lastLine = lines[lines.length - 1]?.trim() || ''
        if (lastLine && /^[a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+$/.test(lastLine)) {
          filePath = lastLine
        }
      }

      if (!filePath) {
        continue
      }

      const isNewFile = searchContent.length === 0
      const isDeletion = replaceContent.length === 0 && searchContent.length > 0

      blocks.push({
        filePath,
        searchContent,
        replaceContent,
        isNewFile,
        isDeletion,
      })
    }

    return blocks
  }
}
