"""Run the changed frontend against a private copy without reading local .env."""
import argparse
import json
from pathlib import Path
import shutil

from updater.tests.docker_isolation import OWNER, host_path, require, validate_fixture
from updater.tests.postgresql_roundtrip import FixtureAgent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--reuse', action='store_true')
    parser.add_argument('--refresh', action='store_true', help='refresh source in the explicitly owned build workspace')
    parser.add_argument('--preview', action='store_true', help='serve the verified copy only at 127.0.0.1:18252')
    parser.add_argument('--stop-preview', action='store_true')
    args = parser.parse_args()
    marker, _ = validate_fixture(args.root)
    agent = FixtureAgent(args.root, 'LearnGraph/UpdateFixture', marker['image'], ['compose.json'])
    agent.verify_isolation()
    repo = Path(__file__).resolve().parents[2]
    output = args.root.resolve() / 'frontend-validation'
    if args.stop_preview:
        item=json.loads(agent._raw(['inspect',marker['project']+'-frontend-preview']))[0]
        require(item['Config'].get('Labels',{}).get(OWNER)==marker['owner'] and item['Config']['Image']=='learngraph-sandbox:reader-preview-verify','foreign frontend worker')
        require(len(item['Mounts'])==1 and item['Mounts'][0]['Type']=='bind' and host_path(item['Mounts'][0]['Source'])==host_path(output),'foreign frontend mount')
        require(set(item['NetworkSettings']['Networks'])=={marker['project']+'_default'},'foreign frontend network')
        agent._raw(['rm','-f',item['Id']],validated=True)
        print('Owned frontend preview stopped')
        return
    require(output.resolve().is_relative_to(args.root.resolve()), 'private frontend output required')
    require(not args.refresh or args.reuse,'refresh requires existing ownership')
    if args.reuse:
        require((output/'.fixture-owner').is_file() and (output/'.fixture-owner').read_text()==marker['owner'], 'cannot adopt unmarked frontend workspace')
    else:
        require(not output.exists(), 'fresh private frontend output required')
        output.mkdir(mode=0o700)
        (output/'.fixture-owner').write_text(marker['owner'])
    # Only source/public/config allowlist; never copy .env, host node_modules,
    # backend, tokens or any local data directories into the frontend worker.
    for name in ['src', 'public']:
        require(all(not path.is_symlink() and path.resolve().is_relative_to(repo/'frontend'/name) for path in (repo/'frontend'/name).rglob('*')), 'frontend source symlink escapes refused')
        if not args.reuse:
            shutil.copytree(repo/'frontend'/name, output/name, symlinks=False)
        else:
            if args.refresh:
                require(all(not item.is_symlink() and item.resolve().is_relative_to(output) for item in (output/name).rglob('*')),'copied source symlinks refused before refresh')
                shutil.copytree(repo/'frontend'/name,output/name,dirs_exist_ok=True)
            for path in (repo/'frontend'/name).rglob('*'):
                if path.is_file():
                    copied = output/path.relative_to(repo/'frontend')
                    require(not copied.is_symlink() and copied.read_bytes()==path.read_bytes(), 'copied frontend source differs; create a fresh workspace')
    for pattern in ['package.json', 'package-lock.json', 'tsconfig*.json', 'vite.config.ts', 'index.html']:
        for source in (repo/'frontend').glob(pattern):
            if not args.reuse:
                shutil.copy2(source, output/source.name)
            else:
                if args.refresh: shutil.copy2(source,output/source.name)
                require((output/source.name).read_bytes()==source.read_bytes(), 'copied frontend configuration differs')
    commands = ("" if args.reuse else "node --version && npm ci --no-audit --no-fund && ") + "node node_modules/vitest/vitest.mjs run src/lib/backup-download.test.ts src/features/settings/site-backup-panel.test.tsx && node node_modules/typescript/bin/tsc -b && node node_modules/vite/bin/vite.js build"
    require(not list(output.glob('.env*')), 'frontend validation must never load environment files')
    if args.preview:
        require(args.reuse, 'preview requires an explicitly owned existing workspace')
        commands = 'node node_modules/vite/bin/vite.js preview --host 0.0.0.0 --port 5173 --strictPort'
    options = ['run','--rm','--read-only','--cap-drop','ALL','--memory','6g','--env','NODE_OPTIONS=--max-old-space-size=4608','--tmpfs','/tmp:rw,exec,nosuid,nodev', '--network',marker['project']+'_default','--label',OWNER+'='+marker['owner'],'--mount','type=bind,source='+str(output)+',target=/workspace','--workdir','/workspace','--entrypoint','sh','learngraph-sandbox:reader-preview-verify','-c',commands]
    if args.preview:
        options[2:2] = ['--detach','--name',marker['project']+'-frontend-preview','--publish','127.0.0.1:18252:5173','--env','LEARNGRAPH_BACKEND_ORIGIN=http://app:8000']
    try:
        result = agent._raw(options,1800,validated=True)
        if args.preview:
            (args.root/'frontend-preview.json').write_text(json.dumps({'container':result.strip(),'url':'http://127.0.0.1:18252'}),encoding='utf-8')
            print('Owned frontend preview: http://127.0.0.1:18252',flush=True)
        else:
            (args.root/'frontend-validation.log').write_text(result,encoding='utf-8')
            print(json.dumps({'passed':True,'log':str(args.root/'frontend-validation.log'),'tail':result.splitlines()[-2:]},ensure_ascii=False),flush=True)
    finally:
        agent.verify_isolation()


if __name__ == '__main__': main()
