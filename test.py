import ccxt
import datetime
import pandas as pd
import math
import requests
import schedule
import os
import time
import hmac
import hashlib
import atexit
import threading
import numpy as np

api_key = ""
secret = ""
myToken = ""
slackchannel = "#autotrade"

binance = ccxt.binance({
    'apiKey': api_key,
    'secret': secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future'
    }
})

symbol = "KLAY/USDT"
timedata = '1h'
futureleverage = 1

# 포지션 상태를 글로벌 변수로 설정
position = {
    "type": 'none',
    "amount": 0,
    "entry_price": 0,
    "initial_balance": 0
}

# 진입 금지 플래그
long_position_restriction = False
short_position_restriction = False

# 수동 포지션 종료 플래그
manual_exit_flag = False
manual_exit_active = False  # 수동 포지션 종료 후 자동 진입 방지 플래그

# 초기 자산 기록
initial_balance = 0

# 익절매 및 손절매 비율
take_profit_ratio = 0.05
stop_loss_ratio = 0.03
stop_loss_ratio_immediately = 0.005

# 데이터 가져오는 함수
def fetch_ohlcv_data(exchange, symbol, timeframe='1h', limit=1000):
    """바이낸스에서 OHLCV 데이터를 가져옴"""
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def fetch_4h_data(exchange, symbol, limit=1000):
    """바이낸스에서 4시간 봉 OHLCV 데이터를 가져옴"""
    return fetch_ohlcv_data(exchange, symbol, timeframe='4h', limit=limit)

def fetch_weekly_data(exchange, symbol, limit=100):
    """바이낸스에서 주 단위 OHLCV 데이터를 가져옴"""
    return fetch_ohlcv_data(exchange, symbol, timeframe='1w', limit=limit)

def fetch_6m_data(exchange, symbol, limit=100):
    """바이낸스에서 6개월 봉 OHLCV 데이터를 가져옴"""
    return fetch_ohlcv_data(exchange, symbol, timeframe='6M', limit=limit)

# 포지션 기록 함수
def record_position_to_csv(action, position_type, entry_price, entry_cost, rsi, macd, signal, upper_band, lower_band, short_ma, long_ma, exit_price=None, profit=None, profit_rate=None, final_balance=None):
    """포지션을 CSV 파일에 기록"""
    file_exists = os.path.isfile('trades.csv')
    columns = ['Datetime', 'Action', 'Position Type', 'Entry Price', 'Exit Price', 'Entry Cost', 'Final Balance', 'Profit', 'Profit Rate', 'RSI', 'MACD', 'Signal', 'Upper Band', 'Lower Band', 'Short MA', 'Long MA']
    data = {
        'Datetime': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'Action': action,
        'Position Type': position_type,
        'Entry Price': entry_price,
        'Exit Price': exit_price,
        'Entry Cost': entry_cost,
        'Final Balance': final_balance,
        'Profit': profit,
        'Profit Rate': profit_rate,
        'RSI': rsi,
        'MACD': macd,
        'Signal': signal,
        'Upper Band': upper_band,
        'Lower Band': lower_band,
        'Short MA': short_ma,
        'Long MA': long_ma,
    }
    df = pd.DataFrame([data])

    if not file_exists:
        df.to_csv('trades.csv', index=False, columns=columns)
    else:
        df.to_csv('trades.csv', index=False, mode='a', header=False, columns=columns)

# 코인 속성 계산 함수
def calculate_coin_attributes(df):
    """코인 속성을 계산"""
    df['price_change'] = df['close'].pct_change()
    time_vs_rise_ratio = df['price_change'].mean() * 100
    
    rises = df['price_change'][df['price_change'] > 0]
    corrections = df['price_change'][df['price_change'] < 0]
    rise_vs_correction_ratio = -corrections.mean() / rises.mean() if rises.mean() != 0 else 0
    
    rebounds = df['price_change'][df['price_change'] > 0]
    drops = df['price_change'][df['price_change'] < 0]
    drop_vs_rebound_ratio = rebounds.mean() / -drops.mean() if drops.mean() != 0 else 0
    
    return time_vs_rise_ratio, rise_vs_correction_ratio, drop_vs_rebound_ratio

