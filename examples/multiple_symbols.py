# ignore everything here
import sys, os
current_dir = os.path.dirname(os.path.realpath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

### !!! example starts here !!!
import time
from ticker import ticker

tick = ticker(["BINANCE:BTCUSDT", "NASDAQ:AAPL"])
tick.start()

while True:
    time.sleep(2)
    # Continuously outputs data for all tracked symbols, every 2 seconds
    print(tick.states)