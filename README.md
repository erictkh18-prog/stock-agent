# Stock Analysis Agent

An AI-powered investment research tool that analyzes US stocks using fundamental, technical, and sentiment analysis to identify suitable investment opportunities.

## Features

- **Fundamental Analysis**: P/E ratio, EPS, dividend yield, ROA, ROE, debt-to-equity, revenue growth, and more
- **Technical Analysis**: Moving averages (SMA-50, SMA-200), RSI, MACD, Bollinger Bands, support/resistance levels, and trend identification
- **Sentiment Analysis**: News sentiment, analyst sentiment, and market sentiment analysis
- **Stock Screening**: Filter and identify top-performing stocks based on custom criteria
- **Investment Recommendations**: BUY, HOLD, or SELL recommendations with confidence scores

## Architecture

```
Stock Agent/
├── src/                       # Core library code
│   ├── config.py              # Configuration settings
│   ├── models.py              # Pydantic data models
│   ├── fundamental_analysis.py # Fundamental metrics analysis
│   ├── technical_analysis.py   # Technical indicators
│   ├── sentiment_analysis.py   # Opinion & market sentiment
│   ├── stock_screener.py       # Stock screening engine
│   └── main.py                # FastAPI application
├── scripts/                   # Executable scripts & examples
│   ├── menu_stock_agent.py    # Interactive CLI menu
│   └── example_usage.py       # Usage examples
├── web/                       # Web UI assets
│   ├── static/                # CSS, JS, images
│   └── templates/             # HTML templates
├── deployment/                # Optional Docker deployment files
│   ├── Dockerfile             # Docker container config
│   └── .dockerignore          # Docker ignore file
├── tests/                     # Unit tests
├── Procfile                   # Process command for PaaS
├── render.yaml                # Render.com blueprint config
├── requirements.txt           # Python dependencies
├── .env.example              # Environment variables template
└── README.md                 # This file
```

## Installation

### Prerequisites
- Python 3.8+
- pip or conda

### Setup

1. **Clone or navigate to the project**:
   ```bash
   cd "Stock Agent"
   ```

2. **Create a virtual environment** (recommended):
   ```bash
   # Using venv
   python -m venv venv
   
   # On Windows
   venv\Scripts\activate
   
   # On macOS/Linux
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your API keys if needed (optional)
   ```

## Usage

### Starting the API Server

```bash
python -m uvicorn src.main:app --reload
```

The API will be available at `http://localhost:8000`

**Interactive API Documentation**: Visit `http://localhost:8000/docs` for Swagger UI

### Running CLI Scripts

#### Interactive Menu
```bash
python scripts/menu_stock_agent.py
```

An interactive menu to:
- Analyze individual stocks
- Screen multiple stocks with filters
- View top performers
- Get quick recommendations

#### Example Usage
```bash
python scripts/example_usage.py
```

Demonstrates various ways to use the stock screener programmatically.

## Deploy To Free Hosting (Render)

This repo is ready to deploy on Render using the root `render.yaml` blueprint.

### Option 1: Blueprint Deploy (Recommended)

1. Push this repository to GitHub.
2. In Render, click **New +** -> **Blueprint**.
3. Select this repo.
4. Render reads `render.yaml` and creates a web service automatically.
5. Wait for build and deploy, then open:
  - `https://<your-service-name>.onrender.com/health`
  - `https://<your-service-name>.onrender.com/docs`

### Option 2: Manual Web Service

If you prefer manual setup in Render:

- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn src.main:app --host 0.0.0.0 --port $PORT`
- **Health Check Path**: `/health`

### Notes

- Free plans may sleep after inactivity; first request can be slow.
- The app uses free public data sources and does not require paid API keys.

### API Endpoints

#### 1. Analyze a Single Stock
```bash
GET /analyze/{symbol}
```

**Example**:
```bash
curl http://localhost:8000/analyze/AAPL
```

**Response**:
```json
{
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "current_price": 150.25,
  "timestamp": "2024-03-20T10:30:45.123456",
  "fundamental": {
    "pe_ratio": 25.3,
    "eps": 5.95,
    "dividend_yield": 0.004,
    "score": 75
  },
  "technical": {
    "sma_50": 148.5,
    "sma_200": 145.2,
    "rsi": 65,
    "trend": "uptrend",
    "score": 78
  },
  "sentiment": {
    "news_sentiment": 0.35,
    "analyst_sentiment": "bullish",
    "score": 72
  },
  "overall_score": 75,
  "recommendation": "BUY",
  "confidence": 0.67
}
```

#### 2. Screen Multiple Stocks
```bash
POST /screen?symbols=AAPL&symbols=MSFT&symbols=GOOGL&min_overall_score=60&top_n=5
```

**Request Parameters**:
- `symbols` (list): Stock symbols to analyze
- `min_overall_score` (0-100): Minimum score filter
- `max_pe_ratio` (optional): P/E ratio filter
- `min_dividend_yield` (optional): Dividend yield filter
- `max_debt_to_equity` (optional): Debt-to-equity filter
- `trend` (optional): Trend filter (uptrend, downtrend, sideways)
- `top_n` (1-100): Number of top picks to return

#### 3. Get Top Performers
```bash
POST /fetch-top-performers?top_n=10
```

Analyzes popular large-cap stocks and returns top recommendations.

#### 4. Get Quick Recommendations
```bash
GET /recommendations?symbols=AAPL&symbols=MSFT&symbols=TSLA
```

Returns quick investment recommendations for specified stocks.

#### 5. Health Check
```bash
GET /health
```

## Scoring System

### Fundamental Score (40% weight)
- **P/E Ratio**: Ideal range 10-25
- **EPS**: Positive is better
- **Dividend Yield**: Higher is better (min 2%)
- **Debt-to-Equity**: Lower is better (ideal < 0.5)
- **Current Ratio**: Should be > 1.5
- **ROA/ROE**: Higher is better
- **Revenue Growth**: Positive growth preferred

### Technical Score (35% weight)
- **Trend**: Uptrend > Sideways > Downtrend
- **RSI**: 30-70 is healthy range
- **Moving Averages**: Golden cross signals strength
- **Support/Resistance**: Price near support is bullish
- **Support Levels**: Good entry points

### Sentiment Score (25% weight)
- **News Sentiment**: Based on news articles analysis
- **Analyst Sentiment**: Derived from market sentiment
- **Combined Sentiment**: Weighted average approach

### Overall Recommendation
- **BUY** (≥70): Strong buy signal with high confidence
- **HOLD** (50-69): Mixed signals, accumulate on dips
- **SELL** (<50): Weakish fundamentals or technical indicators

## Configuration

Edit `src/config.py` or set environment variables:

```bash
DEBUG=false
LOG_LEVEL=INFO
FUNDAMENTAL_LOOKBACK_DAYS=730
TECHNICAL_LOOKBACK_DAYS=365
SENTIMENT_LOOKBACK_DAYS=30
MIN_MARKET_CAP=1000000000
MIN_VOLUME=1000000
CACHE_TTL_SECONDS=3600
```

## Data Sources

- **Price & Fundamental Data**: Yahoo Finance (via yfinance)
- **News Sentiment**: Yahoo Finance RSS feeds, feedparser
- **Technical Indicators**: Calculated from OHLC data
- **Sentiment Analysis**: TextBlob for natural language processing

All data sources are **free and public**.

## Running Tests

```bash
pytest tests/ -v
```

Coverage:
```bash
pytest tests/ --cov=src
```

## Example Usage Scenarios

### 1. Find Undervalued Large-Cap Stocks
```bash
curl -X POST "http://localhost:8000/screen?symbols=AAPL&symbols=MSFT&symbols=GOOGL&symbols=AMZN&symbols=TSLA&min_overall_score=65&max_pe_ratio=25"
```

### 2. Find High-Dividend Stocks
```bash
curl -X POST "http://localhost:8000/screen?symbols=JNJ&symbols=PG&symbols=KO&symbols=WMT&min_dividend_yield=0.03"
```

### 3. Find Growth Stocks in Uptrend
```bash
curl -X POST "http://localhost:8000/screen?symbols=TSLA&symbols=NVDA&symbols=AMD&trend=uptrend&min_overall_score=70"
```

### 4. Quick Portfolio Analysis
```bash
curl "http://localhost:8000/recommendations?symbols=AAPL&symbols=MSFT&symbols=JPM&symbols=JNJ"
```

## Limitations & Disclaimers

⚠️ **Important**: This tool is for educational and research purposes only. It is **NOT financial advice** and should **NOT** be the sole basis for investment decisions.

- Historical data patterns don't guarantee future results
- Market conditions change rapidly
- Individual stock risk varies significantly
- Consider consulting a financial advisor before investing
- Past performance does not indicate future results

## Future Enhancements

- [ ] Portfolio optimization (Modern Portfolio Theory)
- [ ] Backtesting framework
- [ ] Advanced ML-based prediction models
- [ ] Real-time data feeds
- [ ] Options analysis
- [ ] ETF & mutual fund screener
- [ ] Tax-loss harvesting suggestions
- [ ] Web dashboard UI

## Contributing

Contributions welcome! Areas for improvement:
- Better sentiment analysis (Twitter/StockTwits integration)
- Improved fundamental metrics
- More robust technical indicators
- Machine learning predictions
- Performance optimization

## License

MIT License - See LICENSE file for details

## Support

For questions or issues:
1. Check the API documentation at `/docs` (Swagger UI)
2. Review error messages and logs
3. Ensure all dependencies are installed: `pip install -r requirements.txt`

## Disclaimer

This Stock Analysis Agent does not constitute financial advice. Use at your own risk. Always do your own research and consult with a financial advisor before making investment decisions.
#   s t o c k - a g e n t 
 
 