# 주봉 분석 함수
def analyze_weekly_candles(df):
    """주봉 데이터를 분석하여 윗꼬리 아랫꼬리 패턴을 파악"""
    weekly_candles = df.resample('W', on='timestamp').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    weekly_candles['upper_wick'] = weekly_candles['high'] - weekly_candles[['open', 'close']].max(axis=1)
    weekly_candles['lower_wick'] = weekly_candles[['open', 'close']].min(axis=1) - weekly_candles['low']
    return weekly_candles

# 7080기법 분석 함수
def analyze_7080_pattern(df):
    """7080 기법 분석"""
    highest = df['high'].max()
    correction_point = highest * 0.7
    support_point = highest * 0.8
    return correction_point, support_point

# 9시 캔들 분석 함수
def analyze_9am_candle(df):
    """9시 캔들 분석"""
    df['hour'] = df['timestamp'].dt.hour
    am9_candle = df[df['hour'] == 9]
    upper_wicks = am9_candle['high'] - am9_candle[['open', 'close']].max(axis=1)
    lower_wicks = am9_candle[['open', 'close']].min(axis=1) - am9_candle['low']
    return upper_wicks, lower_wicks

# 볼린저 밴드 계산 함수
def calculate_bollinger_bands(df, period=20, std_dev=2):
    """볼린저 밴드 계산"""
    df['ma'] = df['close'].rolling(window=period).mean()
    df['std'] = df['close'].rolling(window=period).std()
    df['upper_band'] = df['ma'] + (df['std'] * std_dev)
    df['lower_band'] = df['ma'] - (df['std'] * std_dev)
    return df

# 이동평균선 계산 함수
def calculate_moving_averages(df, periods=[7, 25, 99, 130]):
    """이동평균선 계산"""
    for period in periods:
        df[f'ma_{period}'] = df['close'].rolling(window=period).mean()
    return df

# RSI 계산 함수
def calculate_rsi(df, period=14):
    """RSI 계산"""
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    return df

# 스토캐스틱 RSI 계산 함수
def calculate_stochastic_rsi(df, period=14, k_period=3, d_period=3):
    """스토캐스틱 RSI 계산"""
    rsi = calculate_rsi(df, period)['rsi']
    stoch_rsi = (rsi - rsi.rolling(window=period).min()) / (rsi.rolling(window=period).max() - rsi.rolling(window=period).min())
    df['stoch_rsi_k'] = stoch_rsi.rolling(window=k_period).mean()
    df['stoch_rsi_d'] = df['stoch_rsi_k'].rolling(window=d_period).mean()
    return df

# 낙폭과 반등폭 계산 함수
def find_abcd_points(df):
    """주어진 데이터프레임에서 a, b, c, d 지점을 찾음"""
    a_index = df['high'].idxmax()
    b_index = df['low'][a_index:].idxmax() + a_index
    c_index = df['low'][b_index:].idxmin() + b_index
    d_index = df['low'][c_index:].idxmax() + c_index

    a = df.iloc[a_index]
    b = df.iloc[b_index]
    c = df.iloc[c_index]
    d = df.iloc[d_index]

    return a, b, c, d

def calculate_proportion(a, b, c, d):
    """비례식을 이용하여 익절가와 손절가를 계산함"""
    ad = a['high'] - d['low']
    bc = b['high'] - c['low']
    predicted_d = c['low'] - (ad * bc / (b['high'] - a['low']))

    return predicted_d

