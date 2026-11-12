import type { Snippet } from "@/components/site/code-tabs";

/** Code samples; without a configured API URL they use the $LORDE_BASE_URL variable the SDK reads too. */
export const snippets = (api = ""): Snippet[] => withBase(api || "$LORDE_BASE_URL");

const withBase = (api: string): Snippet[] => [
  {
    id: "sdk", label: "Python SDK", lang: "python",
    code: `pip install lorde

from lorde import Lorde

client = Lorde()  # LORDE_API_KEY, LORDE_BASE_URL
r = client.judge.grounding(
    source_text="The Niger River is about 4,180 km long ...",
    question="How long is the Niger River?",
    answer="It is about 6,650 km long.",
)
print(r.verdict, r.grounding_score, r.decision)
# hallucinated 0.1 reject`,
  },
  {
    id: "curatorkit", label: "CuratorKIT", lang: "python",
    code: `from curatorkit import Curator, CuratorConfig
from lorde.curatorkit import judge_llm

# One line: Lorde becomes CuratorKIT's judge.
Curator(CuratorConfig(
    dataset="data.jsonl",
    reward_threshold=0.7,
    hallucination_threshold=0.7,
    judge_llm=judge_llm(),
)).run()`,
  },
  {
    id: "mcp", label: "MCP", lang: "bash",
    code: `# Claude Code, over Streamable HTTP
claude mcp add --transport http lorde ${api}/mcp \\
  --header "Authorization: Bearer $LORDE_API_KEY"

# or locally, over stdio through the SDK
claude mcp add lorde -- lorde mcp`,
  },
  {
    id: "rest", label: "REST", lang: "bash",
    code: `curl ${api}/v1/judge/grounding \\
  -H "Authorization: Bearer $LORDE_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"source_text": "…", "question": "…", "answer": "…", "threshold": 0.7}'

# { "grounding_score": 0.1, "verdict": "hallucinated",
#   "decision": "reject", "model": "lorde-tiny-aya@v1.0.0", … }`,
  },
  {
    id: "jobs", label: "Gating jobs", lang: "bash",
    code: `# Upload, gate with CuratorKIT, wait, download passed/rejected/report
lorde jobs create data.jsonl --grounding 0.7 --reward 0.7 --out results/`,
  },
];
