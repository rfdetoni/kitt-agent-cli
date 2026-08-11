import { ClassifyTaskStepUseCase } from './usecases/ClassifyTaskStepUseCase.js'
import {
  DEFAULT_ROUTER_CONFIG,
  LoadRouterConfigUseCase,
} from './usecases/LoadRouterConfigUseCase.js'
import { GetProfileForStepUseCase } from './usecases/GetProfileForStepUseCase.js'
import type {
  ModelBackendProfile,
  RouterConfig,
  TaskStepContext,
  TaskType,
} from './domain/entities.js'

export type {
  TaskType,
  ModelBackendProfile,
  RouterConfig,
  TaskStepContext,
} from './domain/entities.js'

export { ClassifyTaskStepUseCase } from './usecases/ClassifyTaskStepUseCase.js'
export { LoadRouterConfigUseCase, DEFAULT_ROUTER_CONFIG } from './usecases/LoadRouterConfigUseCase.js'
export { GetProfileForStepUseCase } from './usecases/GetProfileForStepUseCase.js'

const defaultClassifier = new ClassifyTaskStepUseCase()
const defaultLoader = new LoadRouterConfigUseCase()
const defaultGetProfile = new GetProfileForStepUseCase(defaultClassifier)

export function loadRouterConfig(root: string = process.cwd()): RouterConfig {
  return defaultLoader.execute(root)
}

export function classifyTaskStep(step: TaskStepContext): TaskType {
  return defaultClassifier.execute(step)
}

export function getProfileForStep(
  step: TaskStepContext,
  config: RouterConfig = DEFAULT_ROUTER_CONFIG,
): { taskType: TaskType; profileName: string; profile: ModelBackendProfile } {
  return defaultGetProfile.execute(step, config)
}
