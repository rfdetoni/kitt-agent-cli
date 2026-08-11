import { ParseSearchReplaceBlocksUseCase } from './usecases/ParseSearchReplaceBlocksUseCase.js'
import { ApplyEditBlocksUseCase } from './usecases/ApplyEditBlocksUseCase.js'
import { ValidatePostEditsUseCase } from './usecases/ValidatePostEditsUseCase.js'
import type { EditBlock, EditApplyResult, PostEditValidationResult } from './domain/entities.js'

export type { EditBlock, EditApplyResult, PostEditValidationResult } from './domain/entities.js'
export { ParseSearchReplaceBlocksUseCase } from './usecases/ParseSearchReplaceBlocksUseCase.js'
export { ApplyEditBlocksUseCase } from './usecases/ApplyEditBlocksUseCase.js'
export { ValidatePostEditsUseCase } from './usecases/ValidatePostEditsUseCase.js'

const defaultParser = new ParseSearchReplaceBlocksUseCase()
const defaultApplier = new ApplyEditBlocksUseCase()
const defaultValidator = new ValidatePostEditsUseCase()

export function parseEditBlocks(text: string): EditBlock[] {
  return defaultParser.execute(text)
}

export function applyEditBlocks(
  blocks: EditBlock[],
  root: string = process.cwd(),
): EditApplyResult {
  return defaultApplier.execute(blocks, root)
}

export function validatePostEdits(
  root: string = process.cwd(),
): PostEditValidationResult {
  return defaultValidator.execute(root)
}
