import pandas as pd
from datetime import datetime
from portfolio_calculator_library import create_dataframe_and_get_data as split_transactions_from_file, get_data_from_dataframe as split_transactions_from_dataframe, merge_dataframes

#expected columns per transaction row: state ('deposit'/'withdrawal'), amount, rate_type ('fixed'/'variable'),
#rate (fixed annual rate if rate_type=='fixed', or spread added to the variable base rate if rate_type=='variable'),
#capitalization_months (interest compounding period), tax (%, optional, defaults to 19.0)

def create_dataframe_and_get_data(dataframe_file: str, account_column_name: str) -> list:
    account_dataframes=split_transactions_from_file(dataframe_file, account_column_name)
    data=[get_data_from_account(df) for df in account_dataframes]
    final_df=merge_dataframes(data)
    return [final_df]

def get_data_from_dataframe(dataframe: pd.DataFrame, account_column_name: str) -> list:
    account_dataframes=split_transactions_from_dataframe(dataframe, account_column_name)
    data=[get_data_from_account(df) for df in account_dataframes]
    final_df=merge_dataframes(data)
    return [final_df]

def get_interest_rate_data() -> pd.DataFrame:
    interest_rate_df=pd.read_csv('interest_rate.csv')
    interest_rate_df['date']=pd.to_datetime(interest_rate_df['date'], format='%m-%Y')
    interest_rate_df=interest_rate_df.set_index('date')
    interest_rate_df=pd.DataFrame({}, index=pd.date_range(start=interest_rate_df.index.min(), end=datetime.today(), freq='D')).join(interest_rate_df).ffill()
    return interest_rate_df

def get_data_from_account(df: pd.DataFrame, interest_rate_df: pd.DataFrame=None) -> pd.DataFrame:
    start_date=df.index[0]
    end_date=datetime.today()

    rate_type=df['rate_type'].iloc[0]
    account_rate=float(df['rate'].iloc[0])
    capitalization_months=int(df['capitalization_months'].iloc[0])
    tax=float(df['tax'].iloc[0]) if 'tax' in df.columns else 19.0

    if rate_type=='variable' and interest_rate_df is None:
        interest_rate_df=get_interest_rate_data()

    data=pd.DataFrame({
        'Money_invested': 0.0,
        'Profit_without_dividends': 0.0,
        'Profit': 0.0,
    }, index=pd.date_range(start=start_date, end=end_date, freq='D'))

    for idx, row in df.iterrows():
        if row['state']=='deposit':
            data.loc[idx, 'Money_invested']+=round(row['amount'], 2)
        elif row['state']=='withdrawal':
            data.loc[idx, 'Money_invested']-=round(row['amount'], 2)

    data['Money_invested']=data['Money_invested'].cumsum()

    capitalized_interest=0.0
    uncapitalized_interest=0.0
    cumulative_profit=0.0
    last_capitalization_date=start_date

    for idx in data.index:
        interest_bearing_balance=data.loc[idx, 'Money_invested']+capitalized_interest

        if rate_type=='fixed':
            annual_rate=account_rate
        else:
            annual_rate=interest_rate_df.loc[idx, 'rate']+account_rate

        daily_interest=interest_bearing_balance*(annual_rate/100.0)/365.0*(1-tax/100.0)
        uncapitalized_interest+=daily_interest
        cumulative_profit+=daily_interest
        data.loc[idx, 'Profit']=round(cumulative_profit, 2)

        months_elapsed=(idx.year-last_capitalization_date.year)*12+(idx.month-last_capitalization_date.month)
        if idx!=start_date and months_elapsed>=capitalization_months:
            capitalized_interest+=uncapitalized_interest
            uncapitalized_interest=0.0
            last_capitalization_date=idx

    data['Profit_without_dividends']=data['Profit']
    return data
