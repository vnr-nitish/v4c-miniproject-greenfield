"""Operations: run the warehouse ETL from the browser and inspect the batch log."""
import time

import pandas as pd
import streamlit as st

from src.dal import AnalyticsManager, EtlManager
from src.ui.common import attempt, editor_unlocked, page_header

page_header("ETL & data health", "Run the stored-procedure ETL (OLTP → star schema) and read its audit trail")
etl, am = EtlManager(), AnalyticsManager()
version = attempt(am.data_version)
if version:
    st.caption(f"Warehouse version: batch {version[0]} · analytics cache entries: {len(am._cache)}")

if editor_unlocked():
    st.markdown("Loads new reviews and projects into the warehouse (incremental, about 1–3 minutes).")
    if st.button("Run warehouse ETL", type="primary"):
        started = time.perf_counter()
        with st.status("Running hr_dw.sp_run_etl …", expanded=True) as status:
            batch_id = attempt(etl.run_warehouse_etl)
            if batch_id:
                for step in etl.batch_steps(batch_id):
                    st.write(f"✅ {step['step_name']}: {step['rows_affected']:,} rows — {step['message'] or ''}")
                status.update(label=f"ETL batch {batch_id} finished in {time.perf_counter() - started:.0f} s",
                              state="complete")
            else:
                status.update(label="ETL failed — see the message above", state="error")

st.subheader("Recent batches")
batches = attempt(lambda: etl.recent_batches(15))
if batches:
    frame = pd.DataFrame(batches)
    event = st.dataframe(frame, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row")
    if event.selection.rows:
        batch_id = int(frame.iloc[event.selection.rows[0]].batch_id)
        st.dataframe(pd.DataFrame(etl.batch_steps(batch_id)), hide_index=True, width="stretch")
        rejects = etl.rejected_rows(batch_id)
        if rejects:
            st.warning(f"{len(rejects)} rejected rows")
            st.dataframe(pd.DataFrame(rejects), hide_index=True, width="stretch")
