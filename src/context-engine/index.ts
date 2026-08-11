import { ExtractTaskFocusUseCase } from './usecases/ExtractTaskFocusUseCase.js'
import { GetRelevantContextUseCase } from './usecases/GetRelevantContextUseCase.js'
import type { ContextBlock, TaskFocus } from './domain/entities.js'

export type { ContextBlock, TaskFocus, FileTags, Tag } from './domain/entities.js'
export { ExtractTaskFocusUseCase } from './usecases/ExtractTaskFocusUseCase.js'
export { GetRelevantContextUseCase } from './usecases/GetRelevantContextUseCase.js'

const defaultFocusExtractor = new ExtractTaskFocusUseCase()
const defaultGetRelevantContextUseCase = new GetRelevantContextUseCase(defaultFocusExtractor)

export function extractFocusFromTask(taskDescription: string): TaskFocus {
  return defaultFocusExtractor.execute(taskDescription)
}

export async function getRelevantContext(
  taskDescription: string,
  maxTokens = 2048,
  root: string = process.cwd(),
): Promise<ContextBlock[]> {
  return defaultGetRelevantContextUseCase.execute(taskDescription, maxTokens, root)
}
