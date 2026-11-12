# lorde

Python client for the Lorde Gateway: judge training data with CuratorKIT's RewardGate and HallucinationGate.

```bash
pip install lorde            # add [mcp] for `lorde mcp`
export LORDE_API_KEY=lorde_sk_...  LORDE_BASE_URL=https://api.<domain>
```

```python
from lorde import Lorde

client = Lorde()                                   # LORDE_API_KEY / LORDE_BASE_URL
r = client.judge.grounding(source_text=src, question=q, answer=a)
print(r.verdict, r.grounding_score, r.decision)
batch = client.judge.batch(kind="reward", items=[{"instruction": i, "response": o} for i, o in pairs])

# CuratorKIT preset: use Lorde as the judge in one line
from curatorkit import Curator, CuratorConfig
from lorde.curatorkit import judge_llm
Curator(CuratorConfig(dataset="data.jsonl", reward_threshold=0.7, hallucination_threshold=0.7,
                      judge_llm=judge_llm())).run()
```

```bash
lorde judge grounding --source src.txt --question "..." --answer "..."
lorde judge reward --instruction "..." --response "..."
lorde mcp                       # stdio MCP server for agents
claude mcp add lorde -- lorde mcp
```

Retries with exponential backoff and jitter on 429/503, honouring `Retry-After`. The SDK refuses to talk to
a Gateway with an incompatible major API version.
