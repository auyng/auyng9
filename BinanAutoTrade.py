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

symbol = "BTC/USDT"
timedata = '1h'
futureleverage = 10

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

# 초기 자산 기록
initial_balance = 0

# 익절매 및 손절매 비율
take_profit_ratio = 0.05
stop_loss_ratio = 0.03

stop_loss_ratio_immediately = 0.005

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
    exchange.load_markets()
    market = exchange.market(symbol)
    
    base_url = 'https://fapi.binance.com'
    endpoint_margin = '/fapi/v1/marginType'
    timestamp = int(time.time() * 1000)
    params_margin = f'symbol={market["id"]}&marginType=ISOLATED&timestamp={timestamp}'
    signature_margin = generate_signature(params_margin, secret)
    url_margin = f"{base_url}{endpoint_margin}?{params_margin}&signature={signature_margin}"
    
    headers = {'X-MBX-APIKEY': api_key}
    
    response_margin = requests.post(url_margin, headers=headers)
    if response_margin.status_code == 200:
        post_message(myToken, slackchannel, "## Isolated 마진 모드 설정 성공 ##")
    else:
        post_message(myToken, slackchannel, f"!! Isolated 마진 모드 설정 오류 !!\n{response_margin.json()}")
    
    endpoint_leverage = '/fapi/v1/leverage'
    params_leverage = f'symbol={market["id"]}&leverage={leverage}&timestamp={timestamp}'
    signature_leverage = generate_signature(params_leverage, secret)
    url_leverage = f"{base_url}{endpoint_leverage}?{params_leverage}&signature={signature_leverage}"
    
    response_leverage = requests.post(url_leverage, headers=headers)
    if response_leverage.status_code == 200:
        post_message(myToken, slackchannel, f"## 레버리지 설정 성공 ##\n레버리지: {leverage}배\n코인: {symbol}\n마진 모드: ISOLATED")
    else:
        post_message(myToken, slackchannel, f"!! 레버리지 설정 실패 !!\n{response_leverage.json()}")

def get_balance():
    """잔고 조회"""
    balance = binance.fetch_balance()
    return balance['total']['USDT']

def get_current_price(symbol):
    """현재가 조회"""
    ticker = binance.fetch_ticker(symbol)
    return ticker['last']