# 매매 함수에서 새로운 로직을 추가
def enter_position(exchange, symbol, cur_price, amount):
    global position, long_position_restriction, short_position_restriction, manual_exit_flag, manual_exit_active
    
    if manual_exit_active:
        print("종료 후 대기 중... 자동 매매 비활성화")
        return
    
    if manual_exit_flag:
        manual_exit_flag = False
        return  # 수동 종료 플래그 확인 후 즉시 리턴
    
    dif, dea = get_macd(exchange, symbol)
    rsi = get_rsi(exchange, symbol)
    upper_band, lower_band, middle_band = get_bollinger_bands(exchange, symbol)
    short_ma, long_ma = get_ma(exchange, symbol)

    # 4시간 봉 데이터와 돌파 횟수 계산
    df_4h = fetch_4h_data(exchange, symbol)
    highest, lowest = find_high_low_points(df_4h)
    highest_breakouts = count_breakouts(df_4h, highest['high'], direction='up')
    lowest_breakouts = count_breakouts(df_4h, lowest['low'], direction='down')

    # 프랙탈 분석
    df = fetch_ohlcv_data(exchange, symbol, timeframe=timedata)
    fractal_index, similarity = fractal_analysis(df)

    # 주봉 패턴 분석
    df_weekly = fetch_weekly_data(exchange, symbol)
    weekly_pattern = analyze_weekly_pattern(df_weekly)

    # 6개월 봉 데이터 분석
    df_6m = fetch_6m_data(exchange, symbol)
    resistance_line, support_line = calculate_resistance_lines(df_6m)

    # 낙폭과 반등폭 계산
    a, b, c, d = find_abcd_points(df)
    predicted_d = calculate_proportion(a, b, c, d)

    print(f"DEBUG: dif={dif}, dea={dea}, rsi={rsi}, upper_band={upper_band}, lower_band={lower_band}, middle_band={middle_band}, short_ma={short_ma}, long_ma={long_ma}, cur_price={cur_price}, highest_breakouts={highest_breakouts}, lowest_breakouts={lowest_breakouts}, fractal_index={fractal_index}, similarity={similarity}, weekly_pattern={weekly_pattern}, resistance_line={resistance_line}, support_line={support_line}, predicted_d={predicted_d}")

    if manual_exit_active:  # 수동 종료 후 자동 진입 방지
        return

    if manual_exit_flag:  # 수동 종료 플래그 확인
        manual_exit_flag = False  # 플래그 해제 후 바로 리턴
        return

    if position['type'] == 'none':
        if cur_price > resistance_line:
            enter_short_position(exchange, symbol, amount, cur_price, rsi, dif, dea, upper_band, lower_band, short_ma, long_ma)
            post_message(myToken, slackchannel, f"숏 포지션 진입 조건 충족 (저항선 돌파)")
        elif cur_price < support_line:
            enter_long_position(exchange, symbol, amount, cur_price, rsi, dif, dea, upper_band, lower_band, short_ma, long_ma)
            post_message(myToken, slackchannel, f"롱 포지션 진입 조건 충족 (지지선 돌파)")
        elif weekly_pattern:
            enter_short_position(exchange, symbol, amount, cur_price, rsi, dif, dea, upper_band, lower_band, short_ma, long_ma)
            post_message(myToken, slackchannel, f"숏 포지션 진입 조건 충족 (네 번째 주봉 패턴 발견)")
        elif highest_breakouts >= 5:
            enter_short_position(exchange, symbol, amount, cur_price, rsi, dif, dea, upper_band, lower_band, short_ma, long_ma)
            post_message(myToken, slackchannel, f"숏 포지션 진입 조건 충족 (최고점 돌파 5회 이상)")
        elif lowest_breakouts >= 5:
            enter_long_position(exchange, symbol, amount, cur_price, rsi, dif, dea, upper_band, lower_band, short_ma, long_ma)
            post_message(myToken, slackchannel, f"롱 포지션 진입 조건 충족 (최저점 돌파 5회 이상)")
        elif dif > dea and not long_position_restriction and short_ma > long_ma and cur_price < middle_band:
            enter_long_position(exchange, symbol, amount, cur_price, rsi, dif, dea, upper_band, lower_band, short_ma, long_ma)
            post_message(myToken, slackchannel, f"롱 포지션 진입 조건 충족 (dif > dea, short_ma > long_ma, cur_price < middle_band)")
        elif rsi <= 20 and not long_position_restriction and cur_price < lower_band:
            enter_long_position(exchange, symbol, amount, cur_price, rsi, dif, dea, upper_band, lower_band, short_ma, long_ma)
            post_message(myToken, slackchannel, f"롱 포지션 진입 조건 충족 (rsi <= 20, cur_price < lower_band)")
        elif dif < dea and not short_position_restriction and short_ma < long_ma and cur_price > middle_band:
            enter_short_position(exchange, symbol, amount, cur_price, rsi, dif, dea, upper_band, lower_band, short_ma, long_ma)
            post_message(myToken, slackchannel, f"숏 포지션 진입 조건 충족 (dif < dea, short_ma < long_ma, cur_price > middle_band)")
        elif rsi >= 80 and not short_position_restriction and cur_price > upper_band:
            enter_short_position(exchange, symbol, amount, cur_price, rsi, dif, dea, upper_band, lower_band, short_ma, long_ma)
            post_message(myToken, slackchannel, f"숏 포지션 진입 조건 충족 (rsi >= 80, cur_price > upper_band)")

    elif position['type'] == 'long':
        if dif > dea and rsi >= 80 and cur_price > position['entry_price']:
            post_message(myToken, slackchannel, f"롱 포지션 종료 조건 충족 (dif > dea, rsi >= 80, cur_price > entry_price)")
            exit_position(exchange, symbol, position['amount'])
        elif cur_price >= position['entry_price'] * (1 + take_profit_ratio * futureleverage):
            post_message(myToken, slackchannel, "## 롱 포지션 익절 ##")
            exit_position(exchange, symbol, position['amount'])
        elif cur_price <= position['entry_price'] * (1 - stop_loss_ratio * futureleverage):
            post_message(myToken, slackchannel, "## 롱 포지션 손절 ##")
            exit_position(exchange, symbol, position['amount'])

    elif position['type'] == 'short':
        if dif < dea and rsi <= 20 and cur_price < position['entry_price']:
            post_message(myToken, slackchannel, f"숏 포지션 종료 조건 충족 (dif < dea, rsi <= 20, cur_price < entry_price)")
            exit_position(exchange, symbol, position['amount'])
        elif cur_price <= position['entry_price'] * (1 - take_profit_ratio * futureleverage):
            post_message(myToken, slackchannel, "## 숏 포지션 익절 ##")
            exit_position(exchange, symbol, position['amount'])
        elif cur_price >= position['entry_price'] * (1 + stop_loss_ratio * futureleverage):
            post_message(myToken, slackchannel, "## 숏 포지션 손절 ##")
            exit_position(exchange, symbol, position['amount'])

