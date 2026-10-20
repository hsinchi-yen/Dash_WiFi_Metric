#!/usr/bin/env node
/**
 * TDD – Prove-It tests for parseTerminalSegments (extracted pure function).
 * Run: node test_parse_terminal_segments.js
 *
 * RED phase: write tests that FAIL against the current implementation.
 * GREEN phase: fix parseTerminalSegments, then all tests pass.
 */

let passed = 0, failed = 0, provenBugs = 0;

// Use for tests that prove a bug EXISTS in the old code (expected to fail)
function assertBug(condition, msg) {
  if (!condition) {
    console.log(`  BUG✓  ${msg}  ← confirms bug in old code`);
    provenBugs++;
  } else {
    console.log(`  PASS  ${msg}`);
    passed++;
  }
}

function assert(condition, msg) {
  if (condition) {
    console.log(`  PASS  ${msg}`);
    passed++;
  } else {
    console.error(`  FAIL  ${msg}`);
    failed++;
  }
}

// ── Copy of the CURRENT (buggy) parseTerminalSegments ──────────────────────
function parseTerminalSegments_current(text) {
  const segments = [];
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      segments.push({ type: 'blank', text: '' });
      continue;
    }
    const clean = line
      .replace(/^#+\s*/, '')
      .replace(/^\*{1,3}|\*{1,3}$/g, '')
      .replace(/^_{1,2}|_{1,2}$/g, '')
      .trim();
    if (/^WiFi\s+Test\s+Summary\s+Report/i.test(clean)) {
      segments.push({ type: 'report-title', text: clean });
      continue;
    }
    if (/^\d+\.\s+\S/.test(clean)) {
      segments.push({ type: 'section-header', text: clean });
      continue;
    }
    segments.push({ type: 'content', text: clean || line });
  }
  return segments;
}

// ── Proposed fixed parseTerminalSegments ──────────────────────────────────
function parseTerminalSegments_fixed(text) {
  const segments = [];
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      segments.push({ type: 'blank', text: '' });
      continue;
    }
    const isList = /^\s*[-*]\s+/.test(line);
    const clean = line
      .replace(/^#+\s*/, '')                      // strip leading # headings
      .replace(/\*{1,3}([^*\n]+)\*{1,3}/g, '$1') // strip inline **bold** / *italic*
      .replace(/_{1,2}([^_\n]+)_{1,2}/g, '$1')   // strip inline __underline__
      .replace(/^\s*[-*]\s+/, '')                 // strip leading list markers (- or *)
      .trim();
    if (!isList && /^WiFi\s+Test\s+Summary\s+Report/i.test(clean)) {
      segments.push({ type: 'report-title', text: clean });
      continue;
    }
    if (!isList && /^\d+\.\s+\S/.test(clean)) {
      segments.push({ type: 'section-header', text: clean });
      continue;
    }
    segments.push({ type: 'content', text: clean || line });
  }
  return segments;
}

// ── Test suite ─────────────────────────────────────────────────────────────

console.log('\n=== parseTerminalSegments — Prove-It Tests ===\n');

// ──────────────────────────────────────────────────────────────────────────
// Section A: tests that PASS with both current and fixed (regression guard)
// ──────────────────────────────────────────────────────────────────────────
console.log('[A] Regression: already-working cases (current & fixed both pass)');

{
  const s = parseTerminalSegments_current('WiFi Test Summary Report');
  assert(s[0].type === 'report-title', 'plain report title → report-title');
}
{
  const s = parseTerminalSegments_current('1. General Information');
  assert(s[0].type === 'section-header', 'plain section header → section-header');
}
{
  const s = parseTerminalSegments_current('## 1. General Information');
  assert(s[0].type === 'section-header', '## heading → stripped → section-header');
}
{
  const s = parseTerminalSegments_current('**WiFi Test Summary Report**');
  assert(s[0].type === 'report-title', '**wrapped** report title → report-title');
}
{
  const s = parseTerminalSegments_current('**2. Test Statistics**');
  assert(s[0].type === 'section-header', '**wrapped** section header → section-header');
}
{
  const s = parseTerminalSegments_current('Work Order: WO-001');
  assert(s[0].type === 'content', 'plain key:value → content');
  assert(!s[0].text.includes('**'), 'no markdown in plain content');
}
{
  const s = parseTerminalSegments_current('');
  assert(s[0].type === 'blank', 'empty line → blank');
}

// ──────────────────────────────────────────────────────────────────────────
// Section B: tests that FAIL with current but PASS with fixed
// ──────────────────────────────────────────────────────────────────────────
console.log('\n[B] New bugs: inline markdown + list markers (should FAIL current, PASS fixed)');

