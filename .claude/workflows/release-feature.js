export const meta = {
  name: 'release-feature',
  description: 'Code -> Review (parallel dimensions) -> Test & Release, for one already-specced, already-designed, already-branched feature',
  phases: [
    { title: 'Code', detail: 'implement the design on the current feature branch' },
    { title: 'Review', detail: 'correctness, security, simplification reviewers run in parallel over the diff' },
    { title: 'Test & Release', detail: 'check acceptance criteria, apply must-fix findings, run the release checklist, push, open the PR' },
  ],
}

// Expected `args` shape (built by the release-feature skill, which runs the Spec and
// Design phases and creates the branch BEFORE calling this workflow):
// {
//   spec: {
//     problem: string,
//     targetUseCase: string,
//     functionalRequirements: string[],
//     acceptanceCriteria: string[],
//     nonGoals: string[],
//     existingCapabilityNotes: string,
//   },
//   design: {
//     approach: string,
//     affectedAreas: string[],
//     versionBumpType: 'patch'|'minor'|'major',
//     targetVersion: string,
//     touchesArchitecture: boolean,
//     touchesPricing: boolean,
//     userFacing: boolean,
//     branchName: string,
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
    acceptanceCriteriaSatisfied: { type: 'array', items: { type: 'string' } },
    versionBumped: { type: 'boolean' },
    architectureUpdated: { type: 'boolean' },
    deviationsFromDesign: { type: 'array', items: { type: 'string' } },
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
    acceptanceCriteriaVerified: { type: 'array', items: { type: 'string' } },
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
log(`Implementing design for: ${args.spec.problem}`)
const codeResult = await agent(
  `Spec (requirements, fixed — do not re-derive the approach, use this only to check your work against its acceptance criteria):\n${JSON.stringify(args.spec, null, 2)}\n\n` +
    `Design (the technical approach to implement, on the current branch, already checked out, do not create a new one):\n${JSON.stringify(args.design, null, 2)}`,
  { agentType: 'phase-code', label: 'code', schema: CODE_SCHEMA }
)

if (!codeResult) {
  return { spec: args.spec, design: args.design, code: null, reviewFindings: [], release: null, blocker: 'Code phase agent failed to return a result.' }
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
  `Spec's acceptance criteria to verify against the actual implementation:\n${JSON.stringify(args.spec.acceptanceCriteria, null, 2)}\n\n` +
    `Code phase result:\n${JSON.stringify(codeResult, null, 2)}\n\n` +
    `Review findings to triage and fix where real:\n${JSON.stringify(findings, null, 2)}\n\n` +
    `Design (for architecture/pricing/user-facing flags):\n${JSON.stringify(args.design, null, 2)}\n\n` +
    `Branch: ${args.branch}\n\n` +
    `Use this exact commit-message trailer:\n${args.attribution.commitTrailer}\n\n` +
    `Use this exact PR-description footer:\n${args.attribution.prFooter}`,
  { agentType: 'phase-test', label: 'test-and-release', schema: TEST_SCHEMA }
)

return { spec: args.spec, design: args.design, code: codeResult, reviewFindings: findings, release: testResult }
