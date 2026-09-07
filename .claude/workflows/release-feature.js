export const meta = {
  name: 'release-feature',
  description: 'Code -> Review (parallel dimensions) -> Test & Release, for one already-planned, already-branched feature',
  phases: [
    { title: 'Code', detail: 'implement the plan on the current feature branch' },
    { title: 'Review', detail: 'correctness, security, simplification reviewers run in parallel over the diff' },
    { title: 'Test & Release', detail: 'apply must-fix findings, run the release checklist, push, open the PR' },
  ],
}

// Expected `args` shape (built by the release-feature skill, which runs the Plan phase
// and creates the branch BEFORE calling this workflow):
// {
//   plan: {
//     scope: string,
//     affectedAreas: string[],
//     versionBumpType: 'patch'|'minor'|'major',
//     targetVersion: string,
//     touchesArchitecture: boolean,
//     touchesPricing: boolean,
//     userFacing: boolean,
//     branchName: string,
//     openQuestions: string[],
//   },
//   branch: string,          // the feature branch, already checked out
//   attribution: {
//     commitTrailer: string, // e.g. "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: ..."
//     prFooter: string,      // e.g. "🤖 Generated with [Claude Code](...)\n\nhttps://..."
//   },
// }

const CODE_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    filesChanged: { type: 'array', items: { type: 'string' } },
    versionBumped: { type: 'boolean' },
    architectureUpdated: { type: 'boolean' },
    deviationsFromPlan: { type: 'array', items: { type: 'string' } },
  },
  required: ['summary', 'filesChanged', 'versionBumped'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'number' },
          summary: { type: 'string' },
          failureScenario: { type: 'string' },
          severity: { type: 'string', enum: ['low', 'medium', 'high'] },
        },
        required: ['file', 'summary', 'failureScenario'],
      },
    },
  },
  required: ['findings'],
}

const TEST_SCHEMA = {
  type: 'object',
  properties: {
    testsPassed: { type: 'boolean' },
    testCount: { type: 'number' },
    findingsFixed: { type: 'array', items: { type: 'string' } },
    findingsSkipped: { type: 'array', items: { type: 'string' } },
    prUrl: { type: 'string' },
    blocker: { type: 'string' },
    checklistNotesApplicable: { type: 'array', items: { type: 'string' } },
  },
  required: ['testsPassed'],
}

function reviewPrompt(dimension) {
  return `Review the current uncommitted git diff on this branch along the "${dimension}" dimension only, per your phase-review instructions. Report concrete, verified findings — an empty list is fine if there's nothing real to report.`
}

phase('Code')
log(`Implementing plan: ${args.plan.scope}`)
const codeResult = await agent(
  `Implement this plan on the current branch (already checked out, do not create a new one):\n\n${JSON.stringify(args.plan, null, 2)}`,
  { agentType: 'phase-code', label: 'code', schema: CODE_SCHEMA }
)

if (!codeResult) {
  return { plan: args.plan, code: null, reviewFindings: [], release: null, blocker: 'Code phase agent failed to return a result.' }
}

phase('Review')
const DIMENSIONS = ['correctness', 'security', 'simplification']
const reviews = await parallel(
  DIMENSIONS.map((d) => () =>
    agent(reviewPrompt(d), { agentType: 'phase-review', phase: 'Review', label: `review:${d}`, schema: REVIEW_SCHEMA })
  )
)
const findings = reviews.filter(Boolean).flatMap((r) => r.findings || [])
log(`Review found ${findings.length} finding(s) across ${DIMENSIONS.length} dimensions`)

phase('Test & Release')
const testResult = await agent(
  `Code phase result:\n${JSON.stringify(codeResult, null, 2)}\n\n` +
    `Review findings to triage and fix where real:\n${JSON.stringify(findings, null, 2)}\n\n` +
    `Plan (for architecture/pricing/user-facing flags):\n${JSON.stringify(args.plan, null, 2)}\n\n` +
    `Branch: ${args.branch}\n\n` +
    `Use this exact commit-message trailer:\n${args.attribution.commitTrailer}\n\n` +
    `Use this exact PR-description footer:\n${args.attribution.prFooter}`,
  { agentType: 'phase-test', label: 'test-and-release', schema: TEST_SCHEMA }
)

return { plan: args.plan, code: codeResult, reviewFindings: findings, release: testResult }
