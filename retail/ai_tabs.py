# retail/ai_tabs.py
# Streamlit UI for the AI features. Results are kept in st.session_state so a rerun
# (any widget change) does not repeat a paid AI call.
import urllib.parse

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from retail import actions, ai, data, llm, poster

# Tab labels, shared with app.py so the app can reopen the tab an AI button was pressed in
TAB_ASK, TAB_WHY, TAB_PLAN, TAB_PHOTO, TAB_BRIEF = (
    "🤖 Ask AI", "🔍 Why?", "✅ Action Plan", "📷 Sales from Photo", "📣 Brief & Promos")

LANGUAGES = {
    "English": "en", "Hindi": "hi", "Telugu": "te", "Tamil": "ta", "Kannada": "kn",
    "Marathi": "mr", "Bengali": "bn", "Urdu": "ur", "Arabic": "ar", "Spanish": "es",
    "French": "fr", "Portuguese": "pt", "Indonesian": "id", "Swahili": "sw",
}


def _need_key() -> bool:
    if ai.is_configured():
        return False
    st.info(
        "🔑 AI features need a free Google Gemini API key. Get one at aistudio.google.com/apikey, "
        "then add it to `.streamlit/secrets.toml` as `GEMINI_API_KEY` (see "
        "`.streamlit/secrets.toml.example`) and restart the app."
    )
    return True


def provider_caption():
    """Sidebar note saying which AI is answering."""
    p = llm.provider()
    if p is None:
        st.sidebar.caption("🤖 AI: off (no API key)")
    elif p == "gemini":
        st.sidebar.caption(f"🤖 AI: Google Gemini · `{llm.model_name()}`  \n"
                           "Free tier: Google may use what you send to improve its products, "
                           "so avoid customer names or phone numbers.")
    else:
        st.sidebar.caption(f"🤖 AI: Anthropic Claude · `{llm.model_name()}`")


def _md(text: str) -> str:
    """Escape $ (the AI may still use it) so Streamlit doesn't render text between two as a maths formula."""
    return text.replace("$", "\\$")


def stay_on(tab: str):
    """Button callback: keep this tab open when the app reruns (Streamlit would show the first tab)."""
    st.session_state["active_tab"] = tab


def _run(label: str, fn, *args):
    """Run an AI call with a spinner; show a friendly error instead of a traceback."""
    with st.spinner(label):
        try:
            return fn(*args)
        except Exception as e:
            st.error(ai.friendly_error(e))
            return None


# -------------------------------
# 🤖 Ask your data + what-if
# -------------------------------
def ask_tab(df: pd.DataFrame):
    st.subheader("🤖 Ask your shop data anything")
    st.caption("Examples: “Which category grew most this year?” · “What if I raise Product05's price by 5%?” "
               "· “Which products should I stop stocking?” Numbers come from your data, not from the AI's guesses.")
    if not _need_key():
        _chat(df)
    _what_if(df)


def _chat(df: pd.DataFrame):
    history = st.session_state.setdefault("chat", [])
    if st.button("Clear conversation", disabled=not history, on_click=stay_on, args=(TAB_ASK,)):
        history.clear()
    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(_md(msg["content"]))

    question = st.chat_input("Ask about sales, products, prices...", on_submit=stay_on, args=(TAB_ASK,))
    if question:
        with st.chat_message("user"):
            st.markdown(_md(question))
        with st.chat_message("assistant"):
            answer = _run("Looking at your data...", ai.ask, df, list(history), question)
            if answer:
                st.markdown(_md(answer))
                history.extend([{"role": "user", "content": question},
                                {"role": "assistant", "content": answer}])


def _what_if(df: pd.DataFrame):
    st.markdown("---")
    st.subheader("🎛️ What-if price simulator")
    product = st.selectbox("Product", sorted(df['PRODUCT'].unique()), key="whatif_product")
    change = st.slider("Price change %", -30, 30, -10, key="whatif_change")
    learned = data.estimate_elasticity(df, product)
    st.caption(f"Price sensitivity (elasticity): **{learned['elasticity']}**, {learned['source']}. "
               "−1.2 means a 10% price cut sells about 12% more units.")
    custom = st.number_input("Override elasticity (optional)", value=float(learned['elasticity']),
                             min_value=-5.0, max_value=0.0, step=0.1)
    sim = data.simulate_price_change(df, product, change,
                                     None if custom == learned['elasticity'] else custom)
    c1, c2, c3 = st.columns(3)
    c1.metric("New price", data.money(sim['new_price'], 2), f"{change:+d}%")
    c2.metric("30-day units", f"{sim['projected_30d_units']:,.0f}",
              f"{sim['projected_30d_units'] - sim['current_30d_units']:+,.0f}")
    c3.metric("30-day profit", data.money(sim['projected_30d_profit']),
              data.money(sim['projected_30d_profit'] - sim['current_30d_profit'], signed=True))
    if sim['below_cost']:
        st.error("⚠️ This price is below the unit cost.")