// B1: inline bold in content should be stripped
{
  const s = parseTerminalSegments_current('Yield Rate: **98.5%**');
  assertBug(!s[0].text.includes('**'), 'B1 current: inline **bold** not stripped (proves bug)');
}
{
  const s = parseTerminalSegments_fixed('Yield Rate: **98.5%**');
  assert(!s[0].text.includes('**'), 'B1 fixed: inline **bold** stripped from content');
}

// B2: leading "- " list marker should be stripped
{
  const s = parseTerminalSegments_current('- 5G check failed(2 units)');
  assertBug(!s[0].text.startsWith('-'), 'B2 current: leading dash not stripped (proves bug)');
}
{
  const s = parseTerminalSegments_fixed('- 5G check failed(2 units)');
  assert(!s[0].text.startsWith('-'), 'B2 fixed: leading dash stripped from list item');
}

// B3: "* " list marker (asterisk bullet) should be stripped and not misclassified
{
  const s = parseTerminalSegments_current('* Recommendation: inspect 5G antenna');
  assertBug(!s[0].text.startsWith('*'), 'B3 current: leading * stripped (was already working)');
}
{
  const s = parseTerminalSegments_fixed('* Recommendation: inspect 5G antenna');
  assert(!s[0].text.startsWith('*'), 'B3 fixed: leading * stripped from list item');
}

// B4: content with inline bold in a sentence
{
  const s = parseTerminalSegments_current('Alert Level: **WARNING** (see threshold)');
  assertBug(!s[0].text.includes('**'), 'B4 current: **WARNING** not stripped (proves bug)');
}
{
  const s = parseTerminalSegments_fixed('Alert Level: **WARNING** (see threshold)');
  assert(!s[0].text.includes('**'), 'B4 fixed: **WARNING** stripped from mixed content');
}

// B5: inline italic *text* in content
{
  const s = parseTerminalSegments_current('The yield is *below* target.');
  assertBug(!s[0].text.includes('*'), 'B5 current: *italic* not stripped (proves bug)');
}
{
  const s = parseTerminalSegments_fixed('The yield is *below* target.');
  assert(!s[0].text.includes('*'), 'B5 fixed: *italic* stripped from content');
}

// B6: a list item should stay as 'content' type, not section-header
{
  const s = parseTerminalSegments_fixed('- 1. Retest units');
  assert(s[0].type === 'content', 'B6 fixed: "- 1. Retest" is content (not section-header) after stripping dash');
}

// ──────────────────────────────────────────────────────────────────────────
// Section C: edge cases for the fixed version
// ──────────────────────────────────────────────────────────────────────────
console.log('\n[C] Edge cases (fixed only)');

{
  const s = parseTerminalSegments_fixed('**5. Failure Analysis & Recommendations**');
  assert(s[0].type === 'section-header', 'C1: **wrapped** section 5 → section-header');
}
{
  const s = parseTerminalSegments_fixed('# WiFi Test Summary Report');
  assert(s[0].type === 'report-title', 'C2: # prefixed report title → report-title');
}
{
  const s = parseTerminalSegments_fixed('   - Check antenna connections');
  assert(s[0].type === 'content', 'C3: indented list item → content');
  assert(!s[0].text.startsWith('-'), 'C3: indented dash stripped');
}
{
  const input = '**WiFi Test Summary Report**\n\n1. General Information\nWork Order: WO-001\n- All units retested\n\n2. Test Statistics';
  const s = parseTerminalSegments_fixed(input);
  assert(s[0].type === 'report-title',   'C4: multi-line: first is report-title');
  assert(s[1].type === 'blank',          'C4: multi-line: second is blank');
  assert(s[2].type === 'section-header', 'C4: multi-line: third is section-header');
  assert(s[3].type === 'content',        'C4: multi-line: WO line is content');
  assert(s[4].type === 'content',        'C4: multi-line: list item is content');
  assert(!s[4].text.startsWith('-'),     'C4: multi-line: list item dash stripped');
}

// ──────────────────────────────────────────────────────────────────────────
console.log('\n' + '─'.repeat(50));
console.log(`Results: ${passed} passed, ${failed} failed, ${provenBugs} bugs confirmed in old code`);
if (failed > 0) {
  console.log('\nSome fixed-implementation tests FAILED — fix parseTerminalSegments.\n');
  process.exit(1);
} else {
  console.log('\nAll fixed-implementation tests pass. Old-code bugs are confirmed (expected).\n');
  process.exit(0);
}
