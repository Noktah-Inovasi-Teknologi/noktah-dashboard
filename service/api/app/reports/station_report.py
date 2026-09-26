"""
Laporan → Stations (spec 009 US6; FR-052…FR-057; G-14, G-15, G-19, G-21, G-35, G-36).

For the contents of a month (by publication date):
  per Station   content in, content moved forward, Returns (by origin station), time held
                (median, longest), what is still waiting and for how long;
  per person    the same, for the Person who held each Station: B, C, E from the issue's own
                Field Associate / Content Editor, A and D from the Client's Team at the time
                (the Jira assignee is never used);
  per team      client first-pass rate and average QA rounds against the Incentive
                Framework's thresholds (≥ 60 %, ≤ 1.5), and Returns;
  rounds        content that went back and forth more than 3 times between two Stations is
                flagged for managerial review, never charged.
A Return counts against the origin station of its Defect Category; without one it is
"tanpa kategori" under the Station that sent it back, charged to no one.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from . import content, stations

FIRST_PASS_TARGET = 0.6
QA_ROUNDS_TARGET = 1.5
ROUND_LIMIT = 3


def _held(values: List[float]) -> Dict[str, Optional[float]]:
    return {"median": round(median(values), 1) if values else None,
            "longest": round(max(values), 1) if values else None}


async def _team_history(conn: asyncpg.Connection, client_ids: List[str]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    rows = await conn.fetch(
        """SELECT t.client_id::text AS client_id, t.team_role, t.valid_from, t.valid_to, p.id::text AS person_id,
                  p.display_name
           FROM client_team_assignments t JOIN people p ON p.id = t.person_id
           WHERE t.client_id = ANY($1::uuid[])""", client_ids)
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        role = "quality_assurance" if r["team_role"] in ("qc", "quality_assurance") else r["team_role"]
        out[(r["client_id"], role)].append(dict(r))
    return out


def _team_person(history, client_id: Optional[str], role: str, at: datetime) -> Optional[Dict[str, Any]]:
    for t in history.get((client_id, role), []):
        if t["valid_from"] <= at and (t["valid_to"] is None or t["valid_to"] > at):
            return {"id": t["person_id"], "name": t["display_name"]}
    return None


def compute(contents: List[content.Content], people_by_account: Dict[str, Dict[str, Any]], team_history,
            now: datetime) -> Dict[str, Any]:
    st = {s: {"in": set(), "out": set(), "returns_by_origin": defaultdict(int), "held": defaultdict(float),
              "waiting": []} for s in stations.STATIONS}
    per_person: Dict[Tuple[str, str], Dict[str, Any]] = {}
    teams: Dict[Optional[str], Dict[str, Any]] = {}
    returns_list, round_flags, unknown = [], [], set()

    def person_for(c: content.Content, station: str, at: datetime) -> Dict[str, Any]:
        if station in ("B", "E") and c.fa_account:
            p = people_by_account.get(c.fa_account)
            return p or {"id": None, "name": "tidak terdaftar", "account_id": c.fa_account}
        if station == "C" and c.ce_account:
            p = people_by_account.get(c.ce_account)
            return p or {"id": None, "name": "tidak terdaftar", "account_id": c.ce_account}
        role = stations.STATIONS[station]["role_key"]
        return _team_person(team_history, c.client_id, role, at) or {"id": None, "name": "belum ada di tim"}

    def pp(person: Dict[str, Any], station: str) -> Dict[str, Any]:
        key = (person.get("id") or person.get("account_id") or person["name"], station)
        return per_person.setdefault(key, {"person": person, "station": station, "in": set(), "out": set(),
                                           "held": defaultdict(float), "returns_against": 0, "waiting": 0})

    for c in contents:
        team = teams.setdefault(c.client_id, {"client": {"id": c.client_id, "name": c.client_name or "Tanpa klien"},
                                             "client_review_counted": 0, "first_pass": 0, "qa_contents": 0,
                                             "qa_entries": 0, "returns": 0})
        ivals = c.intervals(now)
        for status, a, b in ivals:
            if not stations.is_known(status):
                unknown.add(status)
            s = stations.station_of(status)
            if s is None:
                continue
            h = content.hours(a, b)
            st[s]["in"].add(c.key)
            st[s]["held"][c.key] += h
            p = pp(person_for(c, s, a), s)
            p["in"].add(c.key)
            p["held"][c.key] += h
        # forward moves out of a station, returns, rounds
        pair_rounds: Dict[frozenset, int] = defaultdict(int)
        for t in c.transitions:
            s_from, s_to = stations.station_of(t.from_status), stations.station_of(t.to_status)
            if stations.is_return(t.from_status, t.to_status):
                origin = t.origin
                team["returns"] += 1
                bucket = origin or "tanpa_kategori"
                if origin:
                    st[origin]["returns_by_origin"][origin] += 1
                    pp(person_for(c, origin, t.at), origin)["returns_against"] += 1
                elif s_from:
                    st[s_from]["returns_by_origin"]["tanpa_kategori"] += 1
                returns_list.append({"key": c.key, "client": c.client_name, "at": t.at.isoformat(),
                                     "from_status": t.from_status, "to_status": t.to_status, "origin": origin,
                                     "sent_back_by": s_from, "defect": t.defect_code, "reason": t.reason,
                                     "bucket": bucket})
                if s_from and s_to and s_from != s_to:
                    pair_rounds[frozenset((s_from, s_to))] += 1
            elif s_from and s_to != s_from:
                st[s_from]["out"].add(c.key)
                pp(person_for(c, s_from, t.at), s_from)["out"].add(c.key)
        for pair, n in pair_rounds.items():
            if n > ROUND_LIMIT:
                round_flags.append({"key": c.key, "client": c.client_name, "stations": sorted(pair), "rounds": n})
        # still waiting
        cur = stations.station_of(c.status)
        if cur and not stations.is_published(c.status):
            since = c.transitions[-1].at if c.transitions else c.created_at
            st[cur]["waiting"].append({"key": c.key, "client": c.client_name, "since": since.isoformat(),
                                       "hours": round(content.hours(since, now), 1)})
            pp(person_for(c, cur, since), cur)["waiting"] += 1
        # team thresholds (G-36)
        entries_client = sum(1 for t in c.transitions if stations.canon(t.to_status) == stations.CLIENT_REVIEW_START)
        if entries_client:
            team["client_review_counted"] += 1
            sent_back = any(stations.is_return(t.from_status, t.to_status)
                            and stations.canon(t.from_status) in stations.CLIENT_REVIEW for t in c.transitions)
            reached = any((stations.place(t.to_status) or (0, None))[0] >= 8 for t in c.transitions)
            if entries_client == 1 and not sent_back and reached:
                team["first_pass"] += 1
        qa = sum(1 for t in c.transitions if stations.canon(t.to_status) == stations.QA_ENTRY)
        if qa:
            team["qa_contents"] += 1
            team["qa_entries"] += qa

    station_out = []
    for s, d in st.items():
        station_out.append({"station": s, "label": stations.STATIONS[s]["label"], "role": stations.STATIONS[s]["role"],
                            "in": len(d["in"]), "out": len(d["out"]),
                            "returns": {"total": sum(d["returns_by_origin"].values()),
                                        "by_origin": dict(d["returns_by_origin"])},
                            "held_hours": _held(list(d["held"].values())),
                            "waiting": sorted(d["waiting"], key=lambda w: -w["hours"])})
    people_out = [{"person": v["person"], "station": v["station"], "in": len(v["in"]), "out": len(v["out"]),
                   "returns_against": v["returns_against"], "held_hours": _held(list(v["held"].values())),
                   "waiting": v["waiting"]}
                  for v in per_person.values()]
    people_out.sort(key=lambda p: (p["station"], p["person"]["name"]))
    teams_out = []
    for t in teams.values():
        fp_rate = t["first_pass"] / t["client_review_counted"] if t["client_review_counted"] else None
        qa_avg = t["qa_entries"] / t["qa_contents"] if t["qa_contents"] else None
        teams_out.append({
            "client": t["client"],
            "first_pass": {"rate": round(fp_rate, 3) if fp_rate is not None else None,
                           "counted": t["client_review_counted"], "target": FIRST_PASS_TARGET},
            "qa_rounds": {"average": round(qa_avg, 2) if qa_avg is not None else None, "counted": t["qa_contents"],
                          "target": QA_ROUNDS_TARGET},
            "both_met": fp_rate is not None and qa_avg is not None and fp_rate >= FIRST_PASS_TARGET
            and qa_avg <= QA_ROUNDS_TARGET,
            "returns": t["returns"]})
    teams_out.sort(key=lambda t: t["client"]["name"])
    return {"stations": station_out, "people": people_out, "teams": teams_out, "round_flags": round_flags,
            "returns": sorted(returns_list, key=lambda r: r["at"]), "unknown_statuses": sorted(unknown)}


async def report(conn: asyncpg.Connection, month: date, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    contents = await content.load(conn, month)
    people = {r["jira_account_id"]: {"id": r["id"], "name": r["display_name"]} for r in await conn.fetch(
        "SELECT id::text AS id, display_name, jira_account_id FROM people WHERE jira_account_id IS NOT NULL")}
    history = await _team_history(conn, list({c.client_id for c in contents if c.client_id}))
    return {"month": month.isoformat()[:7], **compute(contents, people, history, now)}