# -------------------------------
# 🔍 Why did it change?
# -------------------------------
def why_tab(df: pd.DataFrame):
    st.subheader("🔍 Why did revenue change?")
    d_cur_start, d_cur_end, d_prev_start, d_prev_end = data.default_periods(df)
    lo, hi = df['DATE'].min().date(), df['DATE'].max().date()
    c1, c2 = st.columns(2)
    cur = c1.date_input("This period", (d_cur_start.date(), d_cur_end.date()), min_value=lo, max_value=hi)
    prev = c2.date_input("Compare with", (d_prev_start.date(), d_prev_end.date()), min_value=lo, max_value=hi)
    if len(cur) != 2 or len(prev) != 2:
        st.info("Pick a start and end date for both periods.")
        return

    facts = data.explain_change(df, cur[0], cur[1], prev[0], prev[1])
    pct = f" ({facts['change_pct']:+.1f}%)" if facts['change_pct'] is not None else ""
    st.metric("Revenue change", data.money(facts['current_revenue']), data.money(facts['change'], signed=True) + pct)

    effects = facts['effects']
    bridge = pd.DataFrame({
        'step': ['Previous', 'Units sold (volume)', 'Product mix', 'Price', 'Current'],
        'value': [facts['previous_revenue'], effects['volume'], effects['mix'], effects['price'],
                  facts['current_revenue']],
    })
    fig = go.Figure(go.Waterfall(
        x=bridge['step'], y=bridge['value'],
        measure=['absolute', 'relative', 'relative', 'relative', 'total'],
        text=[data.money(v) for v in bridge['value']], textposition='outside',
    ))
    fig.update_layout(title="What moved revenue", showlegend=False)
    st.plotly_chart(fig, width="stretch")

    drivers = pd.DataFrame(facts['top_product_drivers'])
    if not drivers.empty:
        st.markdown("**Products behind the change**")
        st.dataframe(drivers, width="stretch", hide_index=True)

    if _need_key():
        return
    key = ("why", str(cur), str(prev), len(df))
    if st.button("✨ Explain this in plain words", on_click=stay_on, args=(TAB_WHY,)):
        st.session_state[key] = _run("Explaining...", ai.explain_change, facts)
    if st.session_state.get(key):
        st.success(_md(st.session_state[key]))

    st.markdown("---")
    st.subheader("🚨 Unusual weeks")
    unusual = data.weekly_anomalies(df)
    if unusual.empty:
        st.info("No unusual weeks found.")
    else:
        st.dataframe(unusual, width="stretch", hide_index=True)
        st.caption("Pick one of these weeks above as “This period” and the week before as “Compare with” to see why.")


