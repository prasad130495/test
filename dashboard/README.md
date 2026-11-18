# Gold 20/50 EMA backtest dashboard (TradingView OANDA:XAUUSD data)
    pip install -r requirements.txt
    export NGROK_AUTHTOKEN=<token>   # optional: prints a public ngrok URL
    python3 server.py
Click "Fetch OANDA:XAUUSD from TradingView" (websocket, like the xauusd-ai project) or upload a TradingView
"Export chart data" CSV per timeframe. Free accounts return ~1 month of 5m / ~3 months of 15m; set TV_JWT for more.
