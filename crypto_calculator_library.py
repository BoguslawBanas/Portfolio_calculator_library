import yfinance as yf
import pandas as pd
from datetime import datetime
from currency_calculator_library import get_currency_exchange_rate_data

def get_data_from_ticker(df: pd.DataFrame, currency_from: str, currency_to: str, ticker_column_name: str) -> pd.DataFrame:
    start_date=df.index[0]
    currency_data=get_currency_exchange_rate_data(currency_from, currency_to, start_date)

    return get_data_from_ticker_with_currency_data(df, currency_data, ticker_column_name)

def get_data_from_ticker_with_currency_data(df: pd.DataFrame, currency_data: pd.DataFrame, ticker_column_name: str) -> pd.DataFrame:
    start_date=df.index[0]

    data=yf.download(df[ticker_column_name].iloc[0], start=start_date, end=datetime.today()+pd.DateOffset(days=1))
    data=data.droplevel(level='Ticker', axis=1)
    data.drop(columns=['High', 'Low', 'Open', 'Volume'], inplace=True)

    all_days=pd.DataFrame(
        index=pd.date_range(start=start_date, end=datetime.today(), freq='D')
    )
    data=all_days.join(data).ffill()
    data['Close']=data['Close']*currency_data['Close']

    data['Money_invested']=0.0
    data['Money_invested_after_penalty']=0.0
    data['Avg_price']=0.0
    data['Dividend']=0.0

    for idx, rows in df.iterrows():
        if rows['state']=='buy':
            data.loc[idx, 'Money_invested']=round(rows['money_invested'], 2)
            data.loc[idx, 'Money_invested_after_penalty']=round(rows['money_invested']*(1-rows['penalty']), 2)
            data.loc[idx, 'Avg_price']=(rows['money_invested']*rows['price_of_unit']*currency_data.loc[idx, 'Close'])
        elif rows['state']=='sell':
            pass
        elif rows['state']=='sell_tax':
            pass
        elif rows['state']=='dividend':
            data.loc[idx, 'Dividend']+=round(rows['money_invested'], 2)
        elif rows['state']=='dividend_tax':
            data.loc[idx, 'Dividend']-=round(rows['money_invested'], 2)

    data['Money_invested']=data['Money_invested'].cumsum()
    data['Money_invested_after_penalty']=data['Money_invested_after_penalty'].cumsum()
    data['Avg_price']=data['Avg_price'].cumsum()/data['Money_invested']
    data['Dividend']=data['Dividend'].cumsum()

    data['Profit_without_dividends']=round((data['Close']-data['Avg_price'])/data['Avg_price']*data['Money_invested']-data['Money_invested']+data['Money_invested_after_penalty'], 2)
    data['Profit']=round(data['Profit_without_dividends']+data['Dividend'], 2)
    data.drop(columns=['Close', 'Money_invested_after_penalty', 'Avg_price'], inplace=True)
    return data
