#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');

const projectRoot = path.resolve(process.argv[2] || process.cwd());
const repoRoot = __dirname;
const config = require(path.join(repoRoot, 'config_data.json'));
const expected = config.bmad.skills;
const expectedVersion = config.bmad.version;
const bmadRoot = path.join(projectRoot, '_bmad');
const manifestPath = path.join(bmadRoot, '_config', 'manifest.yaml');
const skillManifestPath = path.join(bmadRoot, '_config', 'skill-manifest.csv');
const skillsRoot = path.join(projectRoot, '.agents', 'skills');

function fail(message) {
  console.error(`BMAD validation failed: ${message}`);
  process.exit(1);
}

for (const requiredPath of [manifestPath, skillManifestPath, skillsRoot]) {
  if (!fs.existsSync(requiredPath)) fail(`missing ${requiredPath}`);
}

const manifest = fs.readFileSync(manifestPath, 'utf8');
const escapedVersion = expectedVersion.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
if (!new RegExp(`^\\s*version:\\s*${escapedVersion}\\s*$`, 'm').test(manifest)) {
  fail(`version ${expectedVersion} is not recorded`);
}
if (!/^\s*- name:\s*core\s*$/m.test(manifest) || !/^\s*- name:\s*bmm\s*$/m.test(manifest)) {
  fail('core and bmm modules are required');
}
if (!/^\s*- opencode\s*$/m.test(manifest)) fail('OpenCode integration is not recorded');

const manifestIds = fs
  .readFileSync(skillManifestPath, 'utf8')
  .split(/\r?\n/)
  .slice(1)
  .filter(Boolean)
  .map((line) => {
    const match = line.match(/^"([^"]+)"/);
    if (!match) fail(`invalid skill manifest row: ${line}`);
    return match[1];
  });

const installedIds = fs
  .readdirSync(skillsRoot, { withFileTypes: true })
  .filter((entry) => entry.isDirectory() && entry.name.startsWith('bmad-'))
  .filter((entry) => fs.existsSync(path.join(skillsRoot, entry.name, 'SKILL.md')))
  .map((entry) => entry.name)
  .sort();

const expectedIds = [...expected].sort();
const manifestSorted = [...manifestIds].sort();
for (const [label, actual] of [['manifest', manifestSorted], ['installed skills', installedIds]]) {
  const missing = expectedIds.filter((id) => !actual.includes(id));
  const extra = actual.filter((id) => !expectedIds.includes(id));
  if (missing.length || extra.length) {
    fail(`${label} differs (missing: ${missing.join(', ') || 'none'}; extra: ${extra.join(', ') || 'none'})`);
  }
}

console.log(`BMAD validation passed: ${expectedIds.length} skills, version ${expectedVersion}, target ${projectRoot}`);
