# CafeLab v0.4 status

CafeLab is now a two-barista coordination simulation. Alex and Jamie run as separate async barista agents against one deterministic `WorldState`. The world owns orders, staff state, metrics, revenue, event logs, and thinking summaries. Agents still do not mutate reality directly; they request tools and the world validates every transition.

Implemented v0.2 pieces:

1. **Two active baristas**
   - `BARISTA_ROSTER` starts Alex and Jamie in terminal and dashboard mode.
   - Each barista has a private shift memory inside its own loop.

2. **Shared queue ownership**
   - Orders move through `pending -> claimed -> preparing -> ready -> delivered`.
   - A barista must claim before preparing.
   - The world rejects attempts to prepare or mark ready an order claimed by another barista.

3. **Agent-relative prompts**
   - Each barista receives the same underlying world as a personalized operational snapshot.
   - The prompt shows current order, failed claims, pending orders, claimed-by-you counts, claimed-by-other-barista counts, ready counts, and queue pressure.

4. **Dashboard visibility**
   - The Staff panel shows both baristas, current order, completion count, last action, and latest thinking summary.
   - KPIs include total claim conflicts, conflict split, barista completions, idle checks, and lifecycle timing.

5. **Run reports**
   - Reports include coordination metrics, per-barista completions, per-barista idle checks, claim conflicts by barista, conflict pair counts, and basic lifecycle durations.

6. **Live supplies**
   - Supplies are world-owned and tied to menu recipes.
   - Customer menus hide items that cannot currently be made.
   - `prepare_order` still performs the final deterministic stock check and fails already-placed orders cleanly if stock ran out before prep.
   - Dashboard and run summaries show stockout failures and final supply status.

Recommended next work:

1. Add explicit abandoned, failed, and stale transitions when customer impatience or long-ready orders should affect the world state.
2. Add a run comparison helper that reads two report directories and answers whether two baristas improved wait time or mostly added conflicts.
3. Add a deterministic fake-model simulation mode for repeatable CI tests without live OpenAI calls.
4. Consider moving status constants and staff roster into a small shared contract module if more agent roles are added.
