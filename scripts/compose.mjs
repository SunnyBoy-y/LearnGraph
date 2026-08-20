#!/usr/bin/env node
// LearnGraph Docker Compose 统一入口：先确保仓库根目录 .env 存在，再透传
// `docker compose <args>`。
//
// 背景：原生 `docker compose up` 不会自动从 .env.example 创建 .env（Compose
// 只读 .env，缺失时静默使用 docker-compose.yml 里的 ${VAR:-default} 默认值）。
// 本入口在每次调用前幂等生成根目录 .env（已存在则绝不动用户配置），使
// “第一次 docker compose up 就有一份可编辑的 .env”成立。
//
// 用法：
//   node scripts/compose.mjs up -d --build
//   node scripts/compose.mjs down
//   node scripts/compose.mjs logs -f app
//   node scripts/compose.mjs -f docker-compose.sandbox.yml up -d
// 或经 npm 快捷方式（见 package.json scripts）：
//   npm run docker:up      # = node scripts/compose.mjs up -d --build
//   npm run docker -- down # = node scripts/compose.mjs down

import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { initializeEnvFile } from './init-env.mjs'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

// 幂等：仅当根目录 .env 不存在时从 .env.example 复制；已存在或并发创建时零副作用。
const created = initializeEnvFile({ directory: repoRoot, label: 'root', repoRoot })

if (created) {
  console.log('')
  console.log('📝 已自动生成根目录 .env（模板来自 .env.example，全部为注释掉的默认值）。')
  console.log('   如需自定义（端口、LEARNGRAPH_HOST_ACCESS_MODE、LEARNGRAPH_MASTER_KEY 等），')
  console.log('   请编辑 .env 后重新运行；不编辑则继续使用 docker-compose.yml 内置默认值。')
  console.log('')
}

const args = process.argv.slice(2)
const result = spawnSync('docker', ['compose', ...args], {
  cwd: repoRoot,
  stdio: 'inherit',
})

if (result.error) {
  console.error(`[compose] 无法执行 docker compose：${result.error.message}`)
  process.exit(1)
}
process.exit(result.status ?? 1)
