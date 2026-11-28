"""F1 Pace Lab: practice pace analysis and a race tracker, built on OpenF1 data.

Run with:  uv run streamlit run app.py
"""

import streamlit as st

from pacelab.client import OpenF1LockedError
from pacelab.ui import CSS

st.set_page_config(page_title="F1 Pace Lab", page_icon="🏁", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

page = st.navigation(
    [
        st.Page("views/weekend.py", title="Weekend analysis", icon="⏱️", default=True),
        st.Page("views/tracker.py", title="Race tracker", icon="🏎️"),
    ],
    position="top",
)
try:
    page.run()
except OpenF1LockedError as exc:
    st.warning(
        f"**{exc}**\n\nAnything already loaded once still works from the local cache. "
        "Try again after the session, or add an OpenF1 sponsor account to keep full access."
    )
