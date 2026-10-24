// ESLint on the JavaScript of the production STONE Home page (it lives inline in index.html).
// Errors fail: undefined names, redeclarations, duplicate keys, unreachable code.
import { readFileSync } from 'node:fs';
import { ESLint } from 'eslint';
import globals from 'globals';

const page = new URL('../../system_files/usr/share/stone-home/index.html', import.meta.url);
const html = readFileSync(page, 'utf8');
const code = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
const eslint = new ESLint({
  overrideConfigFile: true,
  overrideConfig: [{
    languageOptions: { ecmaVersion: 2022, sourceType: 'script', globals: { ...globals.browser } },
    rules: { 'no-undef': 'error', 'no-redeclare': 'error', 'no-dupe-keys': 'error', 'no-unreachable': 'error',
      'no-unused-vars': ['warn', { args: 'none', caughtErrors: 'none' }], 'no-const-assign': 'error' }
  }]
});
const [res] = await eslint.lintText(code, { filePath: 'index.html.js' });
for (const m of res.messages) console.log(`${m.severity === 2 ? 'error' : 'warn '} ${m.line}:${m.column} ${m.message} (${m.ruleId})`);
console.log(`eslint: ${res.errorCount} errors, ${res.warningCount} warnings`);
process.exit(res.errorCount ? 1 : 0);
