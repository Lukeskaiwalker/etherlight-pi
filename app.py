import tempfile
import uuid
import socket
import platform
import os
import tempfile
import uuid
import socket
import platform
import os
import json, os, threading, time, math
from flask import Flask, jsonify, request, send_from_directory
from led_driver import LedStrip, hex_to_rgb
from snmp_poller import SnmpPoller
from udp_sync import UdpSync
from display import SmallDisplay
from app_context import AppContext
from temps import TempMonitor

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

def load_config():
    with open(CONFIG_PATH,'r') as f: return json.load(f)

def save_config(cfg):
    with open(CONFIG_PATH,'w') as f: json.dump(cfg,f,indent=2)

app = Flask(__name__, static_folder='static', static_url_path='/static')
cfg = load_config()
stop_event = threading.Event()

# Shared runtime state
runtime_lock = threading.Lock()
runtime = {
    "identify_on": False,         # global identify toggle
    "port_flash": {},             # {port: until_ts} for 3s white flash
}

# Temps
tempmon = TempMonitor(cfg); tempmon.start()

# Display (optional)
disp = SmallDisplay(cfg.get('display', {}))
if cfg.get('display',{}).get('enabled', False):
    disp.start()

# LEDs
port_count = cfg['device']['ports']['count']
leds_per_port = cfg['device'].get('leds_per_port', 2)
strip = LedStrip(port_count, leds_per_port,
    pin=cfg['led']['pin'], brightness=cfg['led']['brightness'],
    strip_type=cfg['led'].get('type','ws2812b'),
    color_order=cfg['led'].get('color_order','GRB'))
try:
    # modest 1.2s rainbow boot
    strip.rainbow_cycle(duration_sec=1.2)
except Exception:
    pass

# SNMP poller
poller = SnmpPoller(host=cfg['device']['switch_host'],
                    community=cfg['device']['snmp']['community'],
                    interval_sec=cfg['polling']['interval_sec'],
                    port_count=port_count, stop_event=stop_event)
poller.start()

# Context + sync
ctx = AppContext.init(CONFIG_PATH, poller=poller, temp_monitor=tempmon)
syncer = UdpSync(lambda: ctx.get_cfg_snapshot()); syncer.start()

def choose_link_color(speed_mbps, up, link_colors):
    if not up or not speed_mbps:
        return hex_to_rgb(link_colors.get('down','#000000'))
    buckets = ['10','100','1000','2500','10000']
    chosen = None
    for b in buckets:
        if str(speed_mbps) == b:
            chosen = b; break
    if chosen is None: chosen = '1000'
    return hex_to_rgb(link_colors.get(chosen, '#00C853'))

def choose_vlan_color(vlan, vlan_colors):
    if vlan is None: return (16,16,16)
    return hex_to_rgb(vlan_colors.get(str(vlan), '#101010'))

def _clamp(x, lo=0.0, hi=1.0):
    return hi if x>hi else lo if x<lo else x

def _pulse_factor(cfg_local, now):
    p = (cfg_local.get('pulse') or {})
    period = max(0.5, (p.get('period_ms', 2000)/1000.0))
    lo     = float(p.get('min', 0.15))
    hi     = float(p.get('max', 1.0))
    shape  = (p.get('shape','sine') or 'sine').lower()
    t = (now % period) / period  # 0..1
    if shape == 'triangle':
        f = 1.0 - abs(2.0*t - 1.0)  # 0→1→0
    else:  # sine
        f = 0.5*(1.0 + math.sin(2*math.pi*t - math.pi/2))  # 0..1 starting low
    f = lo + (hi - lo) * _clamp(f,0,1)
    return _clamp(f,0,1)

def _scale_rgb(rgb, f):
    r,g,b = rgb
    return (int(r*f), int(g*f), int(b*f))