# -------------------------------
# ✅ Action plan that checks itself
# -------------------------------
def actions_tab(df: pd.DataFrame):
    st.subheader("✅ This week's action plan")
    st.caption("The AI suggests actions, you mark what you did, and the app measures whether each one "
               "worked by comparing the target with the rest of your shop. Next week's plan learns from it.")

    all_actions = actions.load_actions()
    record = actions.track_record(all_actions)

    if not _need_key():
        start = st.date_input("Actions start on", (df['DATE'].max() + pd.Timedelta(days=1)).date(),
                              help="Pick a past date to backtest the loop on the sales history you already have.")
        if st.button("✨ Suggest 3 actions", on_click=stay_on, args=(TAB_PLAN,)):
            st.session_state["plan"] = _run("Planning this week...", ai.weekly_plan, df, record)
            st.session_state["plan_start"] = start
        plan = st.session_state.get("plan")
        if plan:
            for a in plan:
                target = a['target_product'] or a['target_category'] or "whole shop"
                st.markdown(_md(f"**{a['title']}** · `{a['action_type']}` · {target}  \n"
                                f"{a['rationale']}  \n"
                                f"_Expected: {a['expected_uplift_pct']:+.0f}% {a['metric']}, judged after {a['measure_days']} days._"))
            if st.button("💾 Save plan to tracker", on_click=stay_on, args=(TAB_PLAN,)):
                actions.add_actions(plan, st.session_state["plan_start"])
                st.session_state["plan"] = None
                st.rerun()

    st.markdown("---")
    st.subheader("📋 Tracker")
    if not all_actions:
        st.info("No saved actions yet.")
        return

    for a in reversed(all_actions):
        target = a['target_product'] or a['target_category'] or "whole shop"
        with st.container(border=True):
            c1, c2 = st.columns([3, 2])
            c1.markdown(_md(f"**{a['title']}** · {target} · starts {a['start_date']}"))
            status = c2.radio("Did you do it?", ["suggested", "done", "skipped"],
                              index=["suggested", "done", "skipped"].index(a['status']),
                              key=f"status_{a['id']}", horizontal=True, label_visibility="collapsed",
                              on_change=stay_on, args=(TAB_PLAN,))
            if status != a['status']:
                actions.update_action(a['id'], status=status)
                st.rerun()
            if a['status'] == "done":
                result = actions.evaluate_action(df, a)
                if result != a.get('result'):
                    actions.update_action(a['id'], result=result)
                if result['state'] == "measured":
                    icon = "🟢" if result['verdict'] == "worked" else "🔴"
                    st.markdown(
                        f"{icon} **{result['verdict'].capitalize()}**: {result['metric']} "
                        f"{result['target_change_pct']:+.1f}% vs rest of shop {result['rest_of_shop_change_pct']:+.1f}% "
                        f"→ net effect **{result['net_effect_pct']:+.1f}%** (expected {result['expected_pct']:+.0f}%)")
                else:
                    st.caption("⏳ " + result['message'])

    if record:
        st.markdown("**What has worked in your shop so far**")
        st.dataframe(pd.DataFrame([{"Action type": k, "Measured": v['measured'], "Worked": v['worked'],
                                    "Avg net effect %": v['avg_net_effect_pct']} for k, v in record.items()]),
                     width="stretch", hide_index=True)


# -------------------------------
# 📷 Sales from a photo
# -------------------------------
def photo_tab(df: pd.DataFrame):
    st.subheader("📷 Add sales from a photo")
    st.caption("Photograph a handwritten ledger page, a bill or a receipt. The AI reads it, you check "
               "the rows, and they are added to the dashboard.")
    if _need_key():
        return

    photo = st.file_uploader("Photo", type=["jpg", "jpeg", "png", "webp"])
    if photo and st.button("✨ Read photo", on_click=stay_on, args=(TAB_PHOTO,)):
        media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
        ext = photo.name.rsplit(".", 1)[-1].lower()
        ledger = _run("Reading the photo...", ai.read_ledger_photo, photo.getvalue(), media[ext],
                      sorted(df['PRODUCT'].unique()),
                      (df['DATE'].max() + pd.Timedelta(days=1)).date().isoformat())
        if ledger:
            st.session_state["ledger"] = pd.DataFrame([{
                "DATE": r.date, "PRODUCT": r.product, "QUANTITY": r.quantity,
                "PRICE": r.unit_price or None, "Known product": r.matched_known_product,
                "As written": r.text_as_written,
            } for r in ledger.rows])
            st.session_state["ledger_notes"] = ledger.notes

    if "ledger" in st.session_state:
        if st.session_state.get("ledger_notes"):
            st.warning(_md("AI notes: " + st.session_state["ledger_notes"]))
        st.markdown("Check and correct the rows (empty price = use the usual price):")
        edited = st.data_editor(st.session_state["ledger"], num_rows="dynamic", width="stretch",
                                disabled=["As written"], key="ledger_editor")
        c1, c2 = st.columns(2)
        if c1.button("➕ Add these sales to the dashboard", on_click=stay_on, args=(TAB_PHOTO,)):
            rows = data.enrich_rows(edited[["DATE", "PRODUCT", "QUANTITY", "PRICE"]], df)
            extra = st.session_state.get("extra_rows")
            st.session_state["extra_rows"] = rows if extra is None else pd.concat([extra, rows])
            del st.session_state["ledger"]
            st.rerun()
        if c2.button("Discard", on_click=stay_on, args=(TAB_PHOTO,)):
            del st.session_state["ledger"]
            st.rerun()

    extra = st.session_state.get("extra_rows")
    if extra is not None and not extra.empty:
        st.success(f"{len(extra)} imported sales are included in the dashboard for this session.")
        cols = [c for c in ["DATE", "PRODUCT ID", "PRODUCT", "CATEGORY", "QUANTITY", "PRICE", "COST"] if c in extra]
        st.download_button("⬇️ Download imported sales (Excel)", data.to_excel_bytes({"Imported Sales": extra[cols]}),
                           file_name="imported_sales.xlsx")


