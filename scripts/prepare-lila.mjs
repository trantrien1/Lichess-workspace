// Run inside the Linux UI container. Windows Git may have checked out symlinks as text files.
import { execFileSync } from 'node:child_process';
import { lstatSync, readFileSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, resolve, sep } from 'node:path';

const root = process.cwd();
const entries = execFileSync('git', ['ls-tree', '-rz', 'HEAD'], { maxBuffer: 8 * 1024 * 1024 })
  .toString().split('\0').filter(Boolean);
let links = 0;
let normalized = 0;
for (const entry of entries) {
  const [metadata, path] = entry.split('\t');
  const [mode, , object] = metadata.split(' ');
  const absolute = resolve(root, path);
  if (!absolute.startsWith(root + sep)) throw new Error(`Unexpected path: ${path}`);
  if (mode === '120000' && !lstatSync(absolute).isSymbolicLink()) {
    const target = execFileSync('git', ['cat-file', 'blob', object]).toString();
    if (!resolve(dirname(absolute), target).startsWith(root + sep))
      throw new Error(`Link leaves checkout: ${path}`);
    if (readFileSync(absolute, 'utf8').trim() !== target.trim())
      throw new Error(`Refusing to replace modified file: ${path}`);
    unlinkSync(absolute);
    symlinkSync(target, absolute);
    links++;
  } else if (
    mode === '100755' || /\.(scala|sbt)$/.test(path) ||
    ['.node-version', '.sbtopts.default'].includes(path)
  ) {
    const content = readFileSync(absolute);
    if (!content.includes(0) && content.includes(Buffer.from('\r\n'))) {
      writeFileSync(absolute, content.toString().replaceAll('\r\n', '\n'));
      normalized++;
    }
  }
}
console.log(`Linux checkout prepared: ${links} symlinks, ${normalized} text files normalized.`);
