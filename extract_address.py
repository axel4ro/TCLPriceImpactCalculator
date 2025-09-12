#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import aiohttp
import asyncio
import base64
import json
import time
import random
from datetime import datetime

TCL_MAIN_SC = "erd1qqqqqqqqqqqqqpgqm77vv5dcqs6kuzhj540vf67f90xemypd0ufsygvnvk"
MULTIVERSX_API = "https://api.elrond.com"  # stabil pentru interogări
CONTRACT_FUNCTIONS = [
    "claimRewards", "claimLendingRewards", "claimInfinityRewards",
    "addDaysAutoClaim", "setReinvestInfinity"
]
AFTER_TIMING_OFFSET = 604800 * 32  # ultimele 32 săptămâni
BEFORE_TIMING = int(time.time())
AFTER_TIMING = BEFORE_TIMING - AFTER_TIMING_OFFSET
PAGE_SIZE = 100


# ------------------- Bech32 Helpers -------------------
def bech32_polymod(values):
    GENERATORS = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = (chk >> 25)
        chk = ((chk & 0x1ffffff) << 5) ^ v
        for i in range(5):
            chk ^= GENERATORS[i] if ((b >> i) & 1) else 0
    return chk

def bech32_hrp_expand(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]

def bech32_verify_checksum(hrp, data):
    return bech32_polymod(bech32_hrp_expand(hrp) + data) == 1

def bech32_decode(bech):
    bech = bech.lower()
    pos = bech.rfind("1")
    hrp = bech[:pos]
    data = []
    for x in bech[pos+1:]:
        d = "qpzry9x8gf2tvdw0s3jn54khce6mua7l".find(x)
        data.append(d)
    return hrp, data[:-6]

def bech32_convertbits(data, frombits, tobits, pad=True):
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    return ret

def bech32_encode(hrp, data):
    CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    combined = data + bech32_create_checksum(hrp, data)
    return hrp + "1" + "".join([CHARSET[d] for d in combined])

def bech32_create_checksum(hrp, data):
    values = bech32_hrp_expand(hrp) + data
    polymod = bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ 1
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]

def hex_to_bech32(hex_addr):
    hrp = "erd"
    hex_bytes = bytes.fromhex(hex_addr)
    five_bit_r = bech32_convertbits(list(hex_bytes), 8, 5)
    return bech32_encode(hrp, five_bit_r)

def bech32_to_hex(address):
    hrp, data = bech32_decode(address)
    decoded_bytes = bech32_convertbits(data, 5, 8, False)
    return bytes(decoded_bytes).hex()

def decode_base64_to_bech32(encoded_data):
    try:
        decoded_bytes = base64.b64decode(encoded_data)
        decoded_str = decoded_bytes.decode("utf-8")
        hex_address = decoded_str.split("@")[1]
        return hex_to_bech32(hex_address)
    except:
        return None


# ------------------- Extract Transactions -------------------
async def extract_transactions_async():
    transactions_by_function = {}
    async with aiohttp.ClientSession() as session:
        for contract_function in CONTRACT_FUNCTIONS:
            extracted_data = []
            current_from = 0
            while True:
                url = (
                    f"{MULTIVERSX_API}/transactions"
                    f"?from={current_from}&size={PAGE_SIZE}&receiver={TCL_MAIN_SC}"
                    f"&status=success&function={contract_function}"
                    f"&before={BEFORE_TIMING}&after={AFTER_TIMING}"
                )
                async with session.get(url) as response:
                    if response.status != 200:
                        break
                    data = await response.json()
                    if not data:
                        break
                    for tx in data:
                        if contract_function in ["claimRewards", "claimLendingRewards", "claimInfinityRewards"]:
                            address = decode_base64_to_bech32(tx.get("data", ""))
                            if address:
                                extracted_data.append(address)
                        else:
                            extracted_data.append(tx.get("sender"))

                    current_from += PAGE_SIZE
                    if len(data) < PAGE_SIZE:
                        break

                await asyncio.sleep(0.3)

            transactions_by_function[contract_function] = extracted_data
    return transactions_by_function


def filter_unique_addresses(data):
    return sorted({addr for addr_list in data.values() for addr in addr_list if addr})


# ------------------- Generate Leaderboard -------------------
async def generate_leaderboard_async(addresses, batch_size=50):
    results = {}
    async with aiohttp.ClientSession() as session:
        # împărțim adresele în batch-uri de câte batch_size
        for i in range(0, len(addresses), batch_size):
            batch = addresses[i:i+batch_size]
            print(f"⚡ Processing batch {i//batch_size+1} with {len(batch)} addresses...")

            for addr in batch:
                hex_arg = bech32_to_hex(addr)
                payload = {
                    "scAddress": TCL_MAIN_SC,
                    "funcName": "getRewardsData",
                    "caller": addr,
                    "value": "0",
                    "args": [hex_arg]
                }

                retries = 3
                for attempt in range(retries):
                    try:
                        async with session.post(f"{MULTIVERSX_API}/query", json=payload) as response:
                            if response.status not in (200, 201):
                                print(f"⚠️ Query failed for {addr} with status {response.status}")
                                ...
                                if response.status in (429, 500):
                                    wait = 1 + attempt * 2 + random.random()
                                    print(f"⏳ Retry {attempt+1}/{retries} after {wait:.1f}s")
                                    await asyncio.sleep(wait)
                                    continue
                                break

                            data = await response.json()
                            if "returnData" not in data or not data["returnData"]:
                                break

                            decoded = base64.b64decode(data["returnData"][0]).decode("utf-8")
                            parts = decoded.split(" ")
                            nft = int(parts[4]) if len(parts) > 4 else 0
                            loan = int(parts[5]) if len(parts) > 5 else 0
                            infinity = int(parts[17]) if len(parts) > 17 else 0
                            total = nft + loan + infinity
                            if total >= 1:
                                results[addr] = {
                                    "nft": nft,
                                    "loan": loan,
                                    "infinity": infinity,
                                    "total": total
                                }
                            break
                    except Exception as e:
                        print(f"⚠️ {addr} error: {e}")
                        await asyncio.sleep(1)

                await asyncio.sleep(0.5)  # mic delay per adresă

            print(f"✅ Finished batch {i//batch_size+1}, results so far: {len(results)} stakers")

    # sortare finală după total
    sorted_results = sorted(results.items(), key=lambda x: x[1]["total"], reverse=True)
    leaderboard = {}
    for i, (addr, data) in enumerate(sorted_results, start=1):
        leaderboard[addr] = {"rank": i, **data}

    print(f"📊 Final leaderboard size: {len(leaderboard)}")
    return leaderboard
