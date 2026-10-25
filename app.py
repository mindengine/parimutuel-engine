"""Streamlit dashboard:  streamlit run app.py"""
import json
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from pe import analytics, db
from pe.legacy import ingest_legacy

st.set_page_config(page_title="Parimutuel Engine", layout="wide")
DB = st.sidebar.text_input("Database", str(db.DEFAULT_DB))
con = db.connect(DB)

page = st.sidebar.radio("View", ["Cards", "Tickets", "Hit rates", "LOTT audit", "Data quality", "Load data"])


def q(sql, params=()):
    return pd.read_sql_query(sql, con, params=params)


if page == "Cards":
    st.title("Cards")
    cards = q("SELECT card_id, track, race_date, source, source_file, ingested_at FROM cards ORDER BY ingested_at DESC")
    st.dataframe(cards, width="stretch", hide_index=True)
    if not cards.empty:
        cid = st.selectbox("Card", cards.card_id)
        g = q("SELECT race, pgm, horse_name, bucket, modifiers, tags, ml_odds, grade_confidence, scratched "
              "FROM pre_race_grades WHERE card_id=? ORDER BY race, CAST(pgm AS INT)", (cid,))
        races = q("SELECT race, field_size, qsp_sum, pace_scenario, grades_complete FROM races WHERE card_id=?", (cid,))
        c1, c2, c3 = st.columns(3)
        c1.metric("Runners graded", int(g.bucket.notna().sum()))
        c2.metric("Races fully graded", f"{int(races.grades_complete.sum())} / {len(races)}")
        c3.metric("F- locks", int((g.bucket == "F-").sum()))
        for race, grp in g.groupby("race"):
            with st.expander(f"Race {race}  ·  " + "  ".join(f"#{p} {b}" for p, b in zip(grp.pgm, grp.bucket.fillna("?")))):
                st.dataframe(grp.drop(columns="race"), width="stretch", hide_index=True)

elif page == "Tickets":
    st.title("Tickets")
    t = q("SELECT card_id, label, pool, race, base, selections, combos, cost, claimed_combos, claimed_cost, issues FROM tickets")
    if t.empty:
        st.info("No tickets loaded.")
    else:
        t["legs"] = t.selections.map(lambda s: " / ".join(",".join(l) for l in json.loads(s)))
        t["ok"] = t.issues.map(lambda s: not json.loads(s))
        only_bad = st.checkbox("Only tickets with issues")
        view = t[~t.ok] if only_bad else t
        st.metric("Total cost", f"${view.cost.sum():,.2f}")
        st.dataframe(view[["card_id", "pool", "race", "base", "legs", "combos", "cost", "claimed_combos",
                           "claimed_cost", "ok", "issues", "label"]], width="stretch", hide_index=True)
        graded = analytics.grade_tickets(con)
        if not graded.empty:
            st.subheader("Results")
            st.dataframe(graded, width="stretch", hide_index=True)

elif page == "Hit rates":
    st.title("Bucket hit rates")
    by_field = st.checkbox("Split by field size")
    hr = analytics.bucket_hit_rates(con, by_field)
    if hr.empty:
        st.info("Needs cards with a race_date plus imported results.")
    else:
        st.dataframe(hr, width="stretch", hide_index=True)
        st.caption("flat_win_roi uses final odds as a payout proxy. Small samples are noise; watch the starters column.")

elif page == "LOTT audit":
    st.title("Left on the table")
    lott = analytics.lott_audit(con)
    if lott.empty:
        st.info("Needs payouts imported for cards with a race_date.")
    else:
        left = lott[lott.left_on_table]
        c1, c2, c3 = st.columns(3)
        c1.metric("Payouts audited", len(lott))
        c2.metric("Covered by grades", int(lott.system_covered.sum()))
        c3.metric("Left on the table", f"{len(left)}  (${left.payout.sum():,.0f})")
        st.dataframe(lott, width="stretch", hide_index=True)
        st.subheader("Winning sequence signatures (P4/P5/P6)")
        st.dataframe(analytics.sequence_signatures(con), width="stretch", hide_index=True)

elif page == "Data quality":
    st.title("Data quality flags")
    st.dataframe(q("SELECT card_id, race, severity, message, created_at FROM data_quality_flags ORDER BY flag_id DESC"),
                 width="stretch", hide_index=True)

elif page == "Load data":
    st.title("Load data")
    kind = st.radio("Type", ["Tail block JSON", "Archived chat output (.txt)", "Brisnet charts ZIP", "Results CSV", "Payouts CSV"], horizontal=True)
    date = st.text_input("Race date for archive (YYYY-MM-DD, optional)") if kind.startswith("Archived") else None
    f = st.file_uploader("File")
    if f and st.button("Load"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(f.name).suffix) as tmp:
            tmp.write(f.getvalue())
        if kind.startswith("Tail"):
            st.success(f"Loaded {db.ingest_tail_block(con, tmp.name)}")
        elif kind.startswith("Archived"):
            src = Path(tmp.name).with_name(f.name)
            Path(tmp.name).rename(src)
            cid, stats = ingest_legacy(con, src, race_date=date or None)
            st.success(f"Loaded {cid}: {stats}")
        elif kind.startswith("Brisnet"):
            from pe.brisnet import import_brisnet_zip
            st.success(f"Loaded: {import_brisnet_zip(con, tmp.name)}")
        elif kind.startswith("Results"):
            st.success(f"{db.import_results_csv(con, tmp.name)} result rows")
        else:
            st.success(f"{db.import_payouts_csv(con, tmp.name)} payout rows")
