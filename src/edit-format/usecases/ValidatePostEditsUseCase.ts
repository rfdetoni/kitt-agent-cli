import fs from 'fs'
import path from 'path'
import { execSync } from 'child_process'
import type { PostEditValidationResult } from '../domain/entities.js'

export class ValidatePostEditsUseCase {
  execute(root: string = process.cwd()): PostEditValidationResult {
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
}
