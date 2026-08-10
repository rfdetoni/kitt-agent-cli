import fs from 'fs'
import path from 'path'
import { execSync } from 'child_process'

export interface EditBlock {
  filePath: string
  searchContent: string
  replaceContent: string
  isNewFile?: boolean
  isDeletion?: boolean
}

export interface EditApplyResult {
  success: boolean
  appliedFiles: string[]
  createdFiles: string[]
  deletedFiles: string[]
  errors: string[]
}

/**
 * Parse SEARCH/REPLACE blocks from model output string.
 */
export function parseEditBlocks(text: string): EditBlock[] {
  const blocks: EditBlock[] = []
  
  // Regex matches optional filename line followed by SEARCH/REPLACE tags
  // Example:
  // src/utils/foo.ts
  // <<<<<<< SEARCH
  // old
  // =======
  // new
  // >>>>>>> REPLACE
  const blockRegex = /(?:([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)\s*\n)?<<<<<<< SEARCH\r?\n([\s\S]*?)\r?\n=======\r?\n([\s\S]*?)\r?\n>>>>>>> REPLACE/g

  let match: RegExpExecArray | null
  while ((match = blockRegex.exec(text)) !== null) {
    let filePath = match[1]?.trim() || ''
    const searchContent = match[2]
    const replaceContent = match[3]

    // If filename wasn't right above <<<<<<< SEARCH, look back a line before match
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

/**
 * Apply parsed SEARCH/REPLACE edit blocks to disk.
 * Validates exact match of SEARCH block before writing.
 */
export function applyEditBlocks(
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

  // Pre-validate all blocks before writing any changes
  for (const block of blocks) {
    const fullPath = path.resolve(root, block.filePath)
    const fileExists = fs.existsSync(fullPath)

    if (block.isNewFile || !fileExists) {
      // New file creation expected
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

  // Apply edits
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

export interface PostEditValidationResult {
  valid: boolean
  output?: string
  error?: string
}

/**
 * Optional post-edit validation (runs project linter/tests if available).
 */
export function validatePostEdits(
  root: string = process.cwd(),
): PostEditValidationResult {
  const packageJsonPath = path.join(root, 'package.json')
  if (!fs.existsSync(packageJsonPath)) {
    return { valid: true }
  }

  try {
    const pkg = JSON.parse(fs.readFileSync(packageJsonPath, 'utf-8'))
    const scripts = pkg.scripts || {}

    let commandToRun = ''
    if (scripts.typecheck) {
      commandToRun = 'npm run typecheck'
    } else if (scripts.lint) {
      commandToRun = 'npm run lint'
    }

    if (!commandToRun) {
      return { valid: true }
    }

    const output = execSync(commandToRun, { cwd: root, encoding: 'utf-8' })
    return { valid: true, output }
  } catch (err: unknown) {
    const errorMessage = err instanceof Error ? err.message : String(err)
    return {
      valid: false,
      error: `Post-edit validation failed (${errorMessage})`,
    }
  }
}
