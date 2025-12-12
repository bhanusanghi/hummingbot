"""
Backtest report generation with fill recording and summary statistics.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple
import pandas as pd

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.order_candidate import PerpetualOrderCandidate


@dataclass
class Fill:
    """Represents a filled order"""
    timestamp: float
    side: TradeType
    order_price: Decimal
    fill_price: Decimal
    amount: Decimal
    order: PerpetualOrderCandidate
    candle_open: float
    candle_high: float
    candle_low: float
    candle_close: float
    candle_volume: float
    inventory_after: Decimal
    pnl: Decimal = Decimal("0")  # Calculated later


class BacktestReport:
    """Collects and reports backtest results"""
    
    def __init__(self):
        self.fills: List[Fill] = []
        self._inventory_history: List[Tuple[float, Decimal]] = []  # (timestamp, inventory)
    
    def record_fill(self, fill: Fill, candle: dict, inventory: Decimal):
        """Record a fill event with candle context"""
        fill.candle_open = candle.get('open', 0)
        fill.candle_high = candle.get('high', 0)
        fill.candle_low = candle.get('low', 0)
        fill.candle_close = candle.get('close', 0)
        fill.candle_volume = candle.get('volume', 0)
        fill.inventory_after = inventory
        self.fills.append(fill)
        self._inventory_history.append((fill.timestamp, inventory))
    
    def calculate_pnl(self):
        """Calculate PnL for each fill based on inventory changes"""
        # Simple PnL calculation: assume opposite side fills close positions
        # This is a simplified model - real PnL depends on entry prices
        for i, fill in enumerate(self.fills):
            if i == 0:
                fill.pnl = Decimal("0")  # First fill, no PnL
            else:
                # Simplified: PnL = (fill_price - previous_fill_price) * amount * direction
                # This is approximate - real calculation needs entry prices
                prev_fill = self.fills[i - 1]
                if fill.side != prev_fill.side:
                    # Opposite direction fill - assume closing position
                    price_diff = fill.fill_price - prev_fill.fill_price
                    if prev_fill.side == TradeType.BUY:
                        # Bought then sold - profit if sell > buy
                        fill.pnl = price_diff * fill.amount
                    else:
                        # Sold then bought - profit if buy < sell
                        fill.pnl = -price_diff * fill.amount
                else:
                    fill.pnl = Decimal("0")  # Same direction, no realized PnL
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert fills to DataFrame for analysis"""
        if not self.fills:
            return pd.DataFrame()
        
        self.calculate_pnl()
        
        data = []
        for fill in self.fills:
            data.append({
                'timestamp': fill.timestamp,
                'side': 'buy' if fill.side == TradeType.BUY else 'sell',
                'order_price': float(fill.order_price),
                'fill_price': float(fill.fill_price),
                'amount': float(fill.amount),
                'pnl': float(fill.pnl),
                'candle_open': fill.candle_open,
                'candle_high': fill.candle_high,
                'candle_low': fill.candle_low,
                'candle_close': fill.candle_close,
                'candle_volume': fill.candle_volume,
                'inventory_after': float(fill.inventory_after),
            })
        
        return pd.DataFrame(data)
    
    def save(self, filepath: str):
        """Save report to CSV file"""
        df = self.to_dataframe()
        if not df.empty:
            df.to_csv(filepath, index=False)
        else:
            # Create empty file with headers
            empty_df = pd.DataFrame(columns=[
                'timestamp', 'side', 'order_price', 'fill_price', 'amount', 'pnl',
                'candle_open', 'candle_high', 'candle_low', 'candle_close', 'candle_volume', 'inventory_after'
            ])
            empty_df.to_csv(filepath, index=False)
    
    def print_summary(self):
        """Print summary statistics"""
        if not self.fills:
            print("No fills recorded.")
            return
        
        df = self.to_dataframe()
        
        total_trades = len(self.fills)
        buy_trades = len(df[df['side'] == 'buy'])
        sell_trades = len(df[df['side'] == 'sell'])
        total_pnl = df['pnl'].sum()
        
        # Calculate win rate (simplified - positive PnL trades)
        winning_trades = len(df[df['pnl'] > 0])
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        # Max drawdown (simplified - based on cumulative PnL)
        df['cumulative_pnl'] = df['pnl'].cumsum()
        running_max = df['cumulative_pnl'].expanding().max()
        drawdown = df['cumulative_pnl'] - running_max
        max_drawdown = drawdown.min()
        
        # Average fill price vs order price
        avg_order_price = df['order_price'].mean()
        avg_fill_price = df['fill_price'].mean()
        avg_slippage = avg_fill_price - avg_order_price
        
        print("\n" + "=" * 60)
        print("BACKTEST SUMMARY")
        print("=" * 60)
        print(f"Total Trades:        {total_trades}")
        print(f"  Buy Trades:        {buy_trades}")
        print(f"  Sell Trades:       {sell_trades}")
        print(f"Total PnL:           {total_pnl:.6f}")
        print(f"Win Rate:            {win_rate:.2f}%")
        print(f"Max Drawdown:        {max_drawdown:.6f}")
        print(f"Avg Order Price:     {avg_order_price:.4f}")
        print(f"Avg Fill Price:      {avg_fill_price:.4f}")
        print(f"Avg Slippage:        {avg_slippage:.6f}")
        print("=" * 60)

