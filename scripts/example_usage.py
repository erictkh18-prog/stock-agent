#!/usr/bin/env python3
"""
Example usage of the Stock Analysis Agent
Run with: python scripts/example_usage.py
"""

import sys
from pathlib import Path

# Add parent directory to path so imports work from any directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.stock_screener import StockScreener
from src.models import ScreeningFilter

def main():
    print("=" * 60)
    print("Stock Analysis Agent - Example Usage")
    print("=" * 60)
    
    screener = StockScreener()
    
    # Example 1: Analyze a single stock
    print("\n[1] Analyzing a Single Stock (AAPL)")
    print("-" * 60)
    analysis = screener.analyze_stock("AAPL")
    
    if analysis:
        print(f"Symbol: {analysis.symbol}")
        print(f"Name: {analysis.name}")
        print(f"Current Price: ${analysis.current_price:.2f}")
        print(f"Overall Score: {analysis.overall_score:.1f}/100")
        print(f"Recommendation: {analysis.recommendation}")
        print(f"Confidence: {analysis.confidence:.1%}")
        
        if analysis.fundamental:
            print(f"\nFundamental Analysis Score: {analysis.fundamental.score:.1f}/100")
            print(f"  - P/E Ratio: {analysis.fundamental.pe_ratio}")
            print(f"  - EPS: {analysis.fundamental.eps}")
            print(f"  - Dividend Yield: {analysis.fundamental.dividend_yield}")
        
        if analysis.technical:
            print(f"\nTechnical Analysis Score: {analysis.technical.score:.1f}/100")
            print(f"  - SMA 50: {analysis.technical.sma_50:.2f}")
            print(f"  - SMA 200: {analysis.technical.sma_200:.2f}")
            print(f"  - RSI: {analysis.technical.rsi:.1f}")
            print(f"  - Trend: {analysis.technical.trend}")
        
        if analysis.sentiment:
            print(f"\nSentiment Analysis Score: {analysis.sentiment.score:.1f}/100")
            print(f"  - News Sentiment: {analysis.sentiment.news_sentiment}")
            print(f"  - Analyst Sentiment: {analysis.sentiment.analyst_sentiment}")
    
    # Example 2: Screen multiple stocks
    print("\n\n[2] Screening Multiple Stocks")
    print("-" * 60)
    
    stocks = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "JPM", "JNJ"]
    filters = ScreeningFilter(min_overall_score=55)
    
    result = screener.screen_stocks(stocks, filters, top_n=5)
    
    print(f"Screened: {result.total_candidates} stocks")
    print(f"Passed Filters: {result.filtered_count}")
    print(f"\nTop {len(result.top_picks)} Investment Opportunities:")
    
    for idx, stock in enumerate(result.top_picks, 1):
        print(f"\n  {idx}. {stock.symbol} - {stock.name}")
        print(f"     Price: ${stock.current_price:.2f}")
        print(f"     Score: {stock.overall_score:.1f}/100")
        print(f"     Recommendation: {stock.recommendation}")
    
    # Example 3: Screen with specific filters
    print("\n\n[3] Advanced Filtering - Dividend Stocks")
    print("-" * 60)
    
    dividend_filter = ScreeningFilter(
        min_overall_score=50,
        min_dividend_yield=0.02,  # At least 2% dividend
        max_pe_ratio=25
    )
    
    result = screener.screen_stocks(
        ["JNJ", "PG", "KO", "WMT", "MSFT", "AAPL"],
        dividend_filter,
        top_n=5
    )
    
    print(f"Dividend stocks with yield >= 2%:")
    for stock in result.top_picks:
        div_yield = stock.fundamental.dividend_yield if stock.fundamental else None
        print(f"  - {stock.symbol}: {div_yield:.2%} yield (Score: {stock.overall_score:.1f})")

if __name__ == "__main__":
    main()