# -------------------------------
# 📣 Daily brief + promotions
# -------------------------------
def brief_tab(df: pd.DataFrame):
    st.subheader("📣 Daily brief")
    language = st.selectbox("Language", list(LANGUAGES), key="brief_lang")
    as_of = st.date_input("Brief for", df['DATE'].max().date(),
                          min_value=df['DATE'].min().date(), max_value=df['DATE'].max().date())
    snapshot = data.daily_snapshot(df, as_of)
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Revenue ({snapshot['weekday']})", data.money(snapshot['revenue']),
              f"{snapshot['vs_typical_pct']:+.1f}% vs typical" if snapshot['vs_typical_pct'] is not None else None)
    c2.metric("Last 7 days", data.money(snapshot['last_7_days_revenue']),
              data.money(snapshot['last_7_days_revenue'] - snapshot['previous_7_days_revenue'], signed=True))
    c3.metric(f"Typical {snapshot['tomorrow']}", data.money(snapshot['tomorrow_typical_revenue']))

    if _need_key():
        return
    key = ("brief", str(as_of), language, len(df))
    if st.button("✨ Write today's brief", on_click=stay_on, args=(TAB_BRIEF,)):
        st.session_state[key] = _run("Writing the brief...", ai.daily_brief, snapshot, language)
    brief = st.session_state.get(key)
    if brief:
        st.text_area("Brief", brief, height=180)
        b1, b2 = st.columns(2)
        b1.link_button("📲 Share on WhatsApp", "https://wa.me/?text=" + urllib.parse.quote(brief))
        if b2.button("🔊 Make a voice note", on_click=stay_on, args=(TAB_BRIEF,)):
            audio = _run("Recording...", ai.text_to_speech, brief, LANGUAGES[language])
            if audio:
                st.audio(audio, format="audio/mp3")
        st.caption("Tip: run this every evening, or schedule it, and the owner never needs to open the dashboard.")

    st.markdown("---")
    st.subheader("🏷️ Ready-made promotions")
    slow = data.clearance_candidates(df)['PRODUCT'].tolist()
    picks = st.multiselect("Products to promote (slow movers pre-selected)", sorted(df['PRODUCT'].unique()),
                           default=slow[:3], max_selections=5)
    shop_name = st.text_input("Shop name", "My Shop")
    if picks and st.button("✨ Create promotions", on_click=stay_on, args=(TAB_BRIEF,)):
        st.session_state["promos"] = _run("Creating offers...", ai.promotion_kit, df, picks, language, shop_name)
        st.session_state["promo_shop"] = shop_name
    for o in st.session_state.get("promos") or []:
        with st.container(border=True):
            c1, c2 = st.columns([3, 2])
            with c1:
                st.markdown("### " + _md(o['headline']))
                st.markdown(f"**{o['product']}**" + (f" + {o['bundle_with']}" if o['bundle_with'] else ""))
                st.markdown(f"~~{data.money(o['price'], 2)}~~ → **{data.money(o['offer_price'], 2)}** ({o['discount_pct']:.0f}% off)"
                            if o['discount_pct'] > 0 else f"**{data.money(o['price'], 2)}**")
                st.markdown(_md(o['offer_text']))
                msg = f"{o['whatsapp_message']}\n{o['product']}: {data.money(o['offer_price'], 2)}"
                st.text_area("WhatsApp message", msg, key=f"wa_{o['product']}", height=110)
                st.link_button("📲 Send on WhatsApp", "https://wa.me/?text=" + urllib.parse.quote(msg))
            with c2:
                png = poster.render_poster(st.session_state.get("promo_shop", shop_name), o['headline'],
                                           o['product'], o['offer_text'], o['price'], o['offer_price'],
                                           o['bundle_with'])
                st.image(png)
                st.download_button("⬇️ Poster", png, file_name=f"poster_{o['product']}.png", mime="image/png",
                                   key=f"poster_{o['product']}")
