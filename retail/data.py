# retail/data.py
# Data loading and every calculation the dashboard and the AI features use.
# The AI never computes numbers itself: it calls these functions and explains the results.
import io
from collections import Counter
from itertools import combinations

import numpy as np
import pandas as pd
import streamlit as st

CURRENCY = "₹"


def money(amount, decimals: int = 0, signed: bool = False) -> str:
    """Format an amount as rupees with Indian digit grouping: 1234567.5 -> ₹12,34,568."""
    sign = "-" if amount < 0 else ("+" if signed and amount > 0 else "")
    whole, _, frac = f"{abs(amount):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join([head] + groups + [tail]) if head else ",".join(groups + [tail])
    return f"{sign}{CURRENCY}{whole}" + (f".{frac}" if frac else "")


WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
DEFAULT_ELASTICITY = -1.2  # used when the data has no price variation to learn from


# -------------------------------
# Universal Data Loader
# -------------------------------
def _find_column(df, cols_upper, candidates):
    for col in candidates:
        if col in cols_upper:
            return df.columns[cols_upper.index(col)]
    return None


@st.cache_data
def load_universal_data(file):
    xls = pd.ExcelFile(file)
    dfs = [pd.read_excel(file, sheet_name=s) for s in xls.sheet_names]

    # Merge if multiple sheets
    if len(dfs) == 1:
        df = dfs[0]
    else:
        main = max(dfs, key=len)
        others = [d for d in dfs if d is not main]
        df = main.copy()
        for other in others:
            common = set(df.columns) & set(other.columns)
            if common:
                df = df.merge(other, on=list(common), how='left')
            else:
                other = other.add_suffix('_extra')
                df = pd.concat([df.reset_index(drop=True), other.reset_index(drop=True)], axis=1)

    # Map columns (case-insensitive)
    cols_upper = [str(c).upper() for c in df.columns]
    col_map = {}

    orig = _find_column(df, cols_upper, ['DATE', 'ORDERDATE', 'DELIVERYDATE'])
    if orig is None:
        return None, {"error": "No date column found."}
    df['DATE'] = pd.to_datetime(df[orig], errors='coerce')
    col_map['DATE'] = orig

    orig = _find_column(df, cols_upper, ['PRODUCT', 'ITEM', 'PRODUCTNAME'])
    df['PRODUCT'] = df[orig].astype(str) if orig is not None else 'Unknown'
    col_map['PRODUCT'] = orig

    orig = _find_column(df, cols_upper, ['QUANTITY', 'QTY'])
    if orig is None:
        return None, {"error": "No valid quantity column found."}
    df['QUANTITY'] = pd.to_numeric(df[orig], errors='coerce')
    if df['QUANTITY'].isna().all():
        return None, {"error": "No valid quantity column found."}
    col_map['QUANTITY'] = orig

    orig = _find_column(df, cols_upper, ['UNITPRICE', 'PRICE', 'SELLING PRICE', 'SELLING_PRICE'])
    df['PRICE'] = pd.to_numeric(df[orig], errors='coerce') if orig is not None else 0
    col_map['PRICE'] = orig

    orig = _find_column(df, cols_upper, ['COST', 'BUYING PRIZE', 'BUYING_PRIZE', 'BUYING PRICE', 'UNITCOST'])
    df['COST'] = pd.to_numeric(df[orig], errors='coerce') if orig is not None else 0
    col_map['COST'] = orig

    orig = _find_column(df, cols_upper, ['CATEGORY', 'PRODUCTTYPE', 'TYPE'])
    df['CATEGORY'] = df[orig].astype(str) if orig is not None else 'General'
    col_map['CATEGORY'] = orig

    # Discounts lower the price actually paid, so revenue uses the effective price
    orig = _find_column(df, cols_upper, ['DISCOUNT %', 'DISCOUNT', 'DISCOUNT_PCT'])
    df['DISCOUNT_PCT'] = pd.to_numeric(df[orig], errors='coerce').fillna(0) if orig is not None else 0
    col_map['DISCOUNT_PCT'] = orig

    df['EFFECTIVE_PRICE'] = df['PRICE'] * (1 - df['DISCOUNT_PCT'] / 100)
    df['REVENUE'] = df['EFFECTIVE_PRICE'] * df['QUANTITY']
    df['PROFIT'] = (df['EFFECTIVE_PRICE'] - df['COST']) * df['QUANTITY']

    df = df.dropna(subset=['DATE', 'QUANTITY'])
    return df, col_map