def get_rsi(exchange, symbol, timeframe=timedata, period=6):
    """RSI (Relative Strength Index) 조회"""
    coin = exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=period+1)
    df = pd.DataFrame(coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def get_macd(exchange, symbol, short_period=12, long_period=26, signal_period=9):
    """MACD (Moving Average Convergence Divergence) 조회"""
    coin = exchange.fetch_ohlcv(symbol=symbol, timeframe=timedata, limit=long_period + signal_period)
    df = pd.DataFrame(coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    short_ema = df['close'].ewm(span=short_period, adjust=False).mean()
    long_ema = df['close'].ewm(span=long_period, adjust=False).mean()
    dif = short_ema - long_ema
    dea = dif.ewm(span=signal_period, adjust=False).mean()
    return dif.iloc[-1], dea.iloc[-1]

def get_bollinger_bands(exchange, symbol, period=20, std_dev=2):
    """볼린저 밴드 계산"""
    coin = exchange.fetch_ohlcv(symbol=symbol, timeframe=timedata, limit=period + 1)
    df = pd.DataFrame(coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    df['ma'] = df['close'].rolling(window=period).mean()
    df['std'] = df['close'].rolling(window=period).std()
    df['upper'] = df['ma'] + (df['std'] * std_dev)
    df['lower'] = df['ma'] - (df['std'] * std_dev)
    return df.iloc[-1]['upper'], df.iloc[-1]['lower'], df.iloc[-1]['ma']

def get_ma(exchange, symbol, short_period=7, long_period=30):
    """이동평균선 (MA) 계산"""
    coin = exchange.fetch_ohlcv(symbol=symbol, timeframe=timedata, limit=long_period)
    df = pd.DataFrame(coin, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    short_ma = df['close'].rolling(window=short_period).mean()
    long_ma = df['close'].rolling(window=long_period).mean()
    return short_ma.iloc[-1], long_ma.iloc[-1]

def cal_amount(usdt_balance, cur_price):
    """매매할 암호화폐 양 계산"""
    portion = 1
    usdt_trade = usdt_balance * portion * futureleverage
    amount = math.floor((usdt_trade * 1000000) / cur_price) / 1000000
    min_notional = 5
    if amount * cur_price < min_notional:
        amount = min_notional / cur_price
    return amount

def record_position_to_csv(action, position_type, amount, entry_price, exit_price=None, profit=None, profit_rate=None):
    """포지션을 CSV 파일에 기록"""
    file_exists = os.path.isfile('trades.csv')
    columns = ['Datetime', 'Action', 'Position Type', 'Amount', 'Entry Price', 'Exit Price', 'Profit', 'Profit Rate']
    data = {
        'Datetime': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'Action': action,
        'Position Type': position_type,
        'Amount': amount,
        'Entry Price': entry_price,
        'Exit Price': exit_price,
        'Profit': profit,
        'Profit Rate': profit_rate
    }
    df = pd.DataFrame([data])

    if not file_exists:
        df.to_csv('trades.csv', index=False, columns=columns)
    else:
        df.to_csv('trades.csv', index=False, mode='a', header=False, columns=columns)

# 포지션 진입 및 종료 함수
def enter_long_position(exchange, symbol, amount, cur_price):
    global position
    global long_position_restriction
    try:
        order = exchange.create_market_buy_order(symbol=symbol, amount=amount)
        position['type'] = 'long'
        position['amount'] = amount
        position['entry_price'] = cur_price
        position['initial_balance'] = get_balance()
        entry_cost = amount * cur_price / futureleverage  # 진입 비용 계산
        post_message(myToken, slackchannel, f"## 롱 포지션 진입 ##\n코인: {symbol}\n진입 비용: {entry_cost:.2f} USDT\n진입 가격: {cur_price:.5f}")
        record_position_to_csv('Enter', 'long', amount, cur_price)
    except Exception as e:
        post_message(myToken, slackchannel, f"!! 롱 포지션 진입 실패 !!\n{e}")

def enter_short_position(exchange, symbol, amount, cur_price):
    global position
    global short_position_restriction
    try:
        order = exchange.create_market_sell_order(symbol=symbol, amount=amount)
        position['type'] = 'short'
        position['amount'] = amount
        position['entry_price'] = cur_price
        position['initial_balance'] = get_balance()
        entry_cost = amount * cur_price / futureleverage  # 진입 비용 계산
        post_message(myToken, slackchannel, f"## 숏 포지션 진입 ##\n코인: {symbol}\n진입 비용: {entry_cost:.2f} USDT\n진입 가격: {cur_price:.5f}")
        record_position_to_csv('Enter', 'short', amount, cur_price)
    except Exception as e:
        post_message(myToken, slackchannel, f"!! 숏 포지션 진입 실패 !!\n{e}")

def exit_position(exchange, symbol, amount):
    global position
    global long_position_restriction
    global short_position_restriction
    try:
        final_price = get_current_price(symbol)
        if position['type'] == 'long':
            order = exchange.create_market_sell_order(symbol=symbol, amount=amount)
            profit = (final_price - position['entry_price']) * amount
            profit_rate = (final_price / position['entry_price'] - 1) * 100
            post_message(myToken, slackchannel, f"## 롱 포지션 종료 ##\n수익 금액: {profit:.2f} USDT\n수익률: {profit_rate:.2f}%")
            record_position_to_csv('Exit', 'long', amount, position['entry_price'], final_price, profit, profit_rate)
            long_position_restriction = True
            short_position_restriction = False
        elif position['type'] == 'short':
            order = exchange.create_market_buy_order(symbol=symbol, amount=amount)
            profit = (position['entry_price'] - final_price) * amount
            profit_rate = (position['entry_price'] / final_price - 1) * 100
            post_message(myToken, slackchannel, f"## 숏 포지션 종료 ##\n수익 금액: {profit:.2f} USDT\n수익률: {profit_rate:.2f}%")
            record_position_to_csv('Exit', 'short', amount, position['entry_price'], final_price, profit, profit_rate)
            long_position_restriction = False
            short_position_restriction = True
        position['type'] = 'none'
        position['amount'] = 0
        position['entry_price'] = 0
        position['initial_balance'] = 0
    except Exception as e:
        post_message(myToken, slackchannel, f"!! 포지션 종료 실패 !!\n{e}")

# 통합 함수
def enter_position(exchange, symbol, cur_price, amount):
    global position
    global long_position_restriction
    global short_position_restriction
    dif, dea = get_macd(exchange, symbol)
    rsi = get_rsi(exchange, symbol)
    upper_band, lower_band, middle_band = get_bollinger_bands(exchange, symbol)
    short_ma, long_ma = get_ma(exchange, symbol)

    print(f"DEBUG: dif={dif}, dea={dea}, rsi={rsi}, upper_band={upper_band}, lower_band={lower_band}, middle_band={middle_band}, short_ma={short_ma}, long_ma={long_ma}, cur_price={cur_price}")

    if position['type'] == 'none':
        if dif > dea and not long_position_restriction and short_ma > long_ma and cur_price < middle_band:
            enter_long_position(exchange, symbol, amount, cur_price)
            post_message(myToken, slackchannel, f"롱 포지션 진입 조건 충족 (dif > dea, short_ma > long_ma, cur_price < middle_band)")
        elif rsi <= 20 and not long_position_restriction and cur_price < lower_band:
            enter_long_position(exchange, symbol, amount, cur_price)
            post_message(myToken, slackchannel, f"롱 포지션 진입 조건 충족 (rsi <= 20, cur_price < lower_band)")
        elif dif < dea and not short_position_restriction and short_ma < long_ma and cur_price > middle_band:
            enter_short_position(exchange, symbol, amount, cur_price)
            post_message(myToken, slackchannel, f"숏 포지션 진입 조건 충족 (dif < dea, short_ma < long_ma, cur_price > middle_band)")
        elif rsi >= 80 and not short_position_restriction and cur_price > upper_band:
            enter_short_position(exchange, symbol, amount, cur_price)
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
        #elif dif < dea and cur_price <= position['entry_price'] * (1 - stop_loss_ratio_immediately * futureleverage) and short_ma < long_ma:
        #    post_message(myToken, slackchannel, "## 롱 포지션 종료 및 숏 포지션 진입 ##")
        #    exit_position(exchange, symbol, position['amount'])
        #    enter_short_position(exchange, symbol, amount, cur_price)

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
        # elif dif > dea and cur_price >= position['entry_price'] * (1 + stop_loss_ratio_immediately * futureleverage) and short_ma > long_ma:
        #     post_message(myToken, slackchannel, "## 숏 포지션 종료 및 롱 포지션 진입 ##")
        #     exit_position(exchange, symbol, position['amount'])
        #     enter_long_position(exchange, symbol, amount, cur_price)

# Trade
def trade(symbol):
    balance = get_balance()
    cur_price = get_current_price(symbol)
    amount = cal_amount(balance, cur_price)
    enter_position(binance, symbol, cur_price, amount)
    print(f"포지션 상태: {position}\n{datetime.datetime.now()} 현재 잔고: {balance:.2f}, 암호화폐: {symbol}\n현재가: {cur_price:.7f}")

    time.sleep(1)

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

while True:
    schedule.run_pending()
    time.sleep(1)

