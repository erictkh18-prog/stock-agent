#!/usr/bin/env python3
"""Simple menu interface for the Stock Analysis Agent."""
import sys
from pathlib import Path

# Add parent directory to path so imports work from any directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.stock_screener import StockScreener
from src.models import ScreeningFilter

SCREENER = StockScreener()

MENU_TEXT = """
Stock Analysis Agent - Interactive Menu
---------------------------------------
1) Analyze one stock
2) Screen multiple stocks
3) Show top performers (built-in list)
4) Exit
"""


def analyze_single_stock():
    symbol = input("Enter stock symbol (e.g. AAPL): ").strip().upper()
    if not symbol:
        print("No symbol entered. Returning to menu.")
        return

    analysis = SCREENER.analyze_stock(symbol)
    if not analysis:
        print(f"Could not fetch analysis for {symbol}. Try another symbol.")
        return

    print(f"\n{analysis.name} ({analysis.symbol})")
    print(f"Current price: ${analysis.current_price:.2f}")
    print(f"Overall score: {analysis.overall_score:.1f}/100")
    print(f"Recommendation: {analysis.recommendation}")
    print(f"Confidence: {analysis.confidence:.1%}\n")

    if analysis.fundamental:
        print("Fundamental:")
        print(f"  P/E Ratio: {analysis.fundamental.pe_ratio}")
        print(f"  EPS: {analysis.fundamental.eps}")
        print(f"  Dividend yield: {analysis.fundamental.dividend_yield}")
        print(f"  Fundamental score: {analysis.fundamental.score:.1f}/100")

    if analysis.technical:
        print("\nTechnical:")
        print(f"  Trend: {analysis.technical.trend}")
        print(f"  RSI: {analysis.technical.rsi}")
        print(f"  SMA 50: {analysis.technical.sma_50}")
        print(f"  SMA 200: {analysis.technical.sma_200}")
        print(f"  Technical score: {analysis.technical.score:.1f}/100")

    if analysis.sentiment:
        print("\nSentiment:")
        print(f"  News sentiment: {analysis.sentiment.news_sentiment}")
        print(f"  Analyst sentiment: {analysis.sentiment.analyst_sentiment}")
        print(f"  Sentiment score: {analysis.sentiment.score:.1f}/100")

    print("\n---\n")


def screen_multiple_stocks():
    symbols_input = input("Enter commas-separated symbols (e.g. AAPL,MSFT,GOOGL): ")
    symbols = [s.strip().upper() for s in symbols_input.split(",") if s.strip()]
    if not symbols:
        print("No symbols provided.")
        return

    min_score_input = input("Minimum overall score (0-100, default 60): ")
    try:
        min_score = float(min_score_input) if min_score_input.strip() else 60.0
    except ValueError:
        min_score = 60.0

    filters = ScreeningFilter(min_overall_score=min_score)
    result = SCREENER.screen_stocks(symbols, filters, top_n=10)

    print(f"\nAnalyzed {result.total_candidates} symbols, {result.filtered_count} passed filter.")
    for idx, stock in enumerate(result.top_picks, 1):
        print(f"{idx}. {stock.symbol} - {stock.name} | {stock.recommendation} | Score {stock.overall_score:.1f}")

    print("\n---\n")


def show_top_performers():
    symbols = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META"]
    filters = ScreeningFilter(min_overall_score=50)
    result = SCREENER.screen_stocks(symbols, filters, top_n=10)

    print(f"\nTop performers from built-in list:")
    for idx, stock in enumerate(result.top_picks, 1):
        print(f"{idx}. {stock.symbol} - {stock.name} | {stock.recommendation} | Score {stock.overall_score:.1f}")
    print("\n---\n")


def main():
    while True:
        print(MENU_TEXT)
        choice = input("Choose an option (1-4): ").strip()

        if choice == "1":
            analyze_single_stock()
        elif choice == "2":
            screen_multiple_stocks()
        elif choice == "3":
            show_top_performers()
        elif choice == "4":
            print("Goodbye!")
            break
        else:
            print("Invalid choice, try again.")


if __name__ == "__main__":
    main()