# -------------------------------
# Rows added from a ledger photo
# -------------------------------
def enrich_rows(rows: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Turn DATE/PRODUCT/QUANTITY/PRICE rows into full dashboard rows, borrowing
    category and cost from the products already in the data."""
    known = df.sort_values('DATE').groupby('PRODUCT').last()
    out = rows.copy()
    out['DATE'] = pd.to_datetime(out['DATE'], errors='coerce')
    out['QUANTITY'] = pd.to_numeric(out['QUANTITY'], errors='coerce')
    out['PRICE'] = pd.to_numeric(out['PRICE'], errors='coerce')
    out = out.dropna(subset=['DATE', 'QUANTITY'])
    out = out[out['QUANTITY'] > 0]

    out['CATEGORY'] = out['PRODUCT'].map(known['CATEGORY']).fillna('General')
    out['COST'] = out['PRODUCT'].map(known['COST']).fillna(0)
    missing_price = out['PRICE'].isna() | (out['PRICE'] <= 0)
    out.loc[missing_price, 'PRICE'] = out.loc[missing_price, 'PRODUCT'].map(known['PRICE'])
    out['PRICE'] = out['PRICE'].fillna(0)
    if 'PRODUCT ID' in known.columns:
        out['PRODUCT ID'] = out['PRODUCT'].map(known['PRODUCT ID'])
    out['DISCOUNT_PCT'] = 0
    out['EFFECTIVE_PRICE'] = out['PRICE']
    out['REVENUE'] = out['EFFECTIVE_PRICE'] * out['QUANTITY']
    out['PROFIT'] = (out['EFFECTIVE_PRICE'] - out['COST']) * out['QUANTITY']
    out['SOURCE'] = 'photo import'
    return out


def to_excel_bytes(sheets: dict) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for sheet, data in sheets.items():
            if isinstance(data, pd.DataFrame) and not data.empty:
                data.to_excel(writer, sheet_name=sheet[:31], index=False)
    return output.getvalue()


# -------------------------------
# Core analytics
# -------------------------------
def demand_levels(qty: pd.Series) -> pd.Series:
    """Low/Medium/High thirds. Ranking first avoids qcut failing on tied quantities."""
    if len(qty) < 3:
        return pd.Series('Medium', index=qty.index)
    return pd.qcut(qty.rank(method='first'), q=3, labels=['Low', 'Medium', 'High']).astype(str)


def product_velocity(df: pd.DataFrame) -> pd.DataFrame:
    v = df.groupby('PRODUCT').agg(
        Total_Quantity=('QUANTITY', 'sum'),
        Days_Sold=('DATE', 'nunique'),
        Last_Sold=('DATE', 'max'),
    ).reset_index()
    v['Sales_Velocity'] = v['Total_Quantity'] / v['Days_Sold']
    v['Days_Since_Last_Sale'] = (df['DATE'].max() - v['Last_Sold']).dt.days
    return v


def pricing_table(df: pd.DataFrame) -> pd.DataFrame:
    price_df = df.groupby('PRODUCT').agg(
        Avg_Price=('PRICE', 'mean'),
        Total_Quantity=('QUANTITY', 'sum')
    ).reset_index()
    price_df['Demand_Level'] = demand_levels(price_df['Total_Quantity'])
    price_df['Action'] = np.where(
        price_df['Demand_Level'] == 'High',
        "✅ Consider +3–5% price increase",
        "⏸️ Maintain price"
    )
    return price_df


def clearance_candidates(df: pd.DataFrame) -> pd.DataFrame:
    v = product_velocity(df)
    if v.empty:
        return v
    cutoff = v['Sales_Velocity'].quantile(0.25)
    v = v[(v['Sales_Velocity'] <= cutoff) | (v['Days_Since_Last_Sale'] > 60)]
    return v.sort_values('Sales_Velocity')


def abc_analysis(df: pd.DataFrame) -> pd.DataFrame:
    abc = df.groupby('PRODUCT')['REVENUE'].sum().reset_index().sort_values('REVENUE', ascending=False)
    total = abc['REVENUE'].sum()
    abc['Cumulative %'] = (abc['REVENUE'].cumsum() / total * 100) if total else 0
    abc['ABC'] = abc['Cumulative %'].apply(lambda x: 'A' if x <= 80 else ('B' if x <= 95 else 'C'))
    return abc


def co_purchase(df: pd.DataFrame, min_count: int = 2) -> pd.DataFrame:
    """Pairs sold on the same day. Without an invoice ID this is a proxy, not true baskets."""
    pair_counts = Counter()
    for basket in df.groupby('DATE')['PRODUCT'].apply(set):
        if len(basket) > 1:
            for a, b in combinations(sorted(basket), 2):
                pair_counts[(a, b)] += 1
    recs = [{"Product A": a, "Product B": b, "Together in Baskets": c}
            for (a, b), c in pair_counts.items() if c >= min_count]
    if not recs:
        return pd.DataFrame(columns=["Product A", "Product B", "Together in Baskets"])
    return pd.DataFrame(recs).sort_values('Together in Baskets', ascending=False)


def daily_revenue(df: pd.DataFrame) -> pd.DataFrame:
    return df.groupby('DATE')['REVENUE'].sum().reset_index()


@st.cache_data(show_spinner="Training forecast model...")
def prophet_forecast(daily: pd.DataFrame, periods: int = 30) -> pd.DataFrame:
    from prophet import Prophet
    model = Prophet(yearly_seasonality=True, weekly_seasonality=True)
    model.fit(daily.rename(columns={'DATE': 'ds', 'REVENUE': 'y'}))
    return model.predict(model.make_future_dataframe(periods=periods))


def weekday_pattern(df: pd.DataFrame) -> pd.DataFrame:
    """Average revenue per calendar day for each weekday (days with no sales count as zero)."""
    daily = df.groupby('DATE')['REVENUE'].sum()
    if daily.empty:
        return pd.DataFrame(columns=['Weekday', 'Avg Daily Revenue'])
    full = daily.reindex(pd.date_range(daily.index.min(), daily.index.max()), fill_value=0)
    out = full.groupby(full.index.day_name()).mean().reindex(WEEKDAYS).reset_index()
    out.columns = ['Weekday', 'Avg Daily Revenue']
    return out


# -------------------------------
# Periods and "why did it change?"
# -------------------------------
def parse_date(value, default):
    if value in (None, ""):
        return default
    return pd.Timestamp(value)


def default_periods(df: pd.DataFrame, days: int = 30):
    """Last `days` days of data versus the `days` before that."""
    cur_end = df['DATE'].max().normalize()
    cur_start = cur_end - pd.Timedelta(days=days - 1)
    prev_end = cur_start - pd.Timedelta(days=1)
    prev_start = prev_end - pd.Timedelta(days=days - 1)
    return cur_start, cur_end, prev_start, prev_end


def in_period(df: pd.DataFrame, start, end) -> pd.DataFrame:
    return df[(df['DATE'] >= pd.Timestamp(start)) & (df['DATE'] <= pd.Timestamp(end))]


def summarize(df: pd.DataFrame, start=None, end=None) -> dict:
    start = parse_date(start, df['DATE'].min())
    end = parse_date(end, df['DATE'].max())
    p = in_period(df, start, end)
    days = (end - start).days + 1
    revenue = float(p['REVENUE'].sum())
    profit = float(p['PROFIT'].sum())
    return {
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "days": days,
        "revenue": round(revenue, 2),
        "profit": round(profit, 2),
        "margin_pct": round(profit / revenue * 100, 1) if revenue else None,
        "units": float(p['QUANTITY'].sum()),
        "transactions": int(len(p)),
        "avg_daily_revenue": round(revenue / days, 2) if days > 0 else None,
        "top_products": p.groupby('PRODUCT')['REVENUE'].sum().nlargest(5).round(2).to_dict(),
        "revenue_by_category": p.groupby('CATEGORY')['REVENUE'].sum().round(2).to_dict(),
        "profit_by_category": p.groupby('CATEGORY')['PROFIT'].sum().round(2).to_dict(),
        "units_by_category": p.groupby('CATEGORY')['QUANTITY'].sum().to_dict(),
    }


def explain_change(df, cur_start, cur_end, prev_start, prev_end) -> dict:
    """Split the revenue change between two periods into three causes that add up exactly:
    volume (more/fewer units), mix (shift between cheap and expensive products) and
    price (the same products sold at different prices)."""
    cur = in_period(df, cur_start, cur_end)
    prev = in_period(df, prev_start, prev_end)

    def by_product(p):
        g = p.groupby('PRODUCT').agg(units=('QUANTITY', 'sum'), revenue=('REVENUE', 'sum'))
        g['price'] = g['revenue'] / g['units'].replace(0, np.nan)
        return g

    g0, g1 = by_product(prev), by_product(cur)
    r0, r1 = float(prev['REVENUE'].sum()), float(cur['REVENUE'].sum())
    q0, q1 = float(prev['QUANTITY'].sum()), float(cur['QUANTITY'].sum())

    # A product missing from the old period has no old price; using its new price gives it no price effect
    p0 = g0['price'].reindex(g1.index).fillna(g1['price'])
    avg_p0 = r0 / q0 if q0 else 0
    volume = (q1 - q0) * avg_p0
    price = float((g1['units'] * (g1['price'] - p0)).sum())
    mix = (r1 - r0) - volume - price

    drivers = pd.DataFrame({
        'previous_revenue': g0['revenue'],
        'current_revenue': g1['revenue'],
        'previous_units': g0['units'],
        'current_units': g1['units'],
    }).fillna(0)
    drivers['change'] = drivers['current_revenue'] - drivers['previous_revenue']
    drivers = drivers.reindex(drivers['change'].abs().sort_values(ascending=False).index)

    cat0 = prev.groupby('CATEGORY')['REVENUE'].sum()
    cat1 = cur.groupby('CATEGORY')['REVENUE'].sum()
    cats = (cat1.sub(cat0, fill_value=0)).sort_values()

    return {
        "current_period": f"{pd.Timestamp(cur_start).date()} to {pd.Timestamp(cur_end).date()}",
        "previous_period": f"{pd.Timestamp(prev_start).date()} to {pd.Timestamp(prev_end).date()}",
        "previous_revenue": round(r0, 2),
        "current_revenue": round(r1, 2),
        "change": round(r1 - r0, 2),
        "change_pct": round((r1 - r0) / r0 * 100, 1) if r0 else None,
        "previous_units": q0,
        "current_units": q1,
        "effects": {
            "volume": round(volume, 2),
            "mix": round(mix, 2),
            "price": round(price, 2),
        },
        "top_product_drivers": drivers.head(6).round(2).reset_index(names='product').to_dict('records'),
        "category_changes": cats.round(2).to_dict(),
        "products_not_sold_now": sorted(set(g0.index) - set(g1.index))[:10],
        "new_products_sold": sorted(set(g1.index) - set(g0.index))[:10],
    }


def weekly_anomalies(df: pd.DataFrame, threshold: float = 2.0) -> pd.DataFrame:
    """Weeks whose revenue is unusually far from the previous 8 weeks."""
    weekly = df.set_index('DATE')['REVENUE'].resample('W').sum()
    base = weekly.shift(1).rolling(8, min_periods=4)
    z = (weekly - base.mean()) / base.std().replace(0, np.nan)
    out = pd.DataFrame({'week_ending': weekly.index, 'revenue': weekly.values,
                        'typical': base.mean().values, 'z_score': z.values}).dropna()
    out = out[out['z_score'].abs() >= threshold].copy()
    out['direction'] = np.where(out['z_score'] > 0, 'spike', 'drop')
    return out.round(2)


# -------------------------------
# What-if pricing
# -------------------------------
def estimate_elasticity(df: pd.DataFrame, product: str) -> dict:
    """Learn how this product's demand reacts to price from its own history (log-log fit of
    units per sale against the price paid). Needs real price variation, e.g. from discounts."""
    p = df[(df['PRODUCT'] == product) & (df['EFFECTIVE_PRICE'] > 0) & (df['QUANTITY'] > 0)]
    if len(p) >= 8 and p['EFFECTIVE_PRICE'].round(2).nunique() >= 2:
        slope = np.polyfit(np.log(p['EFFECTIVE_PRICE']), np.log(p['QUANTITY']), 1)[0]
        if -5 < slope < 0:
            return {"elasticity": round(float(slope), 2), "source": "learned from this product's sales",
                    "observations": int(len(p))}
    return {"elasticity": DEFAULT_ELASTICITY,
            "source": "assumed (prices never changed in the data, so there is nothing to learn from)",
            "observations": int(len(p))}


def simulate_price_change(df: pd.DataFrame, product: str, percent_change: float,
                          elasticity: float = None) -> dict:
    """Projected 30-day result of changing a product's price, based on its last 90 days."""
    p = df[df['PRODUCT'] == product]
    if p.empty:
        return {"error": f"Unknown product '{product}'."}
    recent = p[p['DATE'] > p['DATE'].max() - pd.Timedelta(days=90)]
    info = estimate_elasticity(df, product)
    e = info['elasticity'] if elasticity is None else elasticity
    if elasticity is not None:
        info = {**info, "elasticity": e, "source": "set by the user"}

    units_30 = recent['QUANTITY'].sum() / 90 * 30
    price = recent['EFFECTIVE_PRICE'].mean()
    cost = recent['COST'].mean()
    new_price = price * (1 + percent_change / 100)
    new_units = units_30 * (1 + percent_change / 100) ** e
    return {
        "product": product,
        "percent_change": percent_change,
        "elasticity": info,
        "current_price": round(price, 2),
        "new_price": round(new_price, 2),
        "unit_cost": round(cost, 2),
        "current_30d_units": round(units_30, 1),
        "projected_30d_units": round(new_units, 1),
        "current_30d_revenue": round(units_30 * price, 2),
        "projected_30d_revenue": round(new_units * new_price, 2),
        "current_30d_profit": round(units_30 * (price - cost), 2),
        "projected_30d_profit": round(new_units * (new_price - cost), 2),
        "below_cost": bool(new_price < cost),
    }


