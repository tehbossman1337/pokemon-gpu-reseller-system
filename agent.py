#!/usr/bin/env python3
"""CLI: python agent.py sync | status | watch [minutes]"""
import sys
import time

import core
import mail_sync


def status():
    c = core.connect()
    s = core.summary(c)
    print(f"On hand: {s['units_on_hand']} units (cost ${s['inventory_cost']:,.2f})")
    print(f"Incoming: {s['incoming_units']} units across {s['open_orders']} open orders")
    print(f"Profit: ${s['profit']:,.2f} total, ${s['profit_30d']:,.2f} last 30d")
    print(f"Needs review: {s['review_count']}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "sync":
        print(mail_sync.sync())
    elif cmd == "watch":
        mins = float(sys.argv[2]) if len(sys.argv) > 2 else 10
        while True:
            try:
                print(time.strftime("%H:%M"), mail_sync.sync())
            except Exception as e:
                print("sync error:", e)
            time.sleep(mins * 60)
    else:
        status()


if __name__ == "__main__":
    main()
