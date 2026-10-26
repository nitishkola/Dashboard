# retail/ai.py
# AI features. Rule for every feature here: pandas (retail/data.py) computes the numbers,
# the AI only chooses which calculation to run and explains the results in plain language.
# Which AI answers (Gemini or Claude) is decided in retail/llm.py.
import io
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from retail import data, llm
from retail.llm import AIError, friendly_error, is_configured  # noqa: F401  (used by the UI)

_dumps = llm.dumps


SHOP_CONTEXT = (
    "You help the owner of a small retail shop understand their sales. They are not an analyst: "
    "use plain words, short sentences and concrete actions. Amounts are in Indian rupees: write "
    "them with the ₹ sign and Indian digit grouping (₹1,25,000), never with $. "
    "Never calculate, estimate or invent a number yourself. Every figure you state must come "
    "from the data you were given or a tool result. If the data cannot answer something, say so."
)


# -------------------------------
# 1. Ask the data (tool calling)
# -------------------------------
def make_tools(df: pd.DataFrame) -> list:
    """Functions the AI can call. Each one runs a pandas calculation on the filtered data."""

    def safe(fn):
        try:
            return _dumps(fn())
        except Exception as e:  # report bad inputs back to the AI so it can retry
            return _dumps({"error": str(e)})

    def get_summary(start_date: str = "", end_date: str = "") -> str:
        """Revenue, profit, margin, units, transactions, top products, and revenue, profit and units by category for a date range.

        Args:
            start_date: First day, YYYY-MM-DD. Empty means the start of the data.
            end_date: Last day, YYYY-MM-DD. Empty means the end of the data.
        """
        return safe(lambda: data.summarize(df, start_date, end_date))

    def explain_revenue_change(current_start: str = "", current_end: str = "",
                               previous_start: str = "", previous_end: str = "") -> str:
        """Why revenue changed between two periods: splits the change into volume, product-mix and
        price effects, and lists the products and categories that drove it. Empty dates mean the
        last 30 days of data versus the 30 days before.

        Args:
            current_start: YYYY-MM-DD.
            current_end: YYYY-MM-DD.
            previous_start: YYYY-MM-DD.
            previous_end: YYYY-MM-DD.
        """
        def run():
            d = data.default_periods(df)
            dates = [data.parse_date(v, dv) for v, dv in
                     zip([current_start, current_end, previous_start, previous_end], d)]
            return data.explain_change(df, *dates)
        return safe(run)

    def list_products(category: str = "") -> str:
        """Every product with its category, revenue, units sold, sales per selling day, days since last sale and ABC class.

        Args:
            category: Only products in this category. Empty means all.
        """
        def run():
            part = df[df['CATEGORY'] == category] if category else df
            v = data.product_velocity(part)
            abc = data.abc_analysis(part).set_index('PRODUCT')
            v['revenue'] = v['PRODUCT'].map(abc['REVENUE'])
            v['abc_class'] = v['PRODUCT'].map(abc['ABC'])
            v['category'] = v['PRODUCT'].map(part.groupby('PRODUCT')['CATEGORY'].first())
            return v.drop(columns=['Last_Sold']).round(2).to_dict('records')
        return safe(run)

    def simulate_price_change(product: str, percent_change: float) -> str:
        """Projected 30-day units, revenue and profit if a product's price changes. Negative percent_change is a discount.

        Args:
            product: Exact product name as listed by list_products.
            percent_change: For example -10 for a 10% discount, 5 for a 5% increase.
        """
        return safe(lambda: data.simulate_price_change(df, product, percent_change))

    def find_unusual_weeks() -> str:
        """Weeks where revenue spiked or dropped far outside the previous 8 weeks."""
        return safe(lambda: data.weekly_anomalies(df).to_dict('records'))

    def weekday_pattern() -> str:
        """Average daily revenue for each day of the week."""
        return safe(lambda: data.weekday_pattern(df).round(2).to_dict('records'))

    def slow_movers() -> str:
        """Products selling slowest or not sold for over 60 days: candidates for clearance or promotion."""
        return safe(lambda: data.clearance_candidates(df).drop(columns=['Last_Sold']).round(2).head(15).to_dict('records'))

    def bought_together() -> str:
        """Product pairs often sold on the same day (a proxy, since the data has no invoice numbers)."""
        return safe(lambda: data.co_purchase(df).head(15).to_dict('records'))

    return [get_summary, explain_revenue_change, list_products, simulate_price_change,
            find_unusual_weeks, weekday_pattern, slow_movers, bought_together]


