import dotenv
dotenv.load_dotenv()

import datetime, json, math, os, time
import scipy.optimize as so

from web3 import Web3
from web3.middleware import geth_poa_middleware

seconds_per_year = 60*60*24*365
chain_id = 56
my_addr = os.environ.get("METAMASK_ADDRESS")
key = os.environ.get("METAMASK_PK")
farm_abi = json.load(open("./abi/pancake_farm_abi.json"))
pair_abi = json.load(open("./abi/pancake_pair_abi.json"))
pool_abi = json.load(open("./abi/pancake_pool_abi.json"))
router_abi = json.load(open("./abi/pancake_router_abi.json"))
cake_farm_addr = "0x73feaa1eE314F8c655E354234017bE2193C9E24E"
cake_bnb_addr = "0x0eD7e52944161450477ee417DE9Cd3a859b14fD0"
factory_addr = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73" # getPair(addr1, addr2) may be useful; can auto-find routes through key tokens, viz WBNB, BUSD
router_addr = "0x10ed43c718714eb63d5aa57b78b54704e256024e"
sps_addr = "0x1633b7157e7638c4d6593436111bf125ee74703f"
# ws_rpc = "wss://bsc-ws-node.nariox.org:443"; web3 = Web3(Web3.WebsocketProvider(ws_rpc))
rpc = "https://bsc-dataseed.binance.org/"; web3 = Web3(Web3.HTTPProvider(rpc))
web3.middleware_onion.inject(geth_poa_middleware, layer=0)
my_account = web3.eth.account.from_key(key)
web3.eth.default_account = my_account.address

# P -> P*(1+rho*t)-fee; APY = (1+rho*t-fee/P)^(1/t), but we take the log for numerical reasons
def optimal_harvest_schedule(principal, rho, fee):
  return so.minimize(lambda t:-math.log(1+rho*t-fee/principal)/t, [1], bounds=so.Bounds(fee/(principal*rho), (math.e + fee/principal - 1) / rho))

# Data downloaded is your principal, pending, rho, and the CAKE/BNB price
# TODO async-parallelise this
def download_data():
  pool_contract = web3.eth.contract(address=cake_farm_addr, abi=farm_abi)
  pair_contract = web3.eth.contract(address=cake_bnb_addr, abi=pair_abi)
  now = web3.eth.get_block_number() # so that everything is in the same block
  then = now-20
  pending = pool_contract.functions.pendingCake(0, my_addr).call(block_identifier=now)
  old_pending = pool_contract.functions.pendingCake(0, my_addr).call(block_identifier=then)
  is_recently_compounded = pending < old_pending
  if is_recently_compounded:
    time.sleep(60)
    return download_data()
  principal = pool_contract.functions.userInfo(0, my_addr).call(block_identifier=now)[0]
  rho = (pending - old_pending) / principal * seconds_per_year / (web3.eth.get_block(now).timestamp - web3.eth.get_block(then).timestamp)
  price = pair_contract.functions.getReserves().call(block_identifier=now); price = price[0] / price[1]
  return {"principal": principal, "pending": pending, "rho": rho, "price": price}

def main_loop():
  print("Main loop entered")
  while True:
    dat = download_data()
    bnb_fee = 0.00078858 * 10**18
    fee = dat["price"] * bnb_fee
    pending = dat["pending"]
    principal = dat["principal"]
    rho = dat["rho"]
    print(dat, "APY:", 100*(math.exp(rho)-1))
    t_opt = optimal_harvest_schedule(principal, rho, fee)
    target = principal*(t_opt.x[0])*rho
    print(f"Should we compound? Have {float(pending) :.3e}, target {target :.3e}, verdict: {'yes' if pending > target else 'no'}")
    if pending > target:
      tx = web3.eth.contract(address=cake_farm_addr, abi=farm_abi).functions.enterStaking(pending).buildTransaction({
      'chainId': chain_id,
      'gas': 300000,
      'value': 0,
      'nonce': web3.eth.get_transaction_count(my_account.address)
      })
      signed_txn = my_account.signTransaction(tx)
      id = web3.toHex(web3.eth.sendRawTransaction(signed_txn.rawTransaction))
      print(id)
      time.sleep(120) # wait 2 minutes to prevent double-harvesting; theoretically unnecessary but probably wise in case of bounced txns, etc
    else:
      delay = seconds_per_year * (target-pending) / (principal * rho)
      check_delay = max(5, min(delay - 5, delay*0.9 + 60))
      est = datetime.datetime.now() + datetime.timedelta(seconds=delay) ; est = est.replace(microsecond=0)
      est_check = datetime.datetime.now() + datetime.timedelta(seconds=check_delay) ; est_check = est_check.replace(microsecond=0)
      print(f"Next compound estimated at {est}, checking again at {est_check}")
      time.sleep(check_delay)

if __name__ == "__main__":
  main_loop()