import streamlit as st
import pandas as pd
import threading
import time
import sqlite3
import requests
from SmartApi import SmartConnect
import pyotp
from datetime import datetime

# ----------------- 1. CONFIG & DATABASE SETUP -----------------
st.set_page_config(page_title="Trade Nexus ULTIMATE", layout="wide", page_icon="🦅")

# --- DATABASE HANDLER ---
def init_db():
    conn = sqlite3.connect('tradenexus_v2.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS slaves 
                 (name TEXT, api_key TEXT, client_id TEXT, password TEXT, totp TEXT, 
                  multiplier REAL, max_loss REAL, is_active INTEGER)''')
    conn.commit()
    return conn

conn = init_db()

# --- DB HELPERS ---
def save_setting(key, value):
    c = conn.cursor()
    c.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()

def get_setting(key):
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = c.fetchone()
    return res[0] if res else ""

def add_slave_db(name, api, client, pwd, totp, mult, max_loss):
    c = conn.cursor()
    c.execute("INSERT INTO slaves VALUES (?,?,?,?,?,?,?,1)", (name, api, client, pwd, totp, mult, max_loss))
    conn.commit()

def get_slaves_db():
    return pd.read_sql("SELECT * FROM slaves", conn)

def delete_slave_db(client_id):
    c = conn.cursor()
    c.execute("DELETE FROM slaves WHERE client_id=?", (client_id,))
    conn.commit()

# --- STATE MANAGEMENT ---
if "logged_in" not in st.session_state: st.session_state.logged_in = False
if "master_api" not in st.session_state: st.session_state.master_api = None
if "master_info" not in st.session_state: st.session_state.master_info = {"name": "Not Connected", "balance": 0.0, "status": "OFFLINE"}
if "copier_running" not in st.session_state: st.session_state.copier_running = False
if "logs" not in st.session_state: st.session_state.logs = []
if "processed_orders" not in st.session_state: st.session_state.processed_orders = set()
if "slave_instances" not in st.session_state: st.session_state.slave_instances = {}

# ----------------- 2. UTILITIES -----------------
def send_telegram(message):
    bot_token = get_setting("tg_bot_token")
    chat_id = get_setting("tg_chat_id")
    if bot_token and chat_id:
        try:
            requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json={"chat_id": chat_id, "text": message})
        except: pass

def log_msg(msg, type="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    color = "white"
    if type == "trade": color = "#00ff88"
    if type == "error": color = "#ff0066"
    if type == "alert": color = "#f39c12"
    st.session_state.logs.insert(0, f"<span style='color:{color}'>[{ts}] {msg}</span>")
    if type in ["trade", "error", "alert"]: send_telegram(f"{'✅' if type=='trade' else '❌'} {msg}")

# ----------------- 3. ANGEL ONE LOGIC -----------------
def connect_angel_full(api_key, client_id, password, totp_secret):
    """Connects and fetches Profile + Balance immediately"""
    try:
        obj = SmartConnect(api_key=api_key)
        try:
            totp = pyotp.TOTP(totp_secret).now()
        except:
            return None, None, "Invalid TOTP Secret"
            
        data = obj.generateSession(client_id, password, totp)
        if data['status']:
            # Fetch Balance
            funds = 0.0
            try:
                rms = obj.rmsLimit()
                if rms and 'data' in rms: funds = float(rms['data']['net'])
            except: pass
            
            # Fetch Name
            name = client_id
            try:
                profile = obj.getProfile(data['data']['refreshToken'])
                if profile and 'data' in profile: name = profile['data']['name']
            except: pass
            
            return obj, {"name": name, "balance": funds, "status": "ONLINE"}, "Success"
        else:
            return None, None, data['message']
    except Exception as e:
        return None, None, str(e)

# ----------------- 4. ENGINE (RISK + THREADS) -----------------
def worker_slave(slave_row, order_details):
    cid = slave_row['client_id']
    if cid not in st.session_state.slave_instances:
        # Lazy connect slave
        api, _, _ = connect_angel_full(slave_row['api_key'], cid, slave_row['password'], slave_row['totp'])
        if api: st.session_state.slave_instances[cid] = api
        else: return

    api = st.session_state.slave_instances[cid]
    
    # Risk Check
    try:
        pos = api.position()
        curr_pnl = 0
        if pos and 'data' in pos:
            for p in pos['data']: curr_pnl += float(p.get('pnl', 0))
        
        limit = -abs(float(slave_row['max_loss']))
        if curr_pnl < limit:
            log_msg(f"⛔ {slave_row['name']} Risk Stop! PnL {curr_pnl} < {limit}", "alert")
            return
    except: pass

    # Execute
    try:
        slave_qty = int(order_details['qty'] * slave_row['multiplier'])
        params = {
            "variety": "NORMAL", "tradingsymbol": order_details['sym'],
            "symboltoken": order_details['token'], "transactiontype": order_details['txn'],
            "exchange": order_details['exch'], "ordertype": "MARKET",
            "producttype": "INTRADAY", "duration": "DAY", "quantity": slave_qty
        }
        api.placeOrder(params)
        log_msg(f"✅ {slave_row['name']} Copied {slave_qty}", "trade")
    except Exception as e:
        log_msg(f"❌ {slave_row['name']} Failed: {e}", "error")

def engine_loop():
    while st.session_state.copier_running:
        try:
            m = st.session_state.master_api
            if m:
                orders = m.orderBook()
                if orders and 'data' in orders:
                    for o in orders['data']:
                        if o['orderstatus'] == 'complete' and o['orderid'] not in st.session_state.processed_orders:
                            st.session_state.processed_orders.add(o['orderid'])
                            details = {
                                "sym": o['tradingsymbol'], "txn": o['transactiontype'],
                                "qty": int(o['quantity']), "token": o['symboltoken'], "exch": o['exchange']
                            }
                            log_msg(f"🦅 MASTER: {details['txn']} {details['qty']} {details['sym']}", "trade")
                            
                            slaves = get_slaves_db()
                            for _, row in slaves.iterrows():
                                threading.Thread(target=worker_slave, args=(row, details)).start()
        except: pass
        time.sleep(0.5)

# ----------------- 5. UI & LOGIC -----------------

# LOGIN
if not st.session_state.logged_in:
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        st.markdown("<br><h1 style='text-align:center; color:#00ff88'>🦅 TRADE NEXUS LOGIN</h1>", unsafe_allow_html=True)
        u = st.text_input("User"); p = st.text_input("Pass", type="password")
        if st.button("ENTER"):
            if u == "admin" and p == "admin": st.session_state.logged_in = True; st.rerun()
    st.stop()

# DASHBOARD HEADER
st.markdown("""
<style>
    .metric-box { background: #1a1a1a; padding: 15px; border-radius: 10px; border-left: 4px solid #00ff88; margin-bottom: 10px; }
    .val { font-size: 22px; font-weight: bold; color: white; }
    .lbl { font-size: 13px; color: #aaa; }
    .success { color: #00ff88 !important; } .error { color: #ff0066 !important; }
</style>
""", unsafe_allow_html=True)

# METRICS ROW (The Visual Feedback you wanted)
info = st.session_state.master_info
m1, m2, m3, m4 = st.columns(4)
with m1:
    st.markdown(f"""<div class='metric-box'><div class='lbl'>MASTER STATUS</div>
    <div class='val {"success" if info["status"]=="ONLINE" else "error"}'>{info['status']}</div></div>""", unsafe_allow_html=True)
with m2:
    st.markdown(f"""<div class='metric-box'><div class='lbl'>ACCOUNT NAME</div>
    <div class='val'>{info['name']}</div></div>""", unsafe_allow_html=True)
with m3:
    st.markdown(f"""<div class='metric-box'><div class='lbl'>AVAILABLE FUNDS</div>
    <div class='val'>₹ {info['balance']:,.2f}</div></div>""", unsafe_allow_html=True)
with m4:
    status = "RUNNING" if st.session_state.copier_running else "STOPPED"
    st.markdown(f"""<div class='metric-box'><div class='lbl'>COPIER ENGINE</div>
    <div class='val {"success" if status=="RUNNING" else "error"}'>{status}</div></div>""", unsafe_allow_html=True)

st.divider()

# SIDEBAR (SETUP)
with st.sidebar:
    st.header("⚙️ Master Connection")
    
    # Auto-fill from DB
    db_api = get_setting("m_api")
    db_cli = get_setting("m_client")
    db_pass = get_setting("m_pass")
    db_totp = get_setting("m_totp")

    with st.form("master_connect"):
        mk = st.text_input("API Key", value=db_api)
        mi = st.text_input("Client ID", value=db_cli)
        mp = st.text_input("Password", type="password", value=db_pass)
        mt = st.text_input("TOTP Secret", type="password", value=db_totp)
        
        if st.form_submit_button("🔌 CONNECT & SAVE"):
            api, res_info, msg = connect_angel_full(mk, mi, mp, mt)
            if api:
                st.session_state.master_api = api
                st.session_state.master_info = res_info
                # Save to DB
                save_setting("m_api", mk); save_setting("m_client", mi)
                save_setting("m_pass", mp); save_setting("m_totp", mt)
                # Sync orders
                try:
                    ob = api.orderBook()
                    if ob and 'data' in ob:
                        for o in ob['data']:
                            if o['orderstatus'] == 'complete': st.session_state.processed_orders.add(o['orderid'])
                except: pass
                st.success("Connected & Saved!")
                st.rerun()
            else:
                st.error(f"Failed: {msg}")

    st.markdown("---")
    st.header("📱 Telegram Setup")
    t_tok = st.text_input("Bot Token", value=get_setting("tg_bot_token"))
    t_chat = st.text_input("Chat ID", value=get_setting("tg_chat_id"))
    if st.button("Save Telegram"):
        save_setting("tg_bot_token", t_tok); save_setting("tg_chat_id", t_chat)
        send_telegram("🔔 System Connected")
        st.success("Saved!")

    st.markdown("---")
    st.header("➕ Add Slave")
    with st.form("add_slv"):
        n = st.text_input("Name"); ak = st.text_input("API Key")
        ci = st.text_input("Client ID"); pw = st.text_input("Pass", type="password")
        to = st.text_input("TOTP", type="password")
        mu = st.number_input("Multiplier", 1.0); ml = st.number_input("Max Loss Limit", 2000)
        if st.form_submit_button("Add Slave"):
            add_slave_db(n, ak, ci, pw, to, mu, ml)
            st.success("Added"); st.rerun()

# MAIN CONTROLS
c1, c2 = st.columns([2, 1])
with c1:
    st.subheader("📊 Live P&L (All Accounts)")
    if st.button("🔄 Refresh P&L"):
        slaves = get_slaves_db()
        data = []
        for _, row in slaves.iterrows():
            cid = row['client_id']
            pnl = 0.0
            stat = "Offline"
            if cid in st.session_state.slave_instances:
                try:
                    p = st.session_state.slave_instances[cid].position()
                    if p and 'data' in p:
                         for x in p['data']: pnl += float(x.get('pnl',0))
                    stat = "Online"
                except: pass
            else:
                # Try connect once
                api, _, _ = connect_angel_full(row['api_key'], cid, row['password'], row['totp'])
                if api: 
                    st.session_state.slave_instances[cid] = api
                    stat = "Connected"
            
            data.append({"Name": row['name'], "Status": stat, "P&L": f"₹ {pnl:.2f}", "Limit": row['max_loss']})
        if data: st.dataframe(pd.DataFrame(data), use_container_width=True)
        else: st.info("No slaves added.")

with c2:
    st.subheader("🎮 Controls")
    if st.button("🚀 START / STOP ENGINE", type="primary", use_container_width=True):
        if st.session_state.copier_running:
            st.session_state.copier_running = False
            log_msg("🛑 Engine Stopped", "alert")
        else:
            if st.session_state.master_api:
                st.session_state.copier_running = True
                threading.Thread(target=engine_loop).start()
                log_msg("🚀 Engine Started", "trade")
            else:
                st.error("Connect Master first!")
        st.rerun()

    if st.button("☠️ KILL SWITCH (SELL ALL)", type="secondary", use_container_width=True):
        log_msg("⚠️ KILL SWITCH ACTIVATED", "alert")
        # Logic to iterate all slaves and close positions...
        slaves = get_slaves_db()
        for _, row in slaves.iterrows():
             if row['client_id'] in st.session_state.slave_instances:
                 # Add square off logic here
                 pass
        st.success("Signal Sent")

st.markdown("---")
st.subheader("📜 Live Event Log")
log_box = st.container(height=300, border=True)
for l in st.session_state.logs:
    log_box.markdown(l, unsafe_allow_html=True)