def ask(df: pd.DataFrame, history: list, question: str) -> str:
    span = f"The data covers {df['DATE'].min().date()} to {df['DATE'].max().date()}."
    system = (SHOP_CONTEXT + " Use the tools to get numbers. " + span
              + " Answer in the language the owner writes in.")
    return llm.chat_with_tools(system, make_tools(df), history, question)


# -------------------------------
# 2. Why did revenue change?
# -------------------------------
def explain_change(facts: dict) -> str:
    return llm.complete(
        SHOP_CONTEXT,
        "Explain this revenue change to the owner in under 150 words. Start with one sentence on "
        "what happened, then the main causes using the volume / mix / price effects "
        "(volume = more or fewer units sold, mix = customers shifting between cheaper and pricier "
        "products, price = the same products sold at different prices), then name the products "
        "behind it, then one or two things to do about it.\n\n" + _dumps(facts),
        effort="medium",
    )


# -------------------------------
# 3. Weekly action plan
# -------------------------------
class PlannedAction(BaseModel):
    title: str
    action_type: Literal["discount", "price_increase", "bundle", "placement", "restock", "clearance", "promotion"]
    target_product: str
    target_category: str
    metric: Literal["revenue", "units", "profit"]
    expected_uplift_pct: float
    measure_days: int
    rationale: str


class ActionPlan(BaseModel):
    actions: list[PlannedAction]


def weekly_plan(df: pd.DataFrame, record: dict) -> list:
    cur_start, cur_end, prev_start, prev_end = data.default_periods(df)
    top = data.abc_analysis(df).head(5)['PRODUCT'].tolist()
    facts = {
        "last_30_days": data.summarize(df, cur_start, cur_end),
        "change_vs_previous_30_days": data.explain_change(df, cur_start, cur_end, prev_start, prev_end),
        "slow_movers": data.clearance_candidates(df).drop(columns=['Last_Sold']).round(2).head(8).to_dict('records'),
        "weekday_pattern": data.weekday_pattern(df).round(2).to_dict('records'),
        "unusual_weeks": data.weekly_anomalies(df).tail(5).to_dict('records'),
        "top_products_10pct_discount": [data.simulate_price_change(df, p, -10) for p in top],
        "products": sorted(df['PRODUCT'].unique().tolist()),
        "categories": sorted(df['CATEGORY'].unique().tolist()),
        "track_record_of_past_actions": record or "No actions measured yet.",
    }
    plan = llm.structured(
        SHOP_CONTEXT + " You plan this week's actions for the shop.",
        "Suggest exactly 3 actions for this week, ranked by expected impact. Rules:\n"
        "- target_product must be an exact name from 'products', or empty for a whole-category or "
        "whole-shop action; target_category must be from 'categories' or empty.\n"
        "- expected_uplift_pct is the % improvement in the chosen metric you expect versus the "
        "previous period of the same length; keep it realistic (usually 3-25).\n"
        "- measure_days is how long to wait before judging the result (7-28).\n"
        "- Use the track record: favour action types that worked here before and avoid ones that "
        "did not. Mention it in the rationale when it applies.\n"
        "- rationale: two sentences quoting the numbers from the data that justify it.\n\n"
        + _dumps(facts),
        ActionPlan,
    )
    products, categories = set(facts["products"]), set(facts["categories"])
    actions = []
    for a in plan.actions:
        a = a.model_dump()
        if a["target_product"] not in products:
            a["target_product"] = ""
        if a["target_category"] not in categories:
            a["target_category"] = ""
        a["measure_days"] = min(max(a["measure_days"], 7), 28)
        actions.append(a)
    return actions


# -------------------------------
# 4. Read a ledger or bill photo
# -------------------------------
class LedgerRow(BaseModel):
    date: str
    product: str
    quantity: float
    unit_price: float
    matched_known_product: bool
    text_as_written: str


