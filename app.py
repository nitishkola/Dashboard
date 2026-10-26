# app.py
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from retail import ai_tabs, data

st.set_page_config(page_title="Retail Analytics Dashboard", layout="wide")
st.title("🛒 Universal Retail Analytics Platform")
st.markdown("Upload any retail Excel file — dashboard adapts automatically")

# -------------------------------
# File Uploader
# -------------------------------
SAMPLE_FILE = Path(__file__).parent / "Sales-Dashboard-practice-file.xlsx"

uploaded_file = st.sidebar.file_uploader("Upload Excel File", type=["xlsx"])
if not uploaded_file and SAMPLE_FILE.exists():
    if st.sidebar.button("📂 Try the sample data") or st.session_state.get("use_sample"):
        st.session_state["use_sample"] = True
        uploaded_file = str(SAMPLE_FILE)
if not uploaded_file:
    st.info("👆 Please upload an Excel file to begin.")
    st.stop()

df, col_map = data.load_universal_data(uploaded_file)
if df is None:
    st.error(col_map["error"])
    st.stop()

# Sales added from ledger photos in this session
extra_rows = st.session_state.get("extra_rows")
if extra_rows is not None and not extra_rows.empty:
    df = pd.concat([df, extra_rows], ignore_index=True)

# -------------------------------
# Sidebar Filters
# -------------------------------
ai_tabs.provider_caption()
st.sidebar.header("🔍 Filters")
selected_categories = st.sidebar.multiselect(
    "Category",
    options=df['CATEGORY'].unique(),
    default=df['CATEGORY'].unique()
)
filtered_df = df[df['CATEGORY'].isin(selected_categories)].copy()

if filtered_df.empty:
    st.warning("No data matches the selected filters.")
    st.stop()

# -------------------------------
# Metrics Summary
# -------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Revenue", data.money(filtered_df['REVENUE'].sum()))
col2.metric("Total Profit", data.money(filtered_df['PROFIT'].sum()))
if filtered_df['REVENUE'].sum() > 0:
    margin = (filtered_df['PROFIT'].sum() / filtered_df['REVENUE'].sum()) * 100
    col3.metric("Avg Margin", f"{margin:.1f}%")
else:
    col3.metric("Avg Margin", "N/A")
col4.metric("Transactions", f"{len(filtered_df):,}")

# -------------------------------
# Tabs
# -------------------------------
(tab1, tab2, tab3, tab4, tab5,
 tab_ask, tab_why, tab_plan, tab_photo, tab_brief) = st.tabs([
    "📈 Behavior Trends",
    "🛍️ Product & Category",
    "📍 Layout & Pricing",
    "🤝 Recommendations",
    "🧠 Forecast & Optimization",
    ai_tabs.TAB_ASK,
    ai_tabs.TAB_WHY,
    ai_tabs.TAB_PLAN,
    ai_tabs.TAB_PHOTO,
    ai_tabs.TAB_BRIEF,
], default=st.session_state.get("active_tab"))

# -------------------------------
# Tab 1: Behavior Trends
# -------------------------------
with tab1:
    daily = data.daily_revenue(filtered_df)
    fig1 = px.line(daily, x='DATE', y='REVENUE', title="Daily Revenue")
    fig1.update_yaxes(tickprefix=data.CURRENCY)
    st.plotly_chart(fig1, width="stretch")

    monthly = filtered_df.groupby(filtered_df['DATE'].dt.to_period('M'))['REVENUE'].sum().reset_index()
    monthly['MONTH'] = monthly['DATE'].astype(str)
    fig2 = px.bar(monthly, x='MONTH', y='REVENUE', title="Monthly Revenue")
    fig2.update_yaxes(tickprefix=data.CURRENCY)
    st.plotly_chart(fig2, width="stretch")

# -------------------------------
# Tab 2: Product & Category
# -------------------------------
with tab2:
    cat_rev = filtered_df.groupby('CATEGORY')['REVENUE'].sum().reset_index()
    fig_cat = px.bar(cat_rev, x='CATEGORY', y='REVENUE', title="Revenue by Category")
    fig_cat.update_yaxes(tickprefix=data.CURRENCY)
    st.plotly_chart(fig_cat, width="stretch")

    top_products = filtered_df.groupby('PRODUCT')['REVENUE'].sum().nlargest(10).reset_index()
    st.subheader("Top 10 Products by Revenue")
    st.dataframe(top_products, width="stretch")

