# HELEN OS × CONQUEST — Business Plan

**Product:** AI Companion for Governed Knowledge Civilization Games
**Author:** Jean-Marie Tassy
**Date:** 2026-04-15
**Status:** DRAFT — authority=NONE

---

## 1. ONE-LINER

HELEN OS is a governed AI companion that powers CONQUEST — a persistent knowledge civilization game where every claim, territory, and conflict is receipted, replayable, and constitutionally constrained.

---

## 2. PROBLEM

### For gamers
- AI-powered games have no governance — NPCs hallucinate, game state is opaque, disputes have no resolution mechanism
- Existing AI companions are stateless: they forget your progress, your strategy, your alliances
- No game treats AI decisions as auditable artifacts

### For builders
- Multi-agent AI systems have no authority model — agents act without receipts, override each other silently, and drift from goals
- Building AI companions that persist across sessions is unsolved at the product level
- Constitutional AI is research-grade, not product-grade

### For the market
- $180B gaming market, growing 8% YoY
- AI companions market estimated $2.3B by 2027
- Zero products combine governed AI + persistent world simulation + receipted decisions

---

## 3. SOLUTION

### HELEN OS (the engine)
An intent-first, governed AI operating substrate:
- **25 typed intents** — every user request is classified, validated, and receipted before execution
- **5-role Temple** — AURA (insight), HER (expansion), HAL (skepticism), CHRONOS (continuity), MAYOR (readiness)
- **Provider cascade** — Ollama local-first, fallback to Claude/GPT/Gemini with circuit breaker
- **Memory spine** — SQLite with threads, sessions, corpus, mutation log. Memory-backed continuity, not provider-backed
- **Pull OS** — user states intent, HELEN routes. No buttons, no menus

### CONQUEST (the game)
A persistent knowledge civilization simulator:
- **Territory system** — finite hex map, 7 territory states (Neutral → Consecrated)
- **Joute** — CHRONOS-guarded conflict resolution with 9 guards (stake, cooldown, consent, anti-spam)
- **Oracle Deck** — 44 cards (22 Major + 22 Minor across 4 suits: Donjon, Bibliothèque, Tribunal, Remparts)
- **Append-only ledger** — every claim, duel, territory change is sealed and replayable
- **Federation** — multi-player governance with constitutional constraints

### The compound
HELEN is the companion. CONQUEST is the world. Together:
- HELEN remembers your strategy across sessions
- HELEN classifies your moves as typed intents (CLAIM, CHALLENGE, FORTIFY, ALLY)
- HAL pressure-tests your claims before you stake territory
- CHRONOS tracks duel history and cooldowns
- The Oracle Deck generates narrative events governed by the ledger
- Every game decision is receipted — no silent mutations, no hidden state

---

## 4. HOW IT WORKS (User Journey)

### Session 1: First contact
```
Player opens CONQUEST → HELEN greets with /init
"Welcome. No territories held. 3 neutral hexes in range. 
 Oracle draws: The Bibliothèque — knowledge precedes conquest.
 Suggested first move: claim hex A3 (low stake, no defenders)."
```

### Session 2: Return after interruption
```
Player opens CONQUEST 2 days later → HELEN recovers context
"Welcome back. You hold 2 territories. 1 contested (hex B7 — 
 challenger staked 50 points 6 hours ago). CHRONOS cooldown 
 expires in 2h. Your Oracle: The Rempart — defend what matters.
 Tension: unresolved challenge on B7. Next action: prepare joute."
```

### Session 3: Joute (conflict)
```
Player initiates duel → 9 CHRONOS guards validate
✓ G-001 Stake sufficient (50 points)
✓ G-002 Cooldown clear
✓ G-003 Territory is CONTESTED
✓ G-004 Under active duel limit (1/3)
✓ G-005 Consent signatures present
...
HELEN: "All guards passed. Joute authorized. Oracle draws for both players."
```

---

## 5. MARKET

