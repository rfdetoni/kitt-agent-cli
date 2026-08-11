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

export interface PostEditValidationResult {
  valid: boolean
  output?: string
  error?: string
}
