# 🎨 Using Your New Stock Analysis Dashboard

## Quick Start

### 1. Start the Server
Open terminal and run:
```bash
python -m uvicorn src.main:app --reload
```

### 2. Open Your Dashboard
Once the server starts, open your web browser and go to:
```
http://localhost:8000
```

You should see a beautiful, colorful dashboard!

---

## 📊 Dashboard Features

### Feature 1: Analyze a Single Stock
**What it does**: Get complete analysis of one stock with scores and charts

**How to use**:
1. Enter a stock symbol (e.g., AAPL, MSFT, GOOGL)
2. Click the **"Analyze"** button
3. See:
   - Current price
   - Overall score (0-100)
   - Recommendation (BUY, HOLD, or SELL)
   - Visual gauge chart
   - Individual scores for Fundamentals, Technical, and Sentiment
   - Key metrics like P/E Ratio, EPS, Dividend Yield

**Color Meanings**:
- 🟢 **BUY** (70+): Stock looks good to buy
- 🟡 **HOLD** (50-69): Mixed signals, wait or buy small amounts
- 🔴 **SELL** (<50): Stock doesn't look attractive

---

### Feature 2: Screen Multiple Stocks
**What it does**: Compare several stocks at once in a table

**How to use**:
1. Enter multiple stock symbols separated by commas
   - Example: `AAPL,MSFT,GOOGL,NVDA,TSLA`
2. Click the **"Screen"** button
3. See a table showing all stocks with:
   - Prices
   - Overall scores
   - Recommendations
   - P/E ratios
   - Dividend yields
   - Trends

**Pro tip**: You can easily spot the best stocks by looking at the score column!

---

### Feature 3: Top Performers
**What it does**: Automatically shows the best stocks from major companies

**How to use**:
1. Click **"Show Top 10"** button
2. See the 10 best stocks right now
3. No need to search - it's automatic!

---

## 🎯 What the Scores Mean

### Overall Score (Main Circle)
- **0-50**: Not recommended (🔴 SELL)
- **50-70**: Maybe consider (🟡 HOLD)
- **70-100**: Good opportunity (🟢 BUY)

### Fundamental Score (40% of total)
- Looks at company finances, profits, growth
- Higher is better

### Technical Score (35% of total)
- Looks at price trends and patterns
- Rising trends get higher scores

### Sentiment Score (25% of total)
- Looks at news and market opinions
- Positive news = higher score

---

## 📱 Visual Guide

```
┌─────────────────────────────────────┐
│  📊 Stock Analysis Agent            │
│  Smart investing with AI-powered    │
│     analysis                        │
└─────────────────────────────────────┘

┌─── Card 1: Analyze a Stock ─────────┐
│  Enter symbol: [AAPL          ]     │
│                           [Analyze] │
│                                     │
│  Results will show:                │
│  - Price chart                      │
│  - Score gauge (0-100)             │
│  - Recommendation with confidence  │
│  - Detailed metrics                │
└─────────────────────────────────────┘

┌─── Card 2: Screen Multiple ─────────┐
│  Enter symbols: [AAPL,MSFT,GOOGL]  │
│                           [Screen]  │
│                                     │
│  Results will show:                │
│  - All stocks in a table            │
│  - Easy comparison                  │
│  - Best picks highlighted           │
└─────────────────────────────────────┘

┌─── Card 3: Top Performers ──────────┐
│                     [Show Top 10]    │
│                                     │
│  Automatically shows:               │
│  - Best 10 stocks                   │
│  - No searching needed              │
└─────────────────────────────────────┘
```

---

## 💡 Tips for Beginners

1. **Start simple**: Try analyzing one stock first (AAPL is a good start)
2. **Check the score**: The big circle shows the overall recommendation
3. **Color codes matter**: Green = BUY, Yellow = HOLD, Red = SELL
4. **Compare stocks**: Use "Screen Multiple Stocks" to find the best options
5. **Don't rely on just one score**: Look at Fundamentals + Technical + Sentiment

---

## ⌨️ Keyboard Shortcuts

- Press **Enter** instead of clicking button to submit
- Press **Ctrl+C** in terminal to stop the server

---

## 🆘 Troubleshooting

### Dashboard doesn't load
- Make sure the server is running (you should see "Uvicorn running on...")
- Check that you're using `http://localhost:8000` (not https)

### Stock symbols not found
- Make sure you use the correct ticker symbol (AAPL, not Apple)
- Check spelling
- Only US stocks are supported

### No scores showing
- Wait a moment, the analysis takes a few seconds
- Check browser console for any errors (F12 → Console tab)

---

## 📚 Next Steps

Want to learn more?
- Check out `/docs` at `http://localhost:8000/docs` for detailed API documentation
- All the data comes from Yahoo Finance (free, real stocks)
- The AI uses proven financial formulas to calculate scores

---

## 🎉 You're Ready!

Your stock analysis dashboard is now live and ready to use. Start by analyzing your favorite stocks and see how the system works. Have fun exploring!