class Ledger(BaseModel):
    rows: list[LedgerRow]
    notes: str


def read_ledger_photo(image_bytes: bytes, media_type: str, products: list, default_date: str) -> Ledger:
    prompt = (
        "This is a photo of a shop's handwritten sales ledger, a bill or a receipt. Extract one "
        "row per item sold.\n"
        "- product: map each item to the closest name from the known products list below and set "
        "matched_known_product true; if nothing is a reasonable match, copy the item as written "
        "and set it false.\n"
        f"- date: YYYY-MM-DD. If the page shows no date, use {default_date}.\n"
        "- unit_price: price per unit as written; use 0 if none is visible. If only a line total "
        "is visible, divide it by the quantity.\n"
        "- text_as_written: the original line, so the owner can check it.\n"
        "- notes: anything unreadable or uncertain. Never guess digits you cannot read; leave the "
        "row out and mention it in notes instead.\n\n"
        "Known products: " + ", ".join(products)
    )
    return llm.structured("You read retail sales records accurately.", prompt, Ledger,
                          image=(image_bytes, media_type))


# -------------------------------
# 5. Daily brief
# -------------------------------
def daily_brief(snapshot: dict, language: str) -> str:
    return llm.complete(
        SHOP_CONTEXT,
        f"Write the owner's daily brief in {language}, for WhatsApp, under 100 words, plain text "
        "(a few emojis are fine, no markdown headings). Three short parts: how the day went "
        "compared with a typical day like it, the main reason, and one thing to do tomorrow.\n\n"
        + _dumps(snapshot),
    )


# -------------------------------
# 6. Promotion kit
# -------------------------------
class PromoOffer(BaseModel):
    product: str
    discount_pct: float
    bundle_with: str
    headline: str
    offer_text: str
    whatsapp_message: str


class PromoKit(BaseModel):
    offers: list[PromoOffer]


def promotion_kit(df: pd.DataFrame, products: list, language: str, shop_name: str) -> list:
    pairs = data.co_purchase(df)
    facts = []
    for p in products:
        rows = df[df['PRODUCT'] == p]
        price, cost = float(rows['PRICE'].mean()), float(rows['COST'].mean())
        partners = pairs[(pairs['Product A'] == p) | (pairs['Product B'] == p)].head(3)
        facts.append({
            "product": p,
            "price": round(price, 2),
            "max_discount_pct": round(data.max_safe_discount(price, cost), 1),
            "projected_with_10pct_discount": data.simulate_price_change(df, p, -10),
            "often_sold_with": [b if a == p else a for a, b in zip(partners['Product A'], partners['Product B'])],
        })
    kit = llm.structured(
        SHOP_CONTEXT + " You write short, friendly promotions for a local shop.",
        f"Create one offer per product for the shop '{shop_name}', written in {language}.\n"
        "- discount_pct must not exceed that product's max_discount_pct (it protects the margin); "
        "use 0 if a bundle alone is the better offer.\n"
        "- bundle_with: a product from often_sold_with, or empty.\n"
        "- headline: at most 6 words, for a poster. offer_text: one sentence for the poster.\n"
        "- whatsapp_message: under 50 words, ready to send to customers. Do not state the "
        "discounted price; the app adds it.\n\n" + _dumps(facts),
        PromoKit,
    )
    limits = {f["product"]: f for f in facts}
    offers = []
    for o in kit.offers:
        if o.product not in limits:
            continue
        f = limits[o.product]
        o = o.model_dump()
        o["discount_pct"] = round(min(max(o["discount_pct"], 0), f["max_discount_pct"]), 1)
        o["price"] = f["price"]
        o["offer_price"] = round(f["price"] * (1 - o["discount_pct"] / 100), 2)
        offers.append(o)
    return offers


# -------------------------------
# Voice note for the brief (optional, uses gTTS)
# -------------------------------
def text_to_speech(text: str, lang_code: str):
    try:
        from gtts import gTTS
    except ImportError:
        raise AIError("Voice notes need the gTTS package: pip install gTTS")
    buf = io.BytesIO()
    gTTS(text=text, lang=lang_code).write_to_fp(buf)
    return buf.getvalue()