def render_loop():
    while not stop_event.is_set():
        state = poller.get_state()
        cfg_local = ctx.get_cfg_snapshot()

        # If identify is ON -> pulse all LEDs white, ignore normal rendering
        with runtime_lock:
            ident = runtime["identify_on"]
            port_flash = dict(runtime["port_flash"])

        now = time.time()
        if ident:
            f = _pulse_factor(cfg_local, now)
            white = (255,255,255)
            rgb = _scale_rgb(white, f)
            for k in range(strip.total):
                strip._set_rgb(k, rgb)
            strip.show()
            time.sleep(0.04)
            continue

        # Normal render: VLAN (slot0) solid, Link (slot1) pulses instead of blinks
        vlan_colors = cfg_local.get('vlan_colors', {})
        link_colors = cfg_local.get('link_colors', {})
        port_count  = cfg_local['device']['ports']['count']
        leds_pp     = cfg_local['device'].get('leds_per_port', 2)

        # Pulse factor for link LEDs
        pf = _pulse_factor(cfg_local, now)

        for port in range(1, port_count+1):
            s = state.get(port, {})
            vlan = s.get('vlan'); up = s.get('up', False); speed = s.get('speed')

            # Port-level 3s flash override?
            flash_until = port_flash.get(port, 0.0)
            if flash_until and now < flash_until:
                # Solid bright white on both LEDs for this port
                strip.set_port_led(port, 0, (255,255,255))
                if leds_pp >= 2: strip.set_port_led(port, 1, (255,255,255))
                continue
            elif flash_until and now >= flash_until:
                # cleanup expired
                with runtime_lock:
                    runtime["port_flash"].pop(port, None)

            # VLAN LED (slot 0)
            vlan_rgb = choose_vlan_color(vlan, vlan_colors)
            strip.set_port_led(port, 0, vlan_rgb)

            # Link LED (slot 1) = pulse color (never fully off)
            if leds_pp >= 2:
                base_rgb = choose_link_color(speed, up, link_colors)
                strip.set_port_led(port, 1, _scale_rgb(base_rgb, pf))

        strip.show()
        time.sleep(0.04)

renderer = threading.Thread(target=render_loop, daemon=True); renderer.start()

# -------------------- Routes --------------------

@app.route('/')
def index(): return send_from_directory('static', 'index.html')

@app.route('/setup')
def setup_page(): return send_from_directory('static', 'setup.html')

@app.get('/api/version')
def api_version():
    ver = 'dev'
    try:
        vpath = os.path.join(os.path.dirname(__file__), 'VERSION')
        if os.path.exists(vpath):
            with open(vpath,'r') as f: ver = f.read().strip()
    except Exception:
        pass
    return {'version': ver}

@app.post('/api/reload')
def api_reload():
    import subprocess
    threading.Thread(target=lambda: subprocess.call(['sudo','systemctl','restart','etherlight.service'] ),
                     daemon=True).start()
    return {'ok': True}

@app.route('/api/config', methods=['GET','POST'])
def api_config():
    if request.method == 'GET': return jsonify(ctx.get_cfg_snapshot())
    data = request.get_json(force=True)
    save_config(data); ctx.load_cfg()
    return jsonify({'ok': True})

@app.get('/api/state')
def api_state(): return jsonify(poller.get_state())

@app.get('/api/temps')
def api_temps(): return jsonify(tempmon.get_snapshot())

@app.post('/api/test/set')
def api_test_set():
    data = request.get_json(force=True)
    port = int(data.get('port',1)); slot=int(data.get('slot',0)); color=data.get('color','#FFFFFF')
    strip.set_port_led(port, slot, hex_to_rgb(color)); strip.show()
    return jsonify({'ok': True})

# Identify toggle
@app.route('/api/identify', methods=['GET','POST'])
def api_identify():
    if request.method == 'GET':
        with runtime_lock: return jsonify({'on': runtime['identify_on']})
    # POST: toggle or set explicit state
    try: body = request.get_json(force=True)
    except Exception: body = {}
    with runtime_lock:
        if 'on' in (body or {}):
            runtime['identify_on'] = bool(body['on'])
        else:
            runtime['identify_on'] = not runtime['identify_on']
    return jsonify({'ok': True, 'on': runtime['identify_on']})

