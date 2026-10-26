# 🛒 Retail AI Dashboard

A sales dashboard for small retail shops, built with Streamlit. Upload your sales Excel file and get trends, product insights, pricing suggestions and forecasts, plus an AI assistant that explains your numbers in plain language, plans the week and checks whether its advice actually worked.

**The AI never makes up numbers.** Every figure is calculated in Python (pandas) from your own data. The AI only picks which calculation to run and explains the result.

<!-- ![Dashboard overview](docs/screenshots/overview.png) -->
> 📸 *Screenshot placeholder: dashboard overview. Save it as `docs/screenshots/overview.png` and uncomment the line above.*

---

## ✨ Features

### 📊 Analytics (no AI key needed)

| Tab | What it shows |
|---|---|
| 📈 **Behavior Trends** | Daily and monthly revenue |
| 🛍️ **Product & Category** | Revenue by category, top 10 products |
| 📍 **Layout & Pricing** | Fast-selling products, pricing suggestions, discount simulator |
| 🤝 **Recommendations** | Products often sold on the same day |
| 🧠 **Forecast & Optimization** | 30-day forecast (Prophet), best days for discounts, clearance candidates, ABC analysis |

The dashboard accepts most retail Excel layouts: it finds the date, product, quantity, price, cost, category and discount columns automatically, and merges multiple sheets such as sales and product master data.

### 🤖 AI features

| Tab | What it does |
|---|---|
| 🤖 **Ask AI** | Chat with your data in any language, e.g. *"Which category made the most profit in 2022?"*. Includes a **what-if price simulator**. |
| 🔍 **Why?** | Splits any revenue change into **volume** (units sold), **mix** (cheaper vs pricier products) and **price** effects, shown as a waterfall chart, with a plain-language explanation and a list of unusual weeks. |
| ✅ **Action Plan** | Suggests 3 actions for the week. You mark what you did, and the app **measures whether each one worked** against the rest of the shop. Later plans learn from what worked. |
| 📷 **Sales from Photo** | Photograph a handwritten ledger page or bill. The AI reads the items, matches them to your products and lets you check them before adding. |
| 📣 **Brief & Promos** | A short daily summary in 14 languages with a WhatsApp share button and voice note, plus ready-made offers with printable posters. Discounts are capped so you never sell below cost. |

<!-- ![Ask AI](docs/screenshots/ask-ai.png) -->
> 📸 *Screenshot placeholder: Ask AI chat. Save as `docs/screenshots/ask-ai.png`.*

<!-- ![Why did revenue change](docs/screenshots/why.png) -->
> 📸 *Screenshot placeholder: revenue waterfall. Save as `docs/screenshots/why.png`.*

<!-- ![Action plan tracker](docs/screenshots/action-plan.png) -->
> 📸 *Screenshot placeholder: action plan tracker. Save as `docs/screenshots/action-plan.png`.*

<!-- ![Promotion poster](docs/screenshots/poster.png) -->
> 📸 *Screenshot placeholder: promotion poster. Save as `docs/screenshots/poster.png`.*

All amounts are shown in Indian rupees with Indian digit grouping (₹4,01,412).

---

## 🚀 Setup

**You need** Python 3.10 or newer (developed on 3.12) and Git.

1. **Download the project**
   ```bash
   git clone https://github.com/nitishkola/Dashboard.git
   cd Dashboard
   ```

2. **Create a virtual environment and install the packages**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   # source .venv/bin/activate   # macOS / Linux
   pip install -r requirements.txt
   ```

3. **Add a free AI key** (optional: skip this and the analytics tabs still work)
   - Get a free Google Gemini key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
   - Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and paste your key in:
     ```toml
     GEMINI_API_KEY = "your-key-here"
     ```
   - `secrets.toml` is in `.gitignore`, so your key is never committed.

4. **Run the dashboard**
   ```bash
   streamlit run app.py
   ```
   It opens at http://localhost:8501. Upload your Excel file, or click **📂 Try the sample data**.

---

## 🧠 Choosing the AI

| | Google Gemini (default) | Anthropic Claude |
|---|---|---|
| Cost | Free tier | Paid per use |
| Key setting | `GEMINI_API_KEY` | `ANTHROPIC_API_KEY` |
| Get a key | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | [console.anthropic.com](https://console.anthropic.com/) |

- If both keys are set, add `AI_PROVIDER = "claude"` (or `"gemini"`) to `secrets.toml` to choose.
- You can change the model with `GEMINI_MODEL` or `CLAUDE_MODEL`.
- **Gemini free-tier limits:** each model allows only a small number of requests per day (about 20 on a new project). When one model runs out, or is busy, the app automatically moves to the next free model. A chat question uses 2–4 requests.
- **Privacy:** on Gemini's free tier, Google may use what you send to improve its products. Avoid uploading customer names or phone numbers.

---

## 📁 Project structure

```
app.py                 Streamlit page: layout, filters and the analytics tabs
retail/
  data.py              Data loading and every calculation (pandas)
  ai.py                AI features: prompts, tools and response schemas
  llm.py               Gemini / Claude switch, fallbacks and error messages
  ai_tabs.py           UI for the five AI tabs
  actions.py           Action tracker: stores actions and measures results
  poster.py            Promotion poster images (Pillow)
.streamlit/
  secrets.toml.example Template for your API keys
Sales-Dashboard-practice-file.xlsx   Sample data
```

---

## 📋 Your data

The sample file has two sheets: **Input Data** (date, product ID, quantity, sale type, payment mode, discount %) and **Master Data** (product, category, buying price, selling price). Any file with similar columns works. The dashboard recognises common names such as `DATE` / `ORDERDATE`, `QTY`, `UNITPRICE`, `COST` and `CATEGORY`.

**Known limits**
- *Bought together* uses sales on the same day. Add an invoice or bill number column for true basket analysis.
- The what-if simulator learns how customers react to price from past discounts and price changes. If prices never changed in your data, it uses an assumed value and says so.
- Posters support English and other Latin-script languages. The brief and WhatsApp messages work in all listed languages.
- Sales added from photos last for the current session. Download them as Excel to keep them.

---

## 🛠️ Built with

[Streamlit](https://streamlit.io) · [pandas](https://pandas.pydata.org) · [Plotly](https://plotly.com/python/) · [Prophet](https://facebook.github.io/prophet/) · [Google Gemini API](https://ai.google.dev) · [Anthropic Claude API](https://docs.anthropic.com) · [Pillow](https://python-pillow.org) · [gTTS](https://gtts.readthedocs.io)
