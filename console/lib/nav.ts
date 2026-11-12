export type NavLink = { label: string; href: string; desc?: string };
export type NavGroup = { label: string; links: NavLink[] };

export const siteNav: NavGroup[] = [
  {
    label: "Product",
    links: [
      { label: "Grounding gate", href: "/docs#grounding", desc: "Is every claim supported by its source?" },
      { label: "Reward gate", href: "/docs#reward", desc: "Helpfulness, honesty, instruction following." },
      { label: "Gating jobs", href: "/docs#jobs", desc: "Curate whole datasets with CuratorKIT." },
      { label: "Playground", href: "/console/playground", desc: "Judge one item in the browser." },
    ],
  },
  {
    label: "Developers",
    links: [
      { label: "Quickstart", href: "/docs", desc: "API, SDK, MCP and CuratorKIT in minutes." },
      { label: "API reference", href: "/docs#api", desc: "Endpoints, errors and limits." },
      { label: "Python SDK", href: "/docs#sdk", desc: "pip install lorde." },
      { label: "MCP server", href: "/docs#mcp", desc: "Lorde tools for coding agents." },
    ],
  },
  {
    label: "Bench",
    links: [
      { label: "Lorde Bench", href: "/bench", desc: "What every release must prove." },
      { label: "Model card", href: "/model", desc: "Base, data, method, limits." },
      { label: "Languages", href: "/model#languages", desc: "Eight languages, one teacher that reads them." },
    ],
  },
  {
    label: "Resources",
    links: [
      { label: "Status", href: "/status", desc: "Gateway and model server checks." },
      { label: "Licences", href: "/model#licence", desc: "Research and non-commercial use." },
      { label: "Hugging Face", href: "https://huggingface.co/Oxlino", desc: "Model, data and demo Space." },
      { label: "GitHub", href: "https://github.com/linoxbt/Lorde", desc: "Forge, Gateway, SDK and Console." },
    ],
  },
];

export const consoleNav: NavLink[] = [
  { label: "Overview", href: "/console" },
  { label: "Datasets", href: "/console/datasets" },
  { label: "Jobs", href: "/console/jobs" },
  { label: "New job", href: "/console/jobs/new" },
  { label: "Review", href: "/console/review" },
  { label: "Playground", href: "/console/playground" },
  { label: "Reports", href: "/console/reports" },
  { label: "API keys", href: "/console/keys" },
  { label: "Settings", href: "/console/settings" },
];