# Per-port 3s white flash
@app.post('/api/port_blink')
def api_port_blink():
    data = request.get_json(force=True)
    port = int(data.get('port', 1))
    seconds = float(data.get('seconds', 3.0))
    until = time.time() + max(0.2, seconds)
    with runtime_lock:
        runtime['port_flash'][port] = until
    return jsonify({'ok': True, 'port': port, 'until': until})

# Auto-detect switch
# --- Detect switch -----------------------------------------------------------
@app.post('/api/detect_switch')
def api_detect_switch():
    try:
        import asyncio
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        res = loop.run_until_complete(poller.detect_switch())
        loop.close()

        model = guessed = sysname = None
        if isinstance(res, (list, tuple)):
            if len(res) == 3:
                model, guessed, sysname = res
            elif len(res) == 2:
                model, guessed = res
            elif len(res) == 1:
                (model,) = res
        else:
            model = res

        local = ctx.get_cfg_snapshot()
        dev = local.setdefault('device', {})
        dev.setdefault('ports', {})
        if guessed:
            dev['ports']['count'] = int(guessed)
        if model is not None:
            dev['model_hint'] = str(model)
        if sysname:
            dev['switch_name'] = str(sysname)

        save_config(local); ctx.load_cfg()
        return jsonify({'ok': True,
                        'model': dev.get('model_hint',''),
                        'ports': dev.get('ports',{}).get('count'),
                        'switch_name': dev.get('switch_name','')})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# Optional: network config endpoint (DHCP/static) — if your setup page uses it
@app.post('/api/network')
def api_network():
    import subprocess, re as _re, shutil
    data = request.get_json(force=True) if request.data else {}
    mode = (data.get('mode') or 'dhcp').lower()
    iface = 'eth0'
    lines = []
    lines.append('# EtherPi managed block')
    lines.append(f'interface {iface}')
    if mode == 'dhcp':
        # Clear static config
        block = '\n'.join(lines) + '\n'
    else:
        ip_cidr = (data.get('ip_cidr') or '').strip()
        gw = (data.get('gateway') or '').strip()
        dns = [d.strip() for d in (data.get('dns','').split(',') if data.get('dns') else []) if d.strip()]
        if not ip_cidr or not gw:
            return ('missing ip/gateway', 400)
        lines.append(f'static ip_address={ip_cidr}')
        lines.append(f'static routers={gw}')
        if dns: lines.append('static domain_name_servers=' + ' '.join(dns))
        block = '\n'.join(lines) + '\n'
    target = '/etc/dhcpcd.conf'
    try: shutil.copy2(target, target + '.bak')
    except Exception: pass
    try:
        with open(target,'r') as f: cur = f.read()
    except Exception:
        cur = ''
    cur = _re.sub(r'# EtherPi managed block[\s\S]*?(?=\n#|\Z)','',cur, flags=_re.M)
    new = cur.rstrip() + '\n\n' + block
    with open(target,'w') as f: f.write(new)
    try: subprocess.run(['sudo','systemctl','restart','dhcpcd'], check=False)
    except Exception: pass
    return {'ok': True, 'mode': mode}


# --- System info helpers + route ---
def _read_os_release_pretty():
    try:
        with open('/etc/os-release','r') as f:
            for line in f:
                if line.startswith('PRETTY_NAME='):
                    v = line.split('=',1)[1].strip().strip('"')
                    return v
    except Exception:
        pass
    return platform.platform()

def _read_pi_model():
    try:
        # Device-tree model (Pi-specific)
        p = '/proc/device-tree/model'
        if os.path.exists(p):
            return open(p,'rb').read().decode('utf-8','ignore').strip('\x00').strip()
    except Exception:
        pass
    return platform.machine()

def _primary_ip():
    ip = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    return ip or '127.0.0.1'

def _mac_addr():
    n = uuid.getnode()
    mac = ':'.join(f"{(n>>ele) & 0xff:02x}" for ele in range(40,-1,-8))
    return mac

