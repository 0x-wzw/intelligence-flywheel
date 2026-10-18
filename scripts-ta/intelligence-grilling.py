#!/usr/bin/env python3
"""intelligence-grilling.py — daily AI transformation oral board for MUI.

This is an agent-cron context generator. It turns the former briefing-quality
review into a one-question-at-a-time oral examination of the human's AI fluency,
technology judgment, and MUI transformation architecture. The agent must teach
through adversarial questioning: identify an unsupported claim, demand a
mechanism and measurable test, then press or concede.

The script emits context only. The LLM cron conducts the Telegram conversation.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
VAULT = Path("/Users/admin/Documents/Obsidian Vault")
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
LOGS = HERMES_HOME / "logs"
STATE = LOGS / "grilling-state.json"
THREADS = LOGS / "grilling-open-threads.md"
MUI_WIKI = VAULT / "System" / "Wiki" / "entities" / "mui-group-transformation.md"

MUI_FACT_BASE = """
MUI TRANSFORMATION FACT BASE — use only as interrogation anchors; do not invent facts:
- MUIIND's four crises: debt trap; conglomerate discount; family-governance block; parent cash trap.
- Debt burden: RM102M annual interest vs RM26.5M operating profit historically; coverage turned negative in 3QFY26.
- Parent cash: RM6.6M. Net intra-group position: MUI owes subsidiaries about RM262M more than they owe it.
- Load-bearing uncertainties: Regent Corp's RM179M director loans; discount-narrowing mechanism; Hyde Park valuation / debt outcome.
- Hyde Park: RM912M (£152.5M) loan; an estimated RM73–91M annual interest during renovation. A 20% London-property decline could create a covenant/forced-sale pathway.
- AI transformation is not a substitute for capital restructuring. It must either cut a measured cost, accelerate an evidenced revenue/asset outcome, reduce risk, or improve capital-allocation information.
- Operating levers: IHM hotel-management credibility after Hyde Park; A&W unit economics before expansion; Metrojaya's decision gate; MUIPROP land run-off discipline.
""".strip()

EXAMINATION_PROTOCOL = """
MUI AI TRANSFORMATION ORAL BOARD — this is a rigorous executive examination, not a status report.

MISSION
Test the human's ability to connect AI and technology choices to MUI's capital structure,
operating constraints, governance, and measurable value creation. The goal is better
judgment, not agreement or AI theatre.

QUESTIONING RULES
1. Ask EXACTLY ONE question per message. Open immediately with it; no warm-up.
2. Select one rotating domain: (A) AI foundations and model economics; (B) data,
   architecture and governance; (C) automation/agents and operating redesign; (D)
   MUI value creation and capital allocation; (E) risk, compliance and cyber resilience.
3. Every question must be anchored in a concrete MUI constraint from the fact base and
   demand a mechanism plus a number, owner, deadline, decision threshold, or falsifier.
4. After the answer, first state either "HOLD" (the answer survives) or "BREAK" (the
   answer fails), in 1–3 sentences. Then ask the next single question.
5. Attack these load-bearing assumptions before peripheral tools: Hyde Park valuation and
   covenants; Regent Corp director loans; the mechanism for discount narrowing. Do not
   let a chatbot, dashboard, RWA token, or pilot distract from a solvency constraint.
6. Distinguish capability theatre from transformation. Ask: what workflow changes, what
   data is required, who owns the exception, what is the marginal cost, and what happens
   when the model is wrong?
7. If the user is right, concede precisely and advance the difficulty. If vague, say what
   evidence is absent and force a sharper answer. Never praise generically.
8. Do not request confidential credentials, unpublished material information, personal
   data, or trading instructions.

REQUIRED MUI TEARDOWN FOR MATERIAL PROPOSALS
When the human proposes an AI initiative with >RM100k cost, cross-functional impact,
or any capital-structure claim, interrogate it through:
- FATAL / STRUCTURAL / CONTAINABLE blind spots (lead with FATAL);
- strongest antithesis and the mechanism by which it wins;
- 24-month pre-mortem: one self-inflicted, one competitive, one exogenous failure;
- evidence quality: weakest datum, strongest attack, and what still survives.

STATE AND SIGN-OFF
- Maintain `grilling-state.json`: round, domain, topics_covered, open_threads,
  last_question, and updated_at.
- Maintain unresolved questions in `grilling-open-threads.md`; the next session opens
  with the oldest unresolved thread before a new domain.
- Close only when the user explicitly says "comfortable", "good enough", or "ship it"
  AND there are no unresolved objections. Then print:
  `SIGN-OFF: MUI AI transformation oral board closed. <one-line capability or decision stress-tested>.`
""".strip()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _state_summary() -> str:
    raw = _read(STATE)
    if not raw:
        return "No prior state. Start with a diagnostic question in Domain A or D."
    try:
        data = json.loads(raw)
        safe = {key: data.get(key) for key in ("round", "domain", "topics_covered", "open_threads", "last_question", "updated_at") if key in data}
        return json.dumps(safe, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return raw[:1500]


def build_brief() -> str:
    day = date.today().isoformat()
    wiki = _read(MUI_WIKI)
    wiki_excerpt = wiki[:3500] if wiki else "MUI wiki unavailable; use the fact base only."
    threads = _read(THREADS)
    lines = [
        f"=== MUI AI TRANSFORMATION ORAL BOARD — {day} ===",
        "",
        MUI_FACT_BASE,
        "",
        "CANONICAL WIKI EXCERPT (may contain additional context; do not treat stale values as live market data):",
        wiki_excerpt,
        "",
        "OPEN THREADS — pursue first if present:",
        threads or "None.",
        "",
        "LAST SESSION STATE:",
        _state_summary(),
        "",
        EXAMINATION_PROTOCOL,
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(build_brief())
