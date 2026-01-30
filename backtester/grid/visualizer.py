"""
Grid Backtest Visualizer

Creates interactive Plotly charts from backtest results:
1. Candlestick chart with buy/sell fill markers
2. Equity curve with active executors
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime
from decimal import Decimal

from hummingbot.core.data_type.common import TradeType
from backtester.grid.data_types import BacktestResult


class GridBacktestVisualizer:
    """
    Visualizer for grid backtest results.

    Creates interactive Plotly charts showing:
    - Candlestick price chart with fill markers
    - Equity curve over time
    - Active executors count
    """

    def __init__(self, result: BacktestResult, candles: pd.DataFrame):
        """
        Initialize visualizer with backtest result and candles.

        Args:
            result: BacktestResult containing executor results and equity curve
            candles: DataFrame with OHLCV candle data (must have 'timestamp' column)
        """
        self.result = result
        self.candles = candles.copy()

        # Convert timestamps to datetime for plotting
        self._prepare_data()

    def _prepare_data(self):
        """Prepare data for plotting by converting timestamps to datetime."""
        # Debug: Check candles data
        print(f"DEBUG: Candles shape: {self.candles.shape}")
        print(f"DEBUG: Candles columns: {self.candles.columns.tolist()}")
        print(f"DEBUG: First candle: {self.candles.iloc[0].to_dict() if len(self.candles) > 0 else 'empty'}")
        print(f"DEBUG: Candles dtypes: {self.candles.dtypes.to_dict()}")

        # Convert candle timestamps to datetime
        self.candles['datetime'] = pd.to_datetime(self.candles['timestamp'], unit='s')

        print(f"DEBUG: First datetime: {self.candles['datetime'].iloc[0] if len(self.candles) > 0 else 'empty'}")

        # Convert equity curve timestamps to datetime
        self.result.equity_curve['datetime'] = pd.to_datetime(
            self.result.equity_curve['timestamp'], unit='s'
        )

        # Collect all fills with datetime
        self.all_fills = []
        for executor_result in self.result.executor_results:
            for fill in executor_result.fills:
                self.all_fills.append({
                    'datetime': datetime.fromtimestamp(fill.timestamp),
                    'timestamp': fill.timestamp,
                    'executor_id': fill.executor_id,
                    'order_id': fill.order_id,
                    'side': fill.side,
                    'price': float(fill.price),
                    'amount': float(fill.amount),
                    'fee': float(fill.fee),
                    'realized_pnl': float(fill.realized_pnl),
                })

        self.fills_df = pd.DataFrame(self.all_fills) if self.all_fills else pd.DataFrame()

    def plot_candlestick_with_fills(self) -> go.Figure:
        """
        Create candlestick chart with buy/sell fill markers.

        Returns:
            Plotly Figure with candlestick chart and fill markers
        """
        fig = go.Figure()

        # Add candlestick chart
        fig.add_trace(go.Candlestick(
            x=self.candles['datetime'],
            open=self.candles['open'],
            high=self.candles['high'],
            low=self.candles['low'],
            close=self.candles['close'],
            name='Price',
            increasing_line_color='#26a69a',
            decreasing_line_color='#ef5350',
        ))

        # Add fill markers if we have fills
        if not self.fills_df.empty:
            # Buy fills (green triangles pointing up)
            buy_fills = self.fills_df[self.fills_df['side'] == TradeType.BUY]
            if not buy_fills.empty:
                hover_text = [
                    f"BUY<br>"
                    f"Price: {row['price']:.2f}<br>"
                    f"Amount: {row['amount']:.8f}<br>"
                    f"Fee: {row['fee']:.4f}<br>"
                    f"Executor: {row['executor_id'][:12]}...<br>"
                    f"Time: {row['datetime']}"
                    for _, row in buy_fills.iterrows()
                ]

                fig.add_trace(go.Scatter(
                    x=buy_fills['datetime'],
                    y=buy_fills['price'],
                    mode='markers',
                    name='Buy Fills',
                    marker=dict(
                        symbol='triangle-up',
                        size=10,
                        color='#26a69a',
                        line=dict(color='white', width=1),
                    ),
                    hovertext=hover_text,
                    hoverinfo='text',
                ))

            # Sell fills (red triangles pointing down)
            sell_fills = self.fills_df[self.fills_df['side'] == TradeType.SELL]
            if not sell_fills.empty:
                hover_text = [
                    f"SELL<br>"
                    f"Price: {row['price']:.2f}<br>"
                    f"Amount: {row['amount']:.8f}<br>"
                    f"Fee: {row['fee']:.4f}<br>"
                    f"Executor: {row['executor_id'][:12]}...<br>"
                    f"Time: {row['datetime']}"
                    for _, row in sell_fills.iterrows()
                ]

                fig.add_trace(go.Scatter(
                    x=sell_fills['datetime'],
                    y=sell_fills['price'],
                    mode='markers',
                    name='Sell Fills',
                    marker=dict(
                        symbol='triangle-down',
                        size=10,
                        color='#ef5350',
                        line=dict(color='white', width=1),
                    ),
                    hovertext=hover_text,
                    hoverinfo='text',
                ))

        # Update layout
        fig.update_layout(
            title='Backtest Price Chart with Fills',
            xaxis_title='Time',
            yaxis_title='Price',
            xaxis_rangeslider_visible=False,
            hovermode='closest',
            template='plotly_dark',
            height=600,
        )

        return fig

    def plot_equity_curve(self) -> go.Figure:
        """
        Create equity curve chart with active executors.

        Returns:
            Plotly Figure with equity curve and active executors
        """
        # Create figure with secondary y-axis
        fig = make_subplots(
            rows=3, cols=1,
            row_heights=[0.5, 0.25, 0.25],
            subplot_titles=('Equity Curve', 'Active Executors', 'Cumulative Fees'),
            vertical_spacing=0.1,
        )

        equity_df = self.result.equity_curve

        # Equity curve
        fig.add_trace(
            go.Scatter(
                x=equity_df['datetime'],
                y=equity_df['equity'],
                mode='lines',
                name='Equity',
                line=dict(color='#2196f3', width=2),
                hovertemplate='<b>Equity</b>: %{y:.2f}<br><extra></extra>',
            ),
            row=1, col=1
        )

        # Add initial capital reference line
        initial_capital = float(self.result.final_capital - self.result.total_pnl)
        fig.add_hline(
            y=initial_capital,
            line_dash="dash",
            line_color="gray",
            annotation_text=f"Initial Capital: {initial_capital:.2f}",
            annotation_position="right",
            row=1, col=1
        )

        # Active executors
        fig.add_trace(
            go.Scatter(
                x=equity_df['datetime'],
                y=equity_df['active_executors'],
                mode='lines',
                name='Active Executors',
                line=dict(color='#ff9800', width=2),
                fill='tozeroy',
                fillcolor='rgba(255, 152, 0, 0.2)',
                hovertemplate='<b>Active</b>: %{y}<br><extra></extra>',
            ),
            row=2, col=1
        )

        # Cumulative fees
        fig.add_trace(
            go.Scatter(
                x=equity_df['datetime'],
                y=equity_df['fees'],
                mode='lines',
                name='Cumulative Fees',
                line=dict(color='#f44336', width=2),
                fill='tozeroy',
                fillcolor='rgba(244, 67, 54, 0.2)',
                hovertemplate='<b>Fees</b>: %{y:.2f}<br><extra></extra>',
            ),
            row=3, col=1
        )

        # Update layout
        fig.update_xaxes(title_text="Time", row=3, col=1)
        fig.update_yaxes(title_text="Equity (USDT)", row=1, col=1)
        fig.update_yaxes(title_text="Count", row=2, col=1)
        fig.update_yaxes(title_text="Fees (USDT)", row=3, col=1)

        fig.update_layout(
            title='Backtest Performance Metrics',
            template='plotly_dark',
            height=900,
            showlegend=True,
            hovermode='x unified',
        )

        return fig

    def show_all(self):
        """Display all charts (opens in browser)."""
        # Show candlestick chart
        fig_candles = self.plot_candlestick_with_fills()
        fig_candles.show()

        # Show equity curve
        fig_equity = self.plot_equity_curve()
        fig_equity.show()

    def save_html(self, filepath: str):
        """
        Save combined charts to a single HTML file.

        Args:
            filepath: Path to save the HTML file
        """
        # Create combined figure with both charts
        fig_candles = self.plot_candlestick_with_fills()
        fig_equity = self.plot_equity_curve()

        # Create a simple HTML file with both charts
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Grid Backtest Results</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #1e1e1e;
            color: #ffffff;
        }}
        h1 {{
            text-align: center;
            color: #2196f3;
        }}
        .chart-container {{
            margin-bottom: 30px;
        }}
        .summary {{
            background-color: #2d2d2d;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 30px;
        }}
        .summary h2 {{
            color: #2196f3;
            margin-top: 0;
        }}
        .metric {{
            display: inline-block;
            margin-right: 30px;
            margin-bottom: 10px;
        }}
        .metric-label {{
            color: #aaa;
            font-size: 14px;
        }}
        .metric-value {{
            font-size: 24px;
            font-weight: bold;
            color: #ffffff;
        }}
        .metric-value.positive {{
            color: #26a69a;
        }}
        .metric-value.negative {{
            color: #ef5350;
        }}
    </style>
</head>
<body>
    <h1>Grid Backtest Visualization</h1>

    <div class="summary">
        <h2>Summary</h2>
        <div class="metric">
            <div class="metric-label">Total PnL</div>
            <div class="metric-value {'positive' if self.result.total_pnl > 0 else 'negative'}">{float(self.result.total_pnl):.2f} USDT</div>
        </div>
        <div class="metric">
            <div class="metric-label">Total Fees</div>
            <div class="metric-value">{float(self.result.total_fees):.2f} USDT</div>
        </div>
        <div class="metric">
            <div class="metric-label">Total Volume</div>
            <div class="metric-value">{float(self.result.total_volume):.2f} USDT</div>
        </div>
        <div class="metric">
            <div class="metric-label">Total Trades</div>
            <div class="metric-value">{self.result.total_trades}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Final Capital</div>
            <div class="metric-value">{float(self.result.final_capital):.2f} USDT</div>
        </div>
        <div class="metric">
            <div class="metric-label">Executors</div>
            <div class="metric-value">{len(self.result.executor_results)}</div>
        </div>
    </div>

    <div class="chart-container" id="candlestick-chart"></div>
    <div class="chart-container" id="equity-chart"></div>

    <script>
        var candleData = {fig_candles.to_json()};
        Plotly.newPlot('candlestick-chart', candleData.data, candleData.layout);

        var equityData = {fig_equity.to_json()};
        Plotly.newPlot('equity-chart', equityData.data, equityData.layout);
    </script>
</body>
</html>
"""

        with open(filepath, 'w') as f:
            f.write(html_content)

        print(f"\nVisualization saved to: {filepath}")
