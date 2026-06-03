"""llm-curator — CLI to inspect the LLM registry.

Usage:
  python -m llm_curator.cli stats
  python -m llm_curator.cli list [--source openrouter|ollama-cloud] [--free|--paid] [--limit N]
  python -m llm_curator.cli show <model_id>
  python -m llm_curator.cli runs [--limit N]
  python -m llm_curator.cli in-litellm
"""
from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_curator.db import cursor  # noqa: E402

# Brain memory notifier — only used by state-changing subcommands (propose --persist).
# Fire-and-forget; harmless if Brain is unreachable.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
try:
    from memory_notify import notify  # type: ignore
except Exception:
    def notify(*_args, **_kwargs):
        return None


def cmd_stats(_args) -> None:
    with cursor() as cur:
        cur.execute("""
            SELECT source,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE is_free) AS free,
                   COUNT(*) FILTER (WHERE deprecated) AS deprecated,
                   COUNT(*) FILTER (WHERE supports_vision) AS vision,
                   COUNT(*) FILTER (WHERE supports_reasoning) AS reasoning,
                   COUNT(*) FILTER (WHERE supports_tools) AS tools,
                   COUNT(*) FILTER (WHERE in_litellm) AS in_litellm
            FROM llm_registry
            GROUP BY source ORDER BY source;
        """)
        rows = cur.fetchall()
        if not rows:
            print("Registry is empty. Run discovery first.")
            return
        print(f"{'SOURCE':<16}{'TOTAL':>8}{'FREE':>8}{'DEPR':>8}{'VISION':>8}{'REASON':>8}{'TOOLS':>8}{'IN_LL':>8}")
        for r in rows:
            print(f"{r['source']:<16}{r['total']:>8}{r['free']:>8}{r['deprecated']:>8}"
                  f"{r['vision']:>8}{r['reasoning']:>8}{r['tools']:>8}{r['in_litellm']:>8}")


def cmd_list(args) -> None:
    where = ["deprecated = FALSE"]
    params = []
    if args.source:
        where.append("source = %s")
        params.append(args.source)
    if args.free:
        where.append("is_free = TRUE")
    if args.paid:
        where.append("is_free = FALSE")
    sql = f"""
        SELECT model_id, source, provider, context_length, is_free,
               supports_vision, supports_reasoning, supports_tools,
               pricing_input, pricing_output
        FROM llm_registry
        WHERE {' AND '.join(where)}
        ORDER BY source, provider, model_id
        LIMIT %s
    """
    params.append(args.limit)
    with cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            print("No models matched.")
            return
        for r in rows:
            tags = []
            if r["is_free"]:
                tags.append("FREE")
            if r["supports_vision"]:
                tags.append("vis")
            if r["supports_reasoning"]:
                tags.append("reason")
            if r["supports_tools"]:
                tags.append("tools")
            ctx = f"{r['context_length']//1000}K" if r["context_length"] else "?"
            price = ""
            if r["pricing_input"] is not None:
                price = f" ${float(r['pricing_input'])*1e6:.2f}/M-in"
            print(f"  [{r['source'][:4]}] {r['model_id']:<60} {ctx:>6} {','.join(tags):<20}{price}")
        print(f"\n{len(rows)} model(s)")


def cmd_show(args) -> None:
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM llm_registry WHERE model_id = %s ORDER BY source",
            (args.model_id,),
        )
        rows = cur.fetchall()
        if not rows:
            print(f"Not found: {args.model_id}")
            return
        for r in rows:
            print(f"\n── {r['model_id']} ({r['source']}) ──")
            for k in ("provider", "display_name", "context_length", "modalities",
                      "supports_tools", "supports_reasoning", "supports_vision",
                      "pricing_input", "pricing_output", "is_free",
                      "knowledge_cutoff", "in_litellm", "litellm_alias",
                      "tier_suggestion", "first_seen", "last_seen", "deprecated"):
                print(f"  {k:<24} {r[k]}")