# -------------------------------
# Tab 3: Layout & Pricing
# -------------------------------
with tab3:
    st.subheader("🏪 Store Layout: High-Traffic Products")
    layout_df = data.product_velocity(filtered_df).sort_values('Sales_Velocity', ascending=False).head(15)
    st.dataframe(layout_df[['PRODUCT', 'Sales_Velocity']], width="stretch")

    st.subheader("💲 Dynamic Pricing Suggestions")
    price_df = data.pricing_table(filtered_df)
    st.dataframe(price_df[['PRODUCT', 'Avg_Price', 'Total_Quantity', 'Demand_Level', 'Action']],
                 width="stretch")

    # Discount Simulator
    st.markdown("---")
    st.subheader("🎯 Discount Impact Simulator")
    selected_product = st.selectbox("Select Product", sorted(filtered_df['PRODUCT'].unique()))
    discount_pct = st.slider("Discount %", 0, 30, 10)

    if selected_product:
        sim = data.simulate_price_change(filtered_df, selected_product, -discount_pct)
        st.caption(f"Price sensitivity {sim['elasticity']['elasticity']} ({sim['elasticity']['source']}). "
                   "Fine-tune it in the 🤖 Ask AI tab.")
        st.metric("Current 30-day Profit", data.money(sim['current_30d_profit'], 2))
        st.metric("Projected 30-day Profit", data.money(sim['projected_30d_profit'], 2),
                  delta=data.money(sim['projected_30d_profit'] - sim['current_30d_profit'], 2, signed=True))
        if sim['projected_30d_profit'] > sim['current_30d_profit']:
            st.success("✅ Discount is profitable!")
        else:
            st.warning("⚠️ Discount may reduce profit.")

# -------------------------------
# Tab 4: Recommendations
# -------------------------------
with tab4:
    st.subheader("🔗 Product Pairing Recommendations (Co-purchase)")
    st.caption("Products sold on the same day. Add an invoice or bill number column for true basket analysis.")
    recs_df = data.co_purchase(filtered_df).head(20)
    if not recs_df.empty:
        st.dataframe(recs_df, width="stretch")
    else:
        st.info("Not enough co-purchase data to generate recommendations.")

# -------------------------------
# Tab 5: Forecast & Optimization
# -------------------------------
with tab5:
    st.subheader("📈 30-Day Revenue Forecast")
    daily_rev = data.daily_revenue(filtered_df)
    if len(daily_rev) >= 10:
        try:
            forecast = data.prophet_forecast(daily_rev)
            fig_fcst = px.line(forecast, x='ds', y='yhat', title="30-Day Revenue Forecast")
            fig_fcst.add_scatter(x=forecast['ds'], y=forecast['yhat_upper'], mode='lines', name='Upper',
                                 line=dict(dash='dot', color='green'))
            fig_fcst.add_scatter(x=forecast['ds'], y=forecast['yhat_lower'], mode='lines', name='Lower',
                                 line=dict(dash='dot', color='red'))
            fig_fcst.update_yaxes(tickprefix=data.CURRENCY)
            st.plotly_chart(fig_fcst, width="stretch")
        except Exception as e:
            st.warning(f"⚠️ Forecast failed: {e}")
    else:
        st.info("Not enough data for forecasting.")

    st.markdown("---")
    st.subheader("🗓️ Best Days for Discounts")
    dow_rev = data.weekday_pattern(filtered_df)
    fig_dow = px.bar(dow_rev, x='Weekday', y='Avg Daily Revenue', title="Average Revenue by Weekday")
    fig_dow.update_yaxes(tickprefix=data.CURRENCY)
    st.plotly_chart(fig_dow, width="stretch")
    low_days = dow_rev.nsmallest(2, 'Avg Daily Revenue')['Weekday'].tolist()
    st.markdown(f"💡 **Recommendation**: Apply discounts on **{', '.join(low_days)}** to boost sales.")

    st.markdown("---")
    st.subheader("🧹 Clearance Sale Candidates")
    clearance_list = data.clearance_candidates(filtered_df).head(15)
    if not clearance_list.empty:
        st.dataframe(clearance_list[['PRODUCT', 'Sales_Velocity', 'Days_Since_Last_Sale']], width="stretch")
        st.markdown("💡 **Action**: Schedule **clearance sales** on these items.")
    else:
        st.info("✅ No clearance candidates detected.")

    st.markdown("---")
    st.subheader("🏪 ABC Analysis for Store Layout")
    abc_rev = data.abc_analysis(filtered_df)
    st.dataframe(abc_rev[['PRODUCT', 'REVENUE', 'ABC']], width="stretch")
    st.markdown("""
    - **A Products (Top 80% revenue)** → Prime shelf space
    - **B Products (Next 15%)** → Secondary placement
    - **C Products (Bottom 5%)** → Consider bundling or discounting
    """)

# -------------------------------
# AI Tabs
# -------------------------------
with tab_ask:
    ai_tabs.ask_tab(filtered_df)
with tab_why:
    ai_tabs.why_tab(filtered_df)
with tab_plan:
    ai_tabs.actions_tab(filtered_df)
with tab_photo:
    ai_tabs.photo_tab(df)
with tab_brief:
    ai_tabs.brief_tab(filtered_df)

# -------------------------------
# Export Button
# -------------------------------
st.sidebar.markdown("---")
st.sidebar.download_button(
    label="📥 Download Full Report (Excel)",
    data=data.to_excel_bytes({
        "Daily_Trends": daily_rev,
        "Top_Products": top_products,
        "Layout_Suggestions": layout_df.drop(columns=['Last_Sold']),
        "Pricing_Suggestions": price_df,
        "ABC_Analysis": abc_rev[['PRODUCT', 'REVENUE', 'ABC']],
        "Recommendations": recs_df,
    }),
    file_name="retail_dashboard_export.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

st.caption("© 2025 Universal Retail Analytics Platform")
