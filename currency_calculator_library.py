import pandas as pd
import yfinance as yf
from datetime import datetime

def get_currency_exchange_rate_data(currency_from: str, cuurency_to: str, start_date: datetime, end_date: datetime=datetime.today()) -> pd.DataFrame:
    if currency_from.upper()==cuurency_to.upper():
        data=pd.DataFrame({
            'Close': 1.0
        }, index=pd.date_range(start=start_date, end=end_date, freq='D'))
    else:
        data=yf.download(currency_from.upper()+cuurency_to.upper()+"=X", start=start_date, end=end_date+pd.DateOffset(days=1))
        data=data.droplevel(level='Ticker', axis=1)
        data.drop(columns=['High', 'Low', 'Open', 'Volume'], inplace=True)
        all_days=pd.DataFrame(
            index=pd.date_range(start=start_date, end=end_date, freq='D')
        )
        data=all_days.join(data).ffill().bfill()
    return data