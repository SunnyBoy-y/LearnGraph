import { constants as fsConstants, copyFileSync, existsSync } from 'node:fs'
import path from 'node:path'

export function initializeEnvFile({ directory, label, repoRoot }) {
  const templatePath = path.join(directory, '.env.example')
  const targetPath = path.join(directory, '.env')
  if (existsSync(targetPath)) return false
  if (!existsSync(templatePath)) {
    throw new Error(`Required file is missing: ${path.relative(repoRoot, templatePath)}`)
  }

  try {
    copyFileSync(templatePath, targetPath, fsConstants.COPYFILE_EXCL)
    console.log(
      `Created ${path.relative(repoRoot, targetPath)} from ${path.relative(repoRoot, templatePath)}.`,
    )
    return true
  } catch (error) {
    // Two development processes may start at nearly the same time. The
    // exclusive copy guarantees that neither process overwrites an existing
    // user configuration.
    if (error?.code === 'EEXIST') return false
    throw new Error(
      `Could not initialize ${label} environment file: ${
        error instanceof Error ? error.message : String(error)
      }`,
    )
  }
}

export function initializeEnvFiles({ backendDir, frontendDir, repoRoot }) {
  return {
    frontendCreated: initializeEnvFile({
      directory: frontendDir,
      label: 'frontend',
      repoRoot,
    }),
    backendCreated: initializeEnvFile({
      directory: backendDir,
      label: 'backend',
      repoRoot,
    }),
  }
}