def cmd_runs(args) -> None:
    with cursor() as cur:
        cur.execute("""
            SELECT source, started_at, finished_at, models_seen, models_new,
                   models_updated, models_deprecated, success, error_message
            FROM llm_discovery_runs
            ORDER BY started_at DESC LIMIT %s
        """, (args.limit,))
        rows = cur.fetchall()
        if not rows:
            print("No runs yet.")
            return
        for r in rows:
            ok = "✓" if r["success"] else "✗"
            print(f"{ok} {r['started_at']:%Y-%m-%d %H:%M}  {r['source']:<14} "
                  f"seen={r['models_seen']:>4} new={r['models_new']:>3} "
                  f"upd={r['models_updated']:>3} depr={r['models_deprecated']:>3}"
                  f"{'  ERR: ' + (r['error_message'] or '')[:80] if not r['success'] else ''}")


def cmd_evals(args) -> None:
    """Show recent eval results, optionally filtered by model or use_case."""
    where = []
    params: list = []
    if args.model:
        where.append("r.model_id = %s")
        params.append(args.model)
    if args.use_case:
        where.append("e.use_case = %s")
        params.append(args.use_case)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT r.model_id, r.source, e.use_case, e.eval_name, e.score,
               e.latency_ms, e.tokens_input, e.tokens_output, e.cost_usd,
               e.error_message, e.tested_at
        FROM llm_evals e
        JOIN llm_registry r ON r.id = e.model_registry_id
        {where_sql}
        ORDER BY e.tested_at DESC
        LIMIT %s
    """
    params.append(args.limit)
    with cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            print("No eval results yet. Run: python -m llm_curator.eval_runner")
            return
        for r in rows:
            score = f"{float(r['score']):.2f}" if r["score"] is not None else "  - "
            err = f"  ERR: {(r['error_message'] or '')[:60]}" if r["error_message"] else ""
            print(f"{r['tested_at']:%m-%d %H:%M}  {r['model_id'][:38]:<38} "
                  f"{r['use_case']:<14} {r['eval_name']:<28} {score}  "
                  f"{r['latency_ms']}ms{err}")


def cmd_leaderboard(args) -> None:
    """Mean score per model per use_case (last 60 days)."""
    where = ""
    params: list = []
    if args.use_case:
        where = "AND e.use_case = %s"
        params.append(args.use_case)
    sql = f"""
        SELECT r.model_id, r.source, e.use_case,
               AVG(e.score) AS mean_score,
               COUNT(*) AS n_evals,
               MAX(e.tested_at) AS last_tested
        FROM llm_evals e
        JOIN llm_registry r ON r.id = e.model_registry_id
        WHERE e.tested_at > NOW() - INTERVAL '60 days'
          AND e.score IS NOT NULL
          {where}
        GROUP BY r.model_id, r.source, e.use_case
        HAVING COUNT(*) >= 1
        ORDER BY e.use_case, mean_score DESC
        LIMIT %s
    """
    params.append(args.limit)
    with cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows:
            print("No evals scored yet.")
            return
        current_uc = None
        for r in rows:
            if r["use_case"] != current_uc:
                print(f"\n── {r['use_case'].upper()} ──")
                current_uc = r["use_case"]
            print(f"  {float(r['mean_score']):.3f}  {r['model_id']:<55} "
                  f"[{r['source'][:4]}] n={r['n_evals']}  last={r['last_tested']:%m-%d}")


def cmd_propose(args) -> None:
    """Generate a fresh proposal by invoking the engine inline."""
    from llm_curator import proposal_generator as pg
    proposal = pg.generate_proposal()
    print(f"\nPROPOSAL: {proposal['summary']}\n")
    for ch in proposal["proposed_changes"]:
        if ch["kind"] == "replace":
            ev = ch["evidence"]
            print(f"  REPLACE  {ch['alias']:<24} {ch['old_model']} → {ch['new_model']}")
            print(f"           score {ev.get('incumbent_score') or 0:.3f} → {ev['candidate_score']:.3f} "
                  f"(tier={ev['tier']}, n={ev['candidate_n_evals']})")
        elif ch["kind"] == "remove":
            print(f"  REMOVE   {ch['alias']:<24} {ch['old_model']}  ({ch['rationale']})")
        elif ch["kind"] == "add":
            print(f"  ADD      {ch['alias']:<24} {ch['new_model']}")
    if proposal["needs_eval"]:
        print(f"\nATTENTION: {len(proposal['needs_eval'])} aliases lack recent eval data.")
        for ne in proposal["needs_eval"][:10]:
            print(f"  {ne['alias']:<24} ({ne['model']}, {ne['tier']} tier)")
        if len(proposal["needs_eval"]) > 10:
            print(f"  ... and {len(proposal['needs_eval']) - 10} more")
    if args.persist:
        pid = pg.persist(proposal)
        print(f"\nPersisted as proposal #{pid}")
        notify(
            "llm_registry",
            f"Curator proposal #{pid} (manual): {proposal['summary']}"
        )


def cmd_proposals(args) -> None:
    """List recent proposals."""
    with cursor() as cur:
        cur.execute("""
            SELECT id, generated_at, status, summary,
                   n_replacements, n_additions, n_removals
            FROM llm_proposals
            ORDER BY generated_at DESC LIMIT %s
        """, (args.limit,))
        rows = cur.fetchall()
        if not rows:
            print("No proposals yet. Run: llm-curator propose --persist")
            return
        for r in rows:
            badge = {"pending":"●","applied":"✓","rejected":"✗","superseded":"⊘"}.get(r["status"], "?")
            print(f"  #{r['id']:<4} {r['generated_at']:%Y-%m-%d %H:%M}  {badge} {r['status']:<10}  "
                  f"R={r['n_replacements']} A={r['n_additions']} D={r['n_removals']}  {r['summary'][:80]}")


def cmd_proposal_show(args) -> None:
    """Show full detail of one proposal."""
    with cursor() as cur:
        cur.execute("SELECT * FROM llm_proposals WHERE id = %s", (args.id,))
        p = cur.fetchone()
    if not p:
        print(f"Not found: proposal #{args.id}")
        return
    print(f"\nProposal #{p['id']}  [{p['status']}]")
    print(f"  Generated: {p['generated_at']}")
    print(f"  Summary:   {p['summary']}\n")
    payload = p["proposed_changes"]
    changes = payload.get("changes", []) if isinstance(payload, dict) else payload
    needs_eval = payload.get("needs_eval", []) if isinstance(payload, dict) else []
    if changes:
        print("PROPOSED CHANGES:")
        for ch in changes:
            print(f"  [{ch['kind'].upper()}] {ch['alias']}")
            if ch.get("old_model"):
                print(f"    from: {ch['old_model']}")
            if ch.get("new_model"):
                print(f"    to:   {ch['new_model']}")
            print(f"    why:  {ch.get('rationale','')}")
            ev = ch.get("evidence") or {}
            if ev:
                bits = ", ".join(f"{k}={v}" for k, v in ev.items())
                print(f"    evidence: {bits}")
    if needs_eval:
        print(f"\nATTENTION ({len(needs_eval)} aliases lack eval data):")
        for ne in needs_eval:
            print(f"  {ne['alias']:<24} ({ne['model']}, {ne['tier']} tier)")
    if p["reviewer_note"]:
        print(f"\nReviewer note: {p['reviewer_note']}")


def cmd_alerts(args) -> None:
    """Show recent alerts; default = unacknowledged only."""
    where = []
    if not args.all:
        where.append("acknowledged = FALSE")
    if args.severity:
        where.append(f"severity = '{args.severity}'")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    with cursor() as cur:
        cur.execute(f"""
            SELECT id, generated_at, severity, category, model_id,
                   source, litellm_alias, message, acknowledged
            FROM llm_alerts
            {where_sql}
            ORDER BY generated_at DESC LIMIT %s
        """, (args.limit,))
        rows = cur.fetchall()
        if not rows:
            print("No alerts." if args.all else "No unacknowledged alerts.")
            return
        for r in rows:
            sev_color = {"critical": "🔴", "warn": "🟡", "info": "🔵"}.get(r["severity"], "•")
            ack = " [acked]" if r["acknowledged"] else ""
            print(f"#{r['id']:<4} {sev_color} {r['generated_at']:%m-%d %H:%M}  "
                  f"{r['category']:<22} {r['litellm_alias'] or r['model_id'] or '—':<28}{ack}")
            print(f"        {r['message'][:160]}")


def cmd_ack(args) -> None:
    """Acknowledge an alert (or a list of comma-separated IDs)."""
    from llm_curator.db import get_conn
    ids = [int(x) for x in str(args.id).split(",") if x.strip()]
    with cursor() as cur:
        cur.execute(
            "UPDATE llm_alerts SET acknowledged=TRUE, acknowledged_at=NOW(), "
            "ack_note=%s WHERE id = ANY(%s)",
            (args.note, ids),
        )
        n = cur.rowcount
    get_conn().commit()                  # autocommit is OFF — must commit explicitly
    notify("llm_alert", f"Curator alerts acked: {ids} ({args.note or 'no note'})")
    print(f"Acked {n} alert(s): {ids}")


def cmd_in_litellm(_args) -> None:
    """Show all models currently marked as in_litellm=TRUE."""
    with cursor() as cur:
        cur.execute("""
            SELECT model_id, source, litellm_alias, tier_suggestion, is_free
            FROM llm_registry WHERE in_litellm = TRUE
            ORDER BY tier_suggestion, litellm_alias
        """)
        rows = cur.fetchall()
        if not rows:
            print("No models marked in_litellm. (Phase 3 — the curator agent — will populate this.)")
            return
        for r in rows:
            free = "FREE" if r["is_free"] else "PAID"
            print(f"  {r['litellm_alias']:<24} → {r['model_id']:<40} [{r['source']}] {free} ({r['tier_suggestion']})")


def main() -> int:
    p = argparse.ArgumentParser(prog="llm-curator", description="Inspect the LLM registry.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats").set_defaults(func=cmd_stats)

    ls = sub.add_parser("list")
    ls.add_argument("--source", choices=["openrouter", "ollama-cloud"])
    ls.add_argument("--free", action="store_true")
    ls.add_argument("--paid", action="store_true")
    ls.add_argument("--limit", type=int, default=50)
    ls.set_defaults(func=cmd_list)

    sh = sub.add_parser("show")
    sh.add_argument("model_id")
    sh.set_defaults(func=cmd_show)

    rn = sub.add_parser("runs")
    rn.add_argument("--limit", type=int, default=10)
    rn.set_defaults(func=cmd_runs)

    ev = sub.add_parser("evals", help="Recent eval results")
    ev.add_argument("--model")
    ev.add_argument("--use-case")
    ev.add_argument("--limit", type=int, default=30)
    ev.set_defaults(func=cmd_evals)

    lb = sub.add_parser("leaderboard", help="Mean scores per model per use_case (60-day window)")
    lb.add_argument("--use-case")
    lb.add_argument("--limit", type=int, default=50)
    lb.set_defaults(func=cmd_leaderboard)

    pr = sub.add_parser("propose", help="Generate a fresh curator proposal")
    pr.add_argument("--persist", action="store_true", help="Save to llm_proposals (default: dry-run print only)")
    pr.set_defaults(func=cmd_propose)

    ls = sub.add_parser("proposals", help="List recent proposals")
    ls.add_argument("--limit", type=int, default=10)
    ls.set_defaults(func=cmd_proposals)

    ps = sub.add_parser("proposal", help="Show one proposal in detail")
    ps.add_argument("id", type=int)
    ps.set_defaults(func=cmd_proposal_show)

    al = sub.add_parser("alerts", help="Show alerts (default: unacknowledged only)")
    al.add_argument("--all", action="store_true", help="Include acknowledged")
    al.add_argument("--severity", choices=["info", "warn", "critical"])
    al.add_argument("--limit", type=int, default=20)
    al.set_defaults(func=cmd_alerts)

    ak = sub.add_parser("ack", help="Acknowledge an alert by ID (comma-separated for batch)")
    ak.add_argument("id", help="Alert ID, or comma-separated list")
    ak.add_argument("--note", default=None)
    ak.set_defaults(func=cmd_ack)

    sub.add_parser("in-litellm").set_defaults(func=cmd_in_litellm)

    args = p.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
