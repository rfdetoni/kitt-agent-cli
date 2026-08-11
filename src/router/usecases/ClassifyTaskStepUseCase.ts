import type { TaskStepContext, TaskType } from '../domain/entities.js'

export class ClassifyTaskStepUseCase {
  execute(step: TaskStepContext): TaskType {
    const tool = (step.toolName || '').toLowerCase()
    const cmd = (step.command || '').toLowerCase()

    if (
      tool.includes('read') ||
      tool.includes('grep') ||
      tool.includes('glob') ||
      tool.includes('list')
    ) {
      return 'context-gather'
    }

    if (
      tool.includes('edit') ||
      tool.includes('write') ||
      tool.includes('replace')
    ) {
      return 'code-edit'
    }

    if (
      cmd.includes('test') ||
      cmd.includes('lint') ||
      cmd.includes('typecheck') ||
      tool.includes('validate')
    ) {
      return 'validate-diff'
    }

    if (step.prompt && (step.prompt.includes('summary') || step.prompt.includes('map'))) {
      return 'summarize'
    }

    return 'code-generation'
  }
}
