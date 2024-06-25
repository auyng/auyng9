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
import csv
import atexit

api_key = ""
secret = ""
myToken = ""
slackchannel = "#autotrade"

binance = ccxt.binance(config={
    'apiKey': api_key,
    'secret': secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future'
    }
})

symbol = "ETH/USDT"
timedata = '1m'
futureleverage = 1
k_i = 0.5

# 포지션 상태를 글로벌 변수로 설정
position = {
    "type": 'none',
    "amount": 0,
    "entry_price": 0,
    "initial_balance": 0  # 초기 자산을 추가
}

# 초기 자산 기록
initial_balance = 0

# 거래 기록을 저장할 CSV 파일 경로
TRADE_HISTORY_FILE = os.path.expanduser("~/trade_history.csv")
if not os.path.exists(TRADE_HISTORY_FILE):
    with open(TRADE_HISTORY_FILE, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(["timestamp", "ticker", "type", "price", "volume", "conditions", "rsi", "upper_band", "middle_band", "lower_band", "sentiment_index", "short_ma", "long_ma", "macd", "signal"])

def post_message(token, channel, text, attempts=3):
    """슬랙 메시지 전송"""
    for attempt in range(attempts):
        response = requests.post("https://slack.com/api/chat.postMessage",
                                 headers={"Authorization": "Bearer " + token},
                                 data={"channel": channel, "text": text})
        if response.ok:
            print(f"메시지 전송 성공:\n{text}")
            break
        else:
            print(f"메시지 전송 실패: {response.text}")
            time.sleep(1)

def generate_signature(query_string, secret):
    return hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()

def set_isolated_margin_and_leverage(exchange, symbol, leverage):
    """Isolated 마진 모드와 레버리지 설정"""
    exchange.load_markets()  # 마켓 데이터를 로드합니다.
    market = exchange.market(symbol)
    
    # Isolated 마진 모드 설정
    base_url = 'https://fapi.binance.com'
    endpoint_margin = '/fapi/v1/marginType'
    timestamp = int(time.time() * 1000)
    params_margin = f'symbol={market["id"]}&marginType=ISOLATED&timestamp={timestamp}'
    signature_margin = generate_signature(params_margin, secret)
    url_margin = f"{base_url}{endpoint_margin}?{params_margin}&signature={signature_margin}"
    
    headers = {
        'X-MBX-APIKEY': api_key
    }
    
    response_margin = requests.post(url_margin, headers=headers)
    if response_margin.status_code == 200:
        post_message(myToken, slackchannel, "## Isolated 마진 모드 설정 성공 ##")
    else:
        post_message(myToken, slackchannel, f"!! Isolated 마진 모드 설정 오류 !!\n{response_margin.json()}")
    
    # 레버리지 설정
    endpoint_leverage = '/fapi/v1/leverage'
    params_leverage = f'symbol={market["id"]}&leverage={leverage}&timestamp={timestamp}'
    signature_leverage = generate_signature(params_leverage, secret)
    url_leverage = f"{base_url}{endpoint_leverage}?{params_leverage}&signature={signature_leverage}"
    
    response_leverage = requests.post(url_leverage, headers=headers)
    if response_leverage.status_code == 200:
        post_message(myToken, slackchannel, "## 레버리지 설정 성공 ##")
    else:
        post_message(myToken, slackchannel, f"!! 레버리지 설정 실패 !!\n{response_leverage.json()}")

def cal_target(exchange, symbol, timeframe=timedata, k=k_i):
    """변동성 돌파 전략으로 매수 목표가 조회"""
    coin = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=10)
    df = pd.DataFrame(data=coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    previous = df.iloc[-2]
    current = df.iloc[-1]
    long_target = current['open'] + (previous['high'] - previous['low']) * k
    short_target = current['open'] - (previous['high'] - previous['low']) * k
    return long_target, short_target

def get_balance():
    """잔고 조회"""
    balance = binance.fetch_balance()
    return balance['total']['USDT']

def get_current_price(symbol):
    """현재가 조회"""
    ticker = binance.fetch_ticker(symbol)
    return ticker['last']

def get_rsi(exchange, symbol, timeframe=timedata, period=14):
    """RSI (Relative Strength Index) 조회"""
    coin = exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=period+1)
    df = pd.DataFrame(coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def get_bollinger_bands(exchange, symbol, timeframe=timedata, period=20):
    """볼린저밴드 조회"""
    coin = exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=period+1)
    df = pd.DataFrame(coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    tp = df['close']
    middle_band = tp.rolling(window=period).mean() #중간밴드
    std = tp.rolling(window=period).std()
    upper_band = middle_band + (std * 2)
    lower_band = middle_band - (std * 2)
    return upper_band.iloc[-1], middle_band.iloc[-1], lower_band.iloc[-1]

def get_macd(exchange, symbol, timeframe=timedata, short_period=12, long_period=26, signal_period=9):
    """MACD 조회"""
    coin = exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=long_period + signal_period + 1)
    df = pd.DataFrame(coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    short_ema = df['close'].ewm(span=short_period, adjust=False).mean()
    long_ema = df['close'].ewm(span=long_period, adjust=False).mean()
    dif = short_ema - long_ema
    dea = dif.ewm(span=signal_period, adjust=False).mean()
    return dif.iloc[-1], dea.iloc[-1]

def get_moving_averages(exchange, symbol, short_window=7, long_window=30):
    """단기 및 장기 이동평균 조회"""
    coin = exchange.fetch_ohlcv(symbol=symbol, timeframe=timedata, limit=long_window + 1)
    df = pd.DataFrame(coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    short_ma = df['close'].rolling(window=short_window).mean().iloc[-1]
    long_ma = df['close'].rolling(window=long_window).mean().iloc[-1]
    return short_ma, long_ma

def cal_amount(usdt_balance, cur_price):
    """매매할 암호화폐 양 계산"""
    portion = 1
    usdt_trade = usdt_balance * portion
    amount = math.floor((usdt_trade * 1000000) / cur_price) / 1000000
    
    # 최소 주문 명목 가치 확인 (최소 5 USDT)
    min_notional = 5
    if amount * cur_price < min_notional:
        amount = min_notional / cur_price
    
    return amount

#####포지션 진입######

#롱 포지션 진입
def enter_long_position(exchange, symbol, cur_price, long_target, amount):
    global position
    
    rsi = get_rsi(exchange, symbol)
    upper_band, middle_band, lower_band = get_bollinger_bands(exchange, symbol)
    dif, dea = get_macd(exchange, symbol)
    short_ma, long_ma = get_moving_averages(exchange, symbol)
       
    buy_conditions = []

    if cur_price > long_target:
        buy_conditions.append("현재가 > 목표가")
    
    if cur_price < lower_band or cur_price > middle_band:
        buy_conditions.append("현재가 < 볼린저 밴드 하단 or 현재가 > 볼린저 밴드 중단")
    
    if dif > dea:
        buy_conditions.append("DIF > DEA")
    
    if short_ma > long_ma:
        buy_conditions.append("단기 이동평균 > 장기 이동평균")
    
    if len(buy_conditions) >= 3:
        try:
            order = exchange.create_market_buy_order(symbol=symbol, amount=amount)
            position['type'] = 'long'
            position['amount'] = amount
            position['entry_price'] = cur_price
            position['initial_balance'] = get_balance()  # 초기 자산 기록
            post_message(myToken, slackchannel, f"## 롱 포지션 진입 성공 ##\n진입 조건 충족: {', '.join(buy_conditions)}")
        except Exception as e:
            post_message(myToken, slackchannel, f"!! 롱 포지션 진입 실패 !!\n{e}")

## 숏 포지션 진입
def enter_short_position(exchange, symbol, cur_price, short_target, amount):
    global position
    
    rsi = get_rsi(exchange, symbol)
    upper_band, middle_band, lower_band = get_bollinger_bands(exchange, symbol)
    dif, dea = get_macd(exchange, symbol)
    short_ma, long_ma = get_moving_averages(exchange, symbol)
        
    sell_conditions = []

    if cur_price < short_target:
        sell_conditions.append("현재가 < 목표가")
    
    if cur_price > upper_band or cur_price > middle_band:
        sell_conditions.append("현재가 > 볼린저 밴드 상단 or 현재가 > 볼린저 밴드 중단")

    if dif < dea:
        sell_conditions.append("DIF < DEA")

    if short_ma < long_ma:
        sell_conditions.append("단기 이동평균 < 장기 이동평균")
    
    if len(sell_conditions) >= 3:
        try:
            order = exchange.create_market_sell_order(symbol=symbol, amount=amount)
            position['type'] = 'short'
            position['amount'] = amount
            position['entry_price'] = cur_price
            position['initial_balance'] = get_balance()  # 초기 자산 기록
            post_message(myToken, slackchannel, f"## 숏 포지션 진입 성공 ##\n진입 조건 충족: {', '.join(sell_conditions)}")
        except Exception as e:
            post_message(myToken, slackchannel, f"!! 숏 포지션 진입 실패 !!\n{e}")

# 포지션 종료 함수
def exit_position(exchange, symbol, amount):
    global position
    
    try:
        final_price = get_current_price(symbol)
        if position['type'] == 'long':
            order = exchange.create_market_sell_order(symbol=symbol, amount=amount)
            profit = (final_price - position['entry_price']) * amount
            profit_rate = (final_price / position['entry_price'] - 1) * 100
            post_message(myToken, slackchannel, f"@@@@ 롱 포지션 종료 @@@@\n투자 자산: {position['initial_balance']:.2f} USDT\n수익 금액: {profit:.2f} USDT\n수익률: {profit_rate:.2f}%")
        elif position['type'] == 'short':
            order = exchange.create_market_buy_order(symbol=symbol, amount=amount)
            profit = (position['entry_price'] - final_price) * amount
            profit_rate = (position['entry_price'] / final_price - 1) * 100
            post_message(myToken, slackchannel, f"@@@@ 숏 포지션 종료 @@@@\n투자 자산: {position['initial_balance']:.2f} USDT\n수익 금액: {profit:.2f} USDT\n수익률: {profit_rate:.2f}%")
        position['type'] = 'none'
        position['amount'] = 0
        position['entry_price'] = 0
        position['initial_balance'] = 0
    except Exception as e:
        post_message(myToken, slackchannel, f"!!!!!! 포지션 종료 실패 !!!!!!\n{e}")

# Threshold values for DIF and moving averages to determine significant difference
threshold_dif = 1.5
threshold_ma = 4

# 통합 함수 ( 종료 조건 포함 )
def enter_position(exchange, symbol, cur_price, long_target, short_target, amount):
    global position
    
    dif, dea = get_macd(exchange, symbol)
    short_ma, long_ma = get_moving_averages(exchange, symbol)
    rsi = get_rsi(exchange, symbol)
    upper_band, middle_band, lower_band = get_bollinger_bands(exchange, symbol)

    leverage = futureleverage
    long_stop_loss = 0.97 ** leverage
    long_take_profit = 1.03 ** leverage
    short_stop_loss = 1.03 ** leverage
    short_take_profit = 0.97 ** leverage

    if position['type'] == 'none':
        enter_long_position(exchange, symbol, cur_price, long_target, amount)
        enter_short_position(exchange, symbol, cur_price, short_target, amount)
    else:
        exit_conditions = []

        if position['type'] == 'long':
            if cur_price >= position['entry_price'] * long_take_profit:
                exit_position(exchange, symbol, position['amount'])
                post_message(myToken, slackchannel, f"롱 포지션 종료: {long_take_profit * 100 - 100}% 이익 실현")
                return
            if cur_price <= position['entry_price'] * long_stop_loss:
                exit_position(exchange, symbol, position['amount'])
                post_message(myToken, slackchannel, f"롱 포지션 종료: {100 - long_stop_loss * 100}% 손해 실현")
                return
            if dif > dea and abs(dif - dea) > threshold_dif:
                exit_conditions.append("DIF > DEA (차이 벌어짐)")
            if short_ma > long_ma and abs(short_ma - long_ma) > threshold_ma:
                exit_conditions.append("단기 이동평균 > 장기 이동평균 (차이 벌어짐)")
            if rsi >= 70:
                exit_conditions.append("RSI >= 70")
            if cur_price >= upper_band:
                exit_conditions.append("현재가 >= 볼린저 밴드 상단")

            if len(exit_conditions) >= 3:
                exit_position(exchange, symbol, position['amount'])
                post_message(myToken, slackchannel, f"롱 포지션 종료 조건 충족: {', '.join(exit_conditions)}")

        elif position['type'] == 'short':
            if cur_price <= position['entry_price'] * short_take_profit:
                exit_position(exchange, symbol, position['amount'])
                post_message(myToken, slackchannel, f"숏 포지션 종료: {100 - short_take_profit * 100}% 이익 실현")
                return
            if cur_price >= position['entry_price'] * short_stop_loss:
                exit_position(exchange, symbol, position['amount'])
                post_message(myToken, slackchannel, f"숏 포지션 종료: {short_stop_loss * 100 - 100}% 손해 실현")
                return
            if dif < dea and abs(dif - dea) > threshold_dif:
                exit_conditions.append("DIF < DEA (차이 벌어짐)")
            if short_ma < long_ma and abs(short_ma - long_ma) > threshold_ma:
                exit_conditions.append("단기 이동평균 < 장기 이동평균 (차이 벌어짐)")
            if rsi <= 20:
                exit_conditions.append("RSI <= 20")
            if cur_price <= lower_band:
                exit_conditions.append("현재가 <= 볼린저 밴드 하단")

            if len(exit_conditions) >= 3:
                exit_position(exchange, symbol, position['amount'])
                post_message(myToken, slackchannel, f"숏 포지션 종료 조건 충족: {', '.join(exit_conditions)}")

# trade 함수 실행 전, 마진 모드와 레버리지를 설정합니다.
set_isolated_margin_and_leverage(binance, symbol, leverage=futureleverage)

# Trade
def trade(symbol):
    """트레이드 함수"""
    balance = get_balance()
    cur_price = get_current_price(symbol)
    long_target, short_target = cal_target(binance, symbol, timeframe=timedata)
    amount = cal_amount(balance, cur_price)

    enter_position(binance, symbol, cur_price, long_target, short_target, amount)
    
    print(f"포지션 상태: {position}\n{datetime.datetime.now()} 현재 잔고: {balance:.2f}, 암호화폐: {symbol}\n현재가: {cur_price:.7f}, 롱 타겟: {long_target:.7f}, 숏 타겟: {short_target:.7f}")

    time.sleep(1)

# 초기 자산 기록
initial_balance = get_balance()

# 종료시 슬랙 알람
def notify_exit():
    """프로그램 종료 시 슬랙으로 알림 전송"""
    final_balance = get_balance()
    profit = final_balance - initial_balance
    profit_rate = (final_balance / initial_balance - 1) * 100
    message = f"<선물거래 자동매매 종료>\n초기 자산: {initial_balance:.2f} USDT\n최종 자산: {final_balance:.2f} USDT\n총 수익: {profit:.2f} USDT\n수익률: {profit_rate:.2f} %"
    post_message(myToken, slackchannel, message)

# atexit 모듈을 사용하여 프로그램 종료 시 notify_exit 함수 호출
atexit.register(notify_exit)

# 각 코인에 대해 30초마다 trade 함수 실행
schedule.every(30).seconds.do(lambda: trade(symbol))

# 자동매매 시작 알람
post_message(myToken, slackchannel, "@@ 선물거래 자동매매 시작 @@")

while True:
    schedule.run_pending()
    time.sleep(1)