def max_safe_discount(price: float, cost: float, min_margin_pct: float = 2.0) -> float:
    """Largest discount that still keeps a small margin over cost (capped at 50%)."""
    if price <= 0:
        return 0.0
    return float(np.clip((1 - cost * (1 + min_margin_pct / 100) / price) * 100, 0, 50))


# -------------------------------
# Daily brief facts
# -------------------------------
def daily_snapshot(df: pd.DataFrame, as_of=None) -> dict:
    as_of = parse_date(as_of, df['DATE'].max()).normalize()
    day = in_period(df, as_of, as_of)
    daily = df.groupby('DATE')['REVENUE'].sum()
    daily = daily.reindex(pd.date_range(daily.index.min(), as_of), fill_value=0)
    same_weekday = daily[(daily.index.dayofweek == as_of.dayofweek) & (daily.index < as_of)].tail(8)
    typical = float(same_weekday.mean()) if len(same_weekday) else None
    revenue = float(day['REVENUE'].sum())
    tomorrow = as_of + pd.Timedelta(days=1)
    pattern = weekday_pattern(df).set_index('Weekday')['Avg Daily Revenue']
    return {
        "date": as_of.date().isoformat(),
        "weekday": as_of.day_name(),
        "revenue": round(revenue, 2),
        "profit": round(float(day['PROFIT'].sum()), 2),
        "typical_revenue_for_this_weekday": round(typical, 2) if typical is not None else None,
        "vs_typical_pct": round((revenue - typical) / typical * 100, 1) if typical else None,
        "products_sold": day.groupby('PRODUCT')['QUANTITY'].sum().sort_values(ascending=False).head(5).to_dict(),
        "tomorrow": tomorrow.day_name(),
        "tomorrow_typical_revenue": round(float(pattern.get(tomorrow.day_name(), 0)), 2),
        "busiest_weekday": pattern.idxmax() if pattern.notna().any() else None,
        "slow_movers_to_push": clearance_candidates(df)['PRODUCT'].head(3).tolist(),
        "last_7_days_revenue": round(float(daily.tail(7).sum()), 2),
        "previous_7_days_revenue": round(float(daily.tail(14).head(7).sum()), 2),
    }
