"""Monthly HR Dashboard (Streamlit + Plotly)

Run:
    pip install streamlit pandas plotly numpy
    streamlit run hr_dashboard.py

CSV columns (upload in the sidebar, or leave empty to use sample data):
    employee_id, department, hire_date, exit_date (blank if active),
    salary, absent_days (optional: days absent in the selected month)
"""
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Monthly HR Dashboard", layout="wide")


@st.cache_data
def sample_data(n=150, seed=7):
    rng = np.random.default_rng(seed)
    depts = ["Engineering", "Sales", "Support", "HR", "Finance", "Operations"]
    hire = pd.Timestamp("2023-01-01") + pd.to_timedelta(rng.integers(0, 1400, n), unit="D")
    df = pd.DataFrame({
        "employee_id": range(1, n + 1),
        "department": rng.choice(depts, n),
        "hire_date": hire,
        "salary": rng.normal(55000, 15000, n).round(-2).clip(25000),
        "absent_days": rng.poisson(1.2, n),
    })
    exit_dates = hire + pd.to_timedelta(rng.integers(90, 900, n), unit="D")
    df["exit_date"] = pd.Series(exit_dates).where(rng.random(n) < 0.2)
    df.loc[df["exit_date"] > pd.Timestamp.today(), "exit_date"] = pd.NaT
    return df


def load(file):
    df = pd.read_csv(file, parse_dates=["hire_date", "exit_date"]) if file else sample_data()
    if "absent_days" not in df:
        df["absent_days"] = 0
    return df


def headcount_on(df, date):
    return int(((df.hire_date <= date) & (df.exit_date.isna() | (df.exit_date > date))).sum())


# ---------- Sidebar ----------
st.sidebar.header("Filters")
df = load(st.sidebar.file_uploader("Upload employee CSV", type="csv"))

depts = sorted(df.department.dropna().unique())
chosen = st.sidebar.multiselect("Department", depts, default=depts)
df = df[df.department.isin(chosen)]

months = pd.period_range(df.hire_date.min(), pd.Timestamp.today(), freq="M")
month = st.sidebar.selectbox("Month", months[::-1], index=0)
start, end = month.start_time, month.end_time

# ---------- KPIs ----------
hc_start = headcount_on(df, start - pd.Timedelta(days=1))
hc_end = headcount_on(df, end)
hires = int(df.hire_date.between(start, end).sum())
exits = int(df.exit_date.between(start, end).sum())
avg_hc = (hc_start + hc_end) / 2 or 1
attrition = exits / avg_hc * 100

active = df[(df.hire_date <= end) & (df.exit_date.isna() | (df.exit_date > end))]
work_days = np.busday_count(start.date(), (end + pd.Timedelta(days=1)).date())
absenteeism = active.absent_days.sum() / max(len(active) * work_days, 1) * 100

st.title(f"HR Dashboard: {month.strftime('%B %Y')}")
c = st.columns(6)
c[0].metric("Headcount", hc_end, hc_end - hc_start)
c[1].metric("New hires", hires)
c[2].metric("Exits", exits)
c[3].metric("Attrition (month)", f"{attrition:.1f}%")
c[4].metric("Absenteeism", f"{absenteeism:.1f}%")
c[5].metric("Avg salary", f"{active.salary.mean():,.0f}" if len(active) else "-")

# ---------- Trend data (last 12 months) ----------
last12 = months[-12:]
trend = pd.DataFrame({
    "month": [m.strftime("%b %Y") for m in last12],
    "headcount": [headcount_on(df, m.end_time) for m in last12],
    "hires": [int(df.hire_date.between(m.start_time, m.end_time).sum()) for m in last12],
    "exits": [int(df.exit_date.between(m.start_time, m.end_time).sum()) for m in last12],
})

left, right = st.columns(2)
left.plotly_chart(px.line(trend, x="month", y="headcount", markers=True,
                          title="Headcount (last 12 months)"), use_container_width=True)
right.plotly_chart(px.bar(trend, x="month", y=["hires", "exits"], barmode="group",
                          title="Hires vs exits"), use_container_width=True)

# ---------- Department view ----------
by_dept = active.groupby("department").agg(
    headcount=("employee_id", "count"), avg_salary=("salary", "mean")
).reset_index()
left, right = st.columns(2)
left.plotly_chart(px.bar(by_dept, x="department", y="headcount",
                         title="Headcount by department"), use_container_width=True)
right.plotly_chart(px.bar(by_dept, x="department", y="avg_salary",
                          title="Average salary by department"), use_container_width=True)

with st.expander("Employee data"):
    st.dataframe(active, use_container_width=True)
