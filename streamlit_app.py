"""Entry point under Streamlit Community Cloud's default name; the application itself is app.py
(run locally with `streamlit run app.py` or `streamlit run streamlit_app.py` — both are the same app)."""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
