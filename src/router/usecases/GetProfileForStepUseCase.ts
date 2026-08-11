import type {
  ModelBackendProfile,
  RouterConfig,
  TaskStepContext,
  TaskType,
} from '../domain/entities.js'
import { ClassifyTaskStepUseCase } from './ClassifyTaskStepUseCase.js'
import { DEFAULT_ROUTER_CONFIG } from './LoadRouterConfigUseCase.js'

export class GetProfileForStepUseCase {
  constructor(
    private readonly classifier: ClassifyTaskStepUseCase = new ClassifyTaskStepUseCase(),
  ) {}

  execute(
    step: TaskStepContext,
    config: RouterConfig = DEFAULT_ROUTER_CONFIG,
  ): { taskType: TaskType; profileName: string; profile: ModelBackendProfile } {
    const taskType = this.classifier.execute(step)
    const profileName = config.routing[taskType] || 'execute'
    const profile = config.profiles[profileName] || config.profiles.execute
    return { taskType, profileName, profile }
  }
}