@app.route('/api/system')
def api_system():
    # Load config to pull the device name if set
    cfg_path = os.path.expanduser('~/etherlight-pi/config.json')
    name = None
    try:
        with open(cfg_path,'r') as f:
            cfg = json.load(f)
            name = (cfg.get('device') or {}).get('name')
    except Exception:
        pass

    return jsonify({
        "version": (getattr(globals(), 'BUILD_VERSION', None) or "dev"),
        "pi_model": _read_pi_model(),
        "os_pretty": _read_os_release_pretty(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "hostname": socket.gethostname(),
        "ip": _primary_ip(),
        "mac": _mac_addr(),
        "name": name
    })

# --- injected utility endpoints ---
from flask import jsonify, request
import platform, socket, uuid, os, json

# --- end injected ---


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)


# --- Robust system info endpoint (alt path) ---
def _sysinfo_payload():
    import os, socket, platform, uuid, json
    def _os_pretty():
        try:
            with open('/etc/os-release','r') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        return line.split('=',1)[1].strip().strip('"')
        except Exception:
            pass
        return platform.platform()
    def _pi_model():
        try:
            p = '/proc/device-tree/model'
            if os.path.exists(p):
                return open(p,'rb').read().decode('utf-8','ignore').strip('\x00').strip()
        except Exception:
            pass
        return platform.machine()
    def _primary_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'
    def _mac():
        n = uuid.getnode()
        return ':'.join(f"{(n>>b)&0xff:02x}" for b in range(40,-1,-8))
    # read friendly name from config if present
    name = None
    try:
        cfgp = os.path.expanduser('~/etherlight-pi/config.json')
        with open(cfgp,'r') as f:
            cfg = json.load(f)
        name = (cfg.get('device') or {}).get('name')
    except Exception:
        pass
    return {
        "version": globals().get("BUILD_VERSION","dev"),
        "pi_model": _pi_model(),
        "os_pretty": _os_pretty(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "hostname": socket.gethostname(),
        "ip": _primary_ip(),
        "mac": _mac(),
        "name": name
    }

try:
    from flask import Response as _FResponse, jsonify, request
except Exception:
    _FResponse = None

def _json_response(data):
    import json
    if _FResponse:
        return _FResponse(json.dumps(data), mimetype='application/json')
    return (json.dumps(data), 200, {'Content-Type':'application/json'})


# keep /api/system working too, overriding any earlier text handlers if needed
try:
    @app.route('/api/system')
    def api_system():
        return _json_response(_sysinfo_payload())
except Exception:
    pass


# --- Clean JSON system info ---
@app.route('/api/sysinfo')
def api_sysinfo():
    import os, platform, socket, uuid, json
    def os_pretty():
        try:
            with open('/etc/os-release','r') as f:
                for line in f:
                    if line.startswith('PRETTY_NAME='):
                        return line.split('=',1)[1].strip().strip('"')
        except Exception:
            pass
        return platform.platform()

    def pi_model():
        try:
            p = '/proc/device-tree/model'
            if os.path.exists(p):
                return open(p,'rb').read().decode('utf-8','ignore').strip('\x00').strip()
        except Exception:
            pass
        return platform.machine()

    def ip_addr():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8',80))
            ip = s.getsockname()[0]; s.close()
            return ip
        except Exception:
            return '127.0.0.1'

    def mac_addr():
        n = uuid.getnode()
        return ':'.join(f"{(n>>b)&0xff:02x}" for b in range(40,-1,-8))

    name = None
    try:
        with open(os.path.expanduser('~/etherlight-pi/config.json'),'r') as f:
            cfg = json.load(f)
        name = (cfg.get('device') or {}).get('name')
    except Exception:
        pass

    info = {
        "version": globals().get("BUILD_VERSION","dev"),
        "pi_model": pi_model(),
        "os_pretty": os_pretty(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "hostname": socket.gethostname(),
        "ip": ip_addr(),
        "mac": mac_addr(),
        "name": name
    }
    return jsonify(info)

@app.route('/api/reload', methods=['POST'])
def api_reload():
    try:
        os.system('sudo systemctl restart etherlight.service >/dev/null 2>&1 &')
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/poe')
def api_poe():
    # No standard MIB exposed by your USW-24-PoE; stub for UI
    return jsonify({"supported": False})