### Target audience
| Segment | Size | Why they care |
|---------|------|---------------|
| Strategy gamers (Civilization, EU4, Crusader Kings) | 50M+ | Want persistent, meaningful AI opponents |
| AI enthusiasts / builders | 10M+ | Want to see governed AI in action |
| Tabletop / card game players | 30M+ | Oracle Deck maps to familiar mechanics |
| Web3 / governance enthusiasts | 5M+ | Constitutional game mechanics |

### Go-to-market
1. **Free browser demo** — single-player CONQUEST with HELEN companion (Ollama local)
2. **Paid multiplayer** — federation mode, Oracle Deck NFTs, ranked joutes
3. **API** — HELEN OS as SaaS for other game studios building AI companions

### Pricing
| Tier | Price | What |
|------|-------|------|
| Free | $0 | Single-player, local Ollama, 3 territories |
| Companion | $9/mo | Multiplayer, cloud providers, full Oracle Deck, 20 territories |
| Federation | $29/mo | Guild features, custom Oracle cards, API access, 100 territories |
| Studio API | $99/mo | HELEN OS API for your own game/app |

---

## 6. COMPETITIVE ADVANTAGE

### What others do
| Competitor | What they offer | What they lack |
|-----------|----------------|---------------|
| Character.AI | Chat-based AI companions | No governance, no game, no persistence |
| AI Dungeon | AI-generated stories | No receipts, no constitutional constraints |
| Replika | Emotional AI companion | No strategy, no world simulation |
| AutoGPT/CrewAI | Multi-agent frameworks | No game layer, no authority model |

### What HELEN × CONQUEST does differently
1. **Receipted decisions** — every move is sealed in an append-only ledger. No silent state mutations.
2. **Constitutional governance** — 9 CHRONOS guards, 5-gate governor, authority=NONE on all AI output
3. **Memory-backed continuity** — HELEN restores your exact context after days of absence
4. **Typed intent system** — 25 intents, not raw prompt execution
5. **Proven architecture** — 1728 tests, Coq kernel typecheck, CI green

---

## 7. TECHNOLOGY

### Already built (proven, tested)
| Component | Status | Tests |
|-----------|--------|-------|
| Intent Gateway (25 types) | Live | 99 |
| Autonomous Execution Loop | Live | 51 |
| AIRI Avatar (Live2D) | Live | — |
| Provider cascade (Ollama/Claude/GPT) | Live | 51 |
| Memory spine (SQLite) | Live | 82+ |
| Temple routing (5 roles) | Live | — |
| Computer-use proposals | Live | — |
| AIRI bridge + redaction firewall | Live | 34 |
| Conversation persistence + export | Live | — |
| Circuit breaker | Live | — |
| **Total** | **1728 tests** | |

### To build (Q3-Q4 2026)
| Component | Effort | Priority |
|-----------|--------|----------|
| CONQUEST territory engine | 4 weeks | P1 |
| Oracle Deck card system | 2 weeks | P1 |
| Joute + CHRONOS guards | 3 weeks | P1 |
| Multiplayer federation | 6 weeks | P2 |
| Browser UI (React/PixiJS) | 4 weeks | P2 |
| Payment integration | 2 weeks | P3 |
| Oracle Deck NFTs | 3 weeks | P3 |

### Architecture
```
Player → Intent Gateway → HELEN Companion → CONQUEST Engine
              ↓                  ↓                ↓
         classify           Temple routing     Territory/Joute
              ↓                  ↓                ↓
          governor           HAL review        CHRONOS guards
              ↓                  ↓                ↓
          execute            receipted          sealed in ledger
```

---

## 8. BUSINESS MODEL

### Revenue streams
1. **Subscriptions** — Companion ($9) / Federation ($29) / Studio ($99)
2. **Oracle Deck** — Collectible digital cards (packs, trading, custom decks)
3. **Tournament entry fees** — Ranked joutes with prize pools
4. **API licensing** — HELEN OS for other studios
5. **Consulting** — Constitutional AI implementation for enterprise

