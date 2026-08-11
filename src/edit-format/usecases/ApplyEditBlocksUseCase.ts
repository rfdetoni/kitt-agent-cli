import fs from 'fs'
import path from 'path'
import type { EditBlock, EditApplyResult } from '../domain/entities.js'

export class ApplyEditBlocksUseCase {
  execute(
    blocks: EditBlock[],
    root: string = process.cwd(),
  ): EditApplyResult {
    const appliedFiles: string[] = []
    const createdFiles: string[] = []
    const deletedFiles: string[] = []
    const errors: string[] = []

    if (blocks.length === 0) {
      return {
        success: false,
        appliedFiles: [],
        createdFiles: [],
        deletedFiles: [],
        errors: ['No valid SEARCH/REPLACE blocks found in output.'],
      }
    }

    for (const block of blocks) {
      const fullPath = path.resolve(root, block.filePath)
      const fileExists = fs.existsSync(fullPath)

      if (block.isNewFile || !fileExists) {
        continue
      }

      const currentContent = fs.readFileSync(fullPath, 'utf-8')
      if (!currentContent.includes(block.searchContent)) {
        errors.push(
          `SEARCH block mismatch in file '${block.filePath}'. Expected exact trecho:\n---\n${block.searchContent}\n---`,
        )
      }
    }

    if (errors.length > 0) {
      return {
        success: false,
        appliedFiles: [],
        createdFiles: [],
        deletedFiles: [],
        errors,
      }
    }

    for (const block of blocks) {
      const fullPath = path.resolve(root, block.filePath)
      const fileExists = fs.existsSync(fullPath)

      if (block.isDeletion) {
        if (fileExists) {
          fs.unlinkSync(fullPath)
          deletedFiles.push(block.filePath)
        }
        continue
      }

      if (!fileExists || block.isNewFile) {
        fs.mkdirSync(path.dirname(fullPath), { recursive: true })
        fs.writeFileSync(fullPath, block.replaceContent, 'utf-8')
        createdFiles.push(block.filePath)
      } else {
        const currentContent = fs.readFileSync(fullPath, 'utf-8')
        const updatedContent = currentContent.replace(
          block.searchContent,
          block.replaceContent,
        )
        fs.writeFileSync(fullPath, updatedContent, 'utf-8')
        appliedFiles.push(block.filePath)
      }
    }

    return {
      success: true,
      appliedFiles,
      createdFiles,
      deletedFiles,
      errors: [],
    }
  }
}