# Trade
def trade(symbol):
    balance = get_balance()
    cur_price = get_current_price(symbol)
    amount = cal_amount(balance, cur_price)
    enter_position(binance, symbol, cur_price, amount)
    print(f"포지션 상태: {position}\n{datetime.datetime.now()} 현재 잔고: {balance:.2f}, 암호화폐: {symbol}\n현재가: {cur_price:.7f}")

    time.sleep(1)

# 기존에 있던 다른 함수들 생략

# 자동매매 시작 알람에 추가 정보 포함
post_message(myToken, slackchannel, "@@ 선물거래 자동매매 시작 @@")

# 초기 자산 기록
initial_balance = get_balance()
set_isolated_margin_and_leverage(binance, symbol, futureleverage)

# 종료시 슬랙 알람
def notify_exit():
    final_balance = get_balance()
    profit = final_balance - initial_balance
    profit_rate = (final_balance / initial_balance - 1) * 100
    message = f"<선물거래 자동매매 종료>\n초기 자산: {initial_balance:.2f} USDT\n최종 자산: {final_balance:.2f} USDT\n총 수익: {profit:.2f} USDT\n수익률: {profit_rate:.2f} %"
    post_message(myToken, slackchannel, message)

# atexit 모듈을 사용하여 프로그램 종료 시 notify_exit 함수 호출
atexit.register(notify_exit)

# 각 코인에 대해 10초마다 trade 함수 실행
schedule.every(10).seconds.do(lambda: trade(symbol))

# 수동 포지션 확인 주기 설정 (예: 5초마다)
schedule.every(5).seconds.do(check_and_sync_manual_positions)

while True:
    schedule.run_pending()
    time.sleep(1)