### Unit economics (target Year 2)
| Metric | Value |
|--------|-------|
| MAU (Monthly Active Users) | 50,000 |
| Conversion rate (free→paid) | 5% |
| Paying users | 2,500 |
| ARPU | $15/mo |
| MRR | $37,500 |
| ARR | $450,000 |
| Gross margin | 80% (Ollama local-first) |
| CAC | $20 |
| LTV | $180 (12-month avg) |
| LTV/CAC | 9x |

### Cost structure
| Cost | Monthly | Note |
|------|---------|------|
| Cloud (Railway + Ollama) | $2,000 | Scales with users |
| API costs (Claude/GPT fallback) | $1,500 | Only when local fails |
| Infrastructure | $500 | CI, monitoring, backups |
| **Total COGS** | **$4,000** | |
| Team (2 engineers) | $20,000 | JMT + 1 hire |
| Marketing | $3,000 | Community, content |
| **Total opex** | **$27,000** | |
| **Break-even** | **~1,800 paying users** | |

---

## 9. ROADMAP

### Q2 2026 (NOW) — Foundation
- [x] HELEN OS kernel (1728 tests)
- [x] Intent Gateway (25 types)
- [x] AIRI avatar (Live2D)
- [x] Memory spine + conversation persistence
- [x] Railway deployment
- [ ] CONQUEST territory engine MVP
- [ ] Oracle Deck card system

### Q3 2026 — Alpha
- [ ] Joute + 9 CHRONOS guards
- [ ] Single-player CONQUEST browser demo
- [ ] HELEN companion integrated into game loop
- [ ] Public alpha (100 players)
- [ ] Community Discord

### Q4 2026 — Beta
- [ ] Multiplayer federation
- [ ] Oracle Deck marketplace
- [ ] Ranked joutes
- [ ] Payment integration
- [ ] Public beta (1,000 players)

### Q1 2027 — Launch
- [ ] Full launch
- [ ] Studio API
- [ ] Mobile companion app
- [ ] First tournament

---

## 10. TEAM

**Jean-Marie Tassy** — Founder & CTO
- 20 years in digital engineering
- Built HELEN OS (1728 tests, Coq-verified kernel, multi-model governance)
- Built CONQUEST governance framework (44-card Oracle, 9 CHRONOS guards)
- Mathematics research (autoresearch, spectral theory)

**Hiring:** 1 game developer (Q3 2026), 1 designer (Q4 2026)

---

## 11. ASK

### For investors
- **Raising:** Seed round, $500K
- **Use of funds:** 12 months runway (team + infra + launch)
- **Milestones:** Alpha (Q3), Beta (Q4), Launch (Q1 2027), 50K MAU (Q2 2027)

### For partners
- **Game studios:** License HELEN OS as your AI companion engine
- **Card game designers:** Design Oracle Deck expansions
- **AI researchers:** Contribute to the constitutional governance framework

### For players
- **Join the alpha:** conquest.helen-os.com (coming Q3 2026)
- **Build with us:** github.com/JMTassy/helen-os (open source, 1728 tests)

---

## 12. WHY NOW

1. **Local LLMs are production-ready** — Ollama + gemma4 runs on any Mac. No API costs for basic play.
2. **AI companions are the next UX paradigm** — ChatGPT proved demand; HELEN proves governance.
3. **Governance matters more than intelligence** — The market will split between "fast AI" and "trustworthy AI." HELEN is trustworthy AI.
4. **The kernel exists** — 1728 tests, Coq-verified, CI green. This is not a pitch deck — it's a working system.

---

## 13. ONE SENTENCE

**HELEN OS turns AI from a black box into a governed companion, and CONQUEST turns that companion into a world where every decision is receipted, every conflict is fair, and every player's context survives interruption.**

---

*authority = NONE — this document proposes, it does not decide.*